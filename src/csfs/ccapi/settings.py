"""Reading and writing camera settings.

Settings in CCAPI are reported as ``{"value": ..., "ability": [...]}``, where
``ability`` is what this particular body in its current mode will accept. Always
choose from ``ability`` rather than assuming a value is settable: the same
setting accepts different values on different bodies, and in different exposure
modes on the same body.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from csfs.ccapi.client import CCAPIClient
from csfs.ccapi.discovery import Capabilities
from csfs.ccapi.errors import CCAPIError, UnsupportedEndpointError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Setting:
    """One camera setting and the values it will currently accept."""

    name: str
    value: str
    ability: tuple[str, ...] = ()

    def can_set(self, value: str) -> bool:
        """Whether ``value`` is currently selectable.

        An empty ability list means the camera did not say, so we do not
        pre-emptively refuse.
        """
        return not self.ability or value in self.ability


def read_setting(client: CCAPIClient, caps: Capabilities, capability: str) -> Setting | None:
    """Read one setting, or None if this camera does not expose it.

    Returns None rather than raising, because "this body has no such control"
    is a normal situation the UI should handle by hiding the control.
    """
    try:
        path = caps.path(capability)
    except UnsupportedEndpointError:
        return None
    try:
        payload = client.get_json(path)
    except CCAPIError as exc:
        logger.warning("Could not read setting '%s': %s", capability, exc)
        return None
    return _to_setting(capability, payload)


def _to_setting(name: str, payload: Any) -> Setting | None:
    if not isinstance(payload, dict) or "value" not in payload:
        return None
    ability = payload.get("ability")
    values = tuple(str(v) for v in ability) if isinstance(ability, list) else ()
    return Setting(name=name, value=str(payload["value"]), ability=values)


def write_setting(
    client: CCAPIClient, caps: Capabilities, capability: str, value: str
) -> bool:
    """Set one setting. Returns True on success.

    Returns False instead of raising when the camera lacks the setting or
    rejects the value, since neither is fatal to a scanning session -- the
    caller decides whether to warn or carry on.
    """
    try:
        path = caps.path(capability)
    except UnsupportedEndpointError:
        logger.info("Camera has no '%s' setting; leaving it alone.", capability)
        return False
    try:
        client.put(path, json={"value": value})
    except CCAPIError as exc:
        logger.warning("Could not set '%s' to '%s': %s", capability, value, exc)
        return False
    return True


def choose_supported(preference: tuple[str, ...], setting: Setting | None) -> str | None:
    """Pick the first preferred value this camera actually offers.

    Args:
        preference: Candidate values, best first.
        setting: The setting as read from the camera, or None.

    Returns:
        The best available value, or None if none of them are offered.
    """
    if setting is None:
        return None
    if not setting.ability:
        # The camera did not report what it accepts. Take the top preference and
        # let the write fail loudly rather than silently doing nothing.
        return preference[0] if preference else None
    for candidate in preference:
        if candidate in setting.ability:
            return candidate
    return None


# CCAPI's own name for each capability, as it appears in the combined
# shooting-settings response.
_BULK_KEYS: dict[str, str] = {
    "iso": "iso",
    "aperture": "av",
    "shutter_speed": "tv",
    "white_balance": "wb",
    "shutter_mode": "shuttermode",
    "af_operation": "afoperation",
    "drive_mode": "drive",
    "exposure": "exposure",
    "af_method": "afmethod",
}


def read_all(
    client: CCAPIClient, caps: Capabilities, capabilities: tuple[str, ...]
) -> dict[str, Setting]:
    """Read several settings, skipping any this camera does not expose.

    Uses the combined shooting-settings endpoint when the camera offers it,
    which collapses one request per setting into a single request. That matters
    more than it looks: this runs on every UI poll, and firing seven requests at
    a camera that is simultaneously streaming live view is enough to make it
    stop responding.

    Falls back to reading settings individually if the combined endpoint is
    missing or does not contain what we need.
    """
    out = _read_all_bulk(client, caps, capabilities)

    # Fill in anything the combined response did not cover.
    for capability in capabilities:
        if capability in out:
            continue
        setting = read_setting(client, caps, capability)
        if setting is not None:
            out[capability] = setting
    return out


def _read_all_bulk(
    client: CCAPIClient, caps: Capabilities, capabilities: tuple[str, ...]
) -> dict[str, Setting]:
    """Read what we can from the combined shooting-settings endpoint."""
    if not caps.has("shooting_settings"):
        return {}
    try:
        payload = client.get_json(caps.path("shooting_settings"))
    except CCAPIError as exc:
        logger.debug("Combined settings read failed, falling back: %s", exc)
        return {}
    if not isinstance(payload, dict):
        return {}

    out: dict[str, Setting] = {}
    for capability in capabilities:
        key = _BULK_KEYS.get(capability)
        if key is None:
            continue
        entry = payload.get(key)
        setting = _to_setting(capability, entry)
        if setting is not None:
            out[capability] = setting
    return out


def disable_auto_power_off(client: CCAPIClient, caps: Capabilities) -> bool:
    """Stop the camera sleeping mid-roll.

    Worth doing at the start of a session: a camera that has gone to sleep is
    indistinguishable from a wiring fault at the far end of a USB cable, and
    scanning a roll involves long idle stretches between frames.
    """
    setting = read_setting(client, caps, "auto_power_off")
    value = choose_supported(("disable",), setting)
    if value is None:
        logger.info("This camera does not offer a 'disable' auto-power-off option.")
        return False
    return write_setting(client, caps, "auto_power_off", value)
