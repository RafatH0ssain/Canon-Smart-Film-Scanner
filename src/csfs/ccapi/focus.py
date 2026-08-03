"""Driving focus from the computer.

CCAPI moves focus in six fixed increments -- three sizes in each direction --
rather than by an arbitrary step count. They map naturally onto the coarse/fine
controls a focusing workflow wants.

Two constraints shape this module, both from Canon's reference:

  - **Focus drive does not operate when AF operation is manual.** So the lens
    must be in AF for the app to move focus at all. That is precisely why this
    project computes focus peaking on the PC instead of relying on the camera's
    own peaking display, which only appears in manual focus. You cannot have
    the camera's peaking and remote focus drive at the same time; you can have
    ours and remote focus drive.
  - **It is unsupported on cameras with built-in lenses**, and does nothing
    useful with a fully manual lens. Detection is therefore capability-based
    with a runtime fallback: if the camera refuses a drive, the controller
    records that and the UI hides the buttons rather than offering controls
    that quietly do nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from csfs.ccapi.client import CCAPIClient
from csfs.ccapi.discovery import Capabilities
from csfs.ccapi.errors import CCAPIError, UnsupportedEndpointError
from csfs.ccapi.settings import read_setting

logger = logging.getLogger(__name__)

# Drive amounts CCAPI accepts, smallest first in each direction.
# "near" pulls focus towards the camera, "far" pushes it away.
STEP_SIZES: dict[str, str] = {"fine": "1", "medium": "2", "coarse": "3"}
DIRECTIONS = ("near", "far")


def focus_value(direction: str, size: str = "fine") -> str:
    """Build the CCAPI drive value, e.g. ``("near", "coarse") -> "near3"``.

    Raises:
        ValueError: On an unknown direction or size. These come from the UI, so
            failing here beats sending the camera something it will reject.
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

    Note this says nothing about whether the lens can autofocus -- CCAPI does
    not expose that. It only tells us whether something is mounted.
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

    Availability starts optimistic -- based on what the camera reports -- and
    turns pessimistic the first time a drive is actually refused. That ordering
    matters: there is no way to ask a camera "would focus drive work?" without
    moving focus, and moving the user's focus just to probe would be rude on a
    rig they have carefully set up.
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
        return (
            self._caps.has("focus_drive")
            and self.lens.mounted
            and not self._refused
        )

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
            # The overwhelmingly common cause is the lens being in manual focus,
            # so say that rather than echoing a status code.
            self._refused = True
            self.last_error = (
                f"Focus drive was refused. The usual cause is the lens or camera "
                f"being set to manual focus -- CCAPI cannot drive focus then. "
                f"({exc})"
            )
            logger.warning("Focus drive '%s' failed: %s", value, exc)
            return False

        # A previously refused controller that now succeeds is working again,
        # e.g. the user flicked the lens switch back to AF.
        self._refused = False
        self.last_error = None
        logger.debug("Focus driven: %s", value)
        return True

    def recheck(self) -> None:
        """Re-read lens state and clear a recorded refusal.

        Called when the user asks the UI to look again, typically after moving
        the AF/MF switch.
        """
        self.lens = read_lens(self._client, self._caps)
        self._refused = False
        self.last_error = None


def prepare_for_scanning(client: CCAPIClient, caps: Capabilities) -> dict[str, str]:
    """Put focus and drive settings into the state a copy stand wants.

    Two settings cause real problems if left as they commonly are:

      - **Drive mode.** A body left on continuous will fire a burst from a
        single remote release, filling the card with near-identical frames.
      - **AF operation.** Servo AF keeps refocusing, so frames of one roll are
        no longer captured at an identical focus distance -- which undoes the
        consistency that makes batch inversion predictable.

    Both are best-effort: a body that will not accept them is still usable.

    Returns:
        A mapping of setting name to the value actually applied, for logging
        and for showing the user what was changed on their camera.
    """
    applied: dict[str, str] = {}
    from csfs.ccapi.settings import choose_supported, write_setting

    for capability, preference in (
        ("drive_mode", ("single",)),
        ("af_operation", ("oneshot",)),
    ):
        setting = read_setting(client, caps, capability)
        if setting is None:
            continue
        if setting.value in preference:
            continue  # already correct; do not write for the sake of it
        choice = choose_supported(preference, setting)
        if choice is None:
            logger.info(
                "Camera does not offer %s=%s (has: %s); leaving it alone.",
                capability, preference[0], ", ".join(setting.ability) or "unknown",
            )
            continue
        if write_setting(client, caps, capability, choice):
            applied[capability] = choice
            logger.info("Set %s to '%s' for scanning.", capability, choice)
    return applied
