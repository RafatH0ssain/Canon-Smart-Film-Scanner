"""Driving focus from the computer.

CCAPI moves focus in six fixed increments -- three sizes in each direction --
rather than by a step count.

Focus drive does not operate when the camera is in manual focus, which is why
this project computes its own focus peaking: the camera's peaking only shows in
manual focus, so on the camera alone the two are mutually exclusive.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from csfs.ccapi.client import CCAPIClient
from csfs.ccapi.discovery import Capabilities
from csfs.ccapi.errors import CCAPIError, UnsupportedEndpointError
from csfs.ccapi.settings import choose_supported, read_setting, write_setting

logger = logging.getLogger(__name__)

STEP_SIZES: dict[str, str] = {"fine": "1", "medium": "2", "coarse": "3"}
DIRECTIONS = ("near", "far")


def focus_value(direction: str, size: str = "fine") -> str:
    """Build the CCAPI drive value, e.g. ``("near", "coarse") -> "near3"``.

    Raises:
        ValueError: On an unknown direction or size.
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got {direction!r}")
    if size not in STEP_SIZES:
        raise ValueError(f"size must be one of {tuple(STEP_SIZES)}, got {size!r}")
    return f"{direction}{STEP_SIZES[size]}"


@dataclass
class LensInfo:
    """What the camera reports about the mounted lens."""

    mounted: bool = False
    name: str = ""

    @property
    def description(self) -> str:
        if not self.mounted:
            return "No lens detected"
        return self.name or "Unknown lens"


def read_lens(client: CCAPIClient, caps: Capabilities) -> LensInfo:
    """Read lens information, tolerating bodies that do not report it.

    Says nothing about whether the lens can autofocus -- CCAPI does not expose
    that -- only whether something is mounted.
    """
    if not caps.has("lens"):
        return LensInfo(mounted=True, name="")
    try:
        payload = client.get_json(caps.path("lens"))
    except CCAPIError as exc:
        logger.warning("Could not read lens information: %s", exc)
        return LensInfo(mounted=True, name="")
    return LensInfo(mounted=bool(payload.get("mount", False)), name=str(payload.get("name", "")))


class FocusController:
    """Drives focus, and knows when it cannot.

    Availability starts from what the camera reports and turns pessimistic the
    first time a drive is refused. There is no way to ask whether focus drive
    would work without moving focus, and probing someone's carefully set focus
    would be worse than finding out on the first press.
    """

    def __init__(self, client: CCAPIClient, capabilities: Capabilities) -> None:
        self._client = client
        self._caps = capabilities
        self.lens = read_lens(client, capabilities)
        self.last_error: str | None = None
        self._refused = False

    @property
    def available(self) -> bool:
        """Whether focus can currently be driven from the app."""
        return self._caps.has("focus_drive") and self.lens.mounted and not self._refused

    def unavailable_reason(self) -> str | None:
        """A short explanation for the UI, or None when focus drive works."""
        if not self._caps.has("focus_drive"):
            return "This camera does not support focus drive over CCAPI."
        if not self.lens.mounted:
            return "No lens detected."
        if self._refused:
            return (
                self.last_error
                or "The camera refused focus drive. Set the lens switch to AF -- "
                "focus drive does not operate when focus is manual."
            )
        return None

    def drive(self, direction: str, size: str = "fine") -> bool:
        """Move focus one increment. Returns True if the camera accepted it.

        Args:
            direction: ``"near"`` or ``"far"``.
            size: ``"fine"``, ``"medium"`` or ``"coarse"``.

        Raises:
            ValueError: On an invalid direction or size.
        """
        value = focus_value(direction, size)
        try:
            path = self._caps.path("focus_drive")
        except UnsupportedEndpointError:
            self._refused = True
            self.last_error = "This camera does not support focus drive over CCAPI."
            return False

        try:
            self._client.post(path, json={"value": value})
        except CCAPIError as exc:
            self._refused = True
            self.last_error = (
                "Focus drive was refused. The usual cause is the lens or camera "
                f"being set to manual focus. ({exc})"
            )
            logger.warning("Focus drive '%s' failed: %s", value, exc)
            return False

        self._refused = False
        self.last_error = None
        return True

    def recheck(self) -> None:
        """Re-read lens state and clear a recorded refusal."""
        self.lens = read_lens(self._client, self._caps)
        self._refused = False
        self.last_error = None


def prepare_for_scanning(client: CCAPIClient, caps: Capabilities) -> dict[str, str]:
    """Put drive mode and AF operation into the state a copy stand wants.

    Continuous drive turns one remote release into a burst, and servo AF keeps
    refocusing so frames of a roll are no longer captured at the same distance.
    Best effort; a body that will not accept them is still usable.

    Returns the settings actually changed, so the UI can say what it touched.
    """
    applied: dict[str, str] = {}

    for capability, preference in (
        ("drive_mode", ("single",)),
        ("af_operation", ("oneshot",)),
    ):
        setting = read_setting(client, caps, capability)
        if setting is None or setting.value in preference:
            continue
        choice = choose_supported(preference, setting)
        if choice is None:
            logger.info(
                "Camera does not offer %s=%s (has: %s).",
                capability, preference[0], ", ".join(setting.ability) or "unknown",
            )
            continue
        if write_setting(client, caps, capability, choice):
            applied[capability] = choice
            logger.info("Set %s to '%s' for scanning.", capability, choice)
    return applied
