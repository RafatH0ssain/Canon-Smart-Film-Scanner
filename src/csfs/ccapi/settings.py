"""Reading and writing camera settings.

Settings are reported as ``{"value": ..., "ability": [...]}``, where ability is
what this body will accept in its current mode. Always choose from ability
rather than assuming a value is settable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from csfs.ccapi.client import CCAPIClient
from csfs.ccapi.discovery import Capabilities
from csfs.ccapi.errors import CCAPIError, UnsupportedEndpointError

logger = logging.getLogger(__name__)

# CCAPI's own name for each capability in the combined settings response.
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


@dataclass(frozen=True)
class Setting:
    """One camera setting and the values it will currently accept."""

    name: str
    value: str
    ability: tuple[str, ...] = ()

    def can_set(self, value: str) -> bool:
        """Whether ``value`` is currently selectable.

        An empty ability list means the camera did not say, so we do not refuse.
        """
        return not self.ability or value in self.ability


def read_setting(client: CCAPIClient, caps: Capabilities, capability: str) -> Setting | None:
    """Read one setting, or None if this camera does not expose it.

    None rather than an exception: "this body has no such control" is normal,
    and the UI handles it by hiding the control.
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

    False rather than an exception when the camera lacks the setting or rejects
    the value; neither is fatal to a scanning session.
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
    """Pick the first preferred value this camera actually offers."""
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


def read_all(
    client: CCAPIClient, caps: Capabilities, capabilities: tuple[str, ...]
) -> dict[str, Setting]:
    """Read several settings, skipping any this camera does not expose.

    Uses the combined shooting-settings endpoint where available, which turns
    one request per setting into one request total. That matters because this
    runs on every UI poll, against a camera already busy streaming live view.
    """
    out = _read_all_bulk(client, caps, capabilities)

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
        setting = _to_setting(capability, payload.get(key))
        if setting is not None:
            out[capability] = setting
    return out


def disable_auto_power_off(client: CCAPIClient, caps: Capabilities) -> bool:
    """Stop the camera sleeping mid-roll.

    A sleeping camera is indistinguishable from a network fault, and scanning
    involves long idle stretches between frames.
    """
    setting = read_setting(client, caps, "auto_power_off")
    value = choose_supported(("disable",), setting)
    if value is None:
        logger.info("This camera does not offer a 'disable' auto-power-off option.")
        return False
    return write_setting(client, caps, "auto_power_off", value)
