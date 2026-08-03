"""Firing the shutter without shaking the rig.

This is the module the whole project exists for. On a copy stand, the dominant
cause of soft scans is vibration during the exposure, and it comes from two
places:

1. **Touching the camera.** Solved by firing over the API instead.
2. **The camera's own mechanical shutter.** Solved by selecting the fully
   electronic shutter, which has no moving parts.

A third, subtler source is residual vibration in the stand itself from whatever
you did just before -- adjusting focus, nudging the negative, or simply walking
across a wooden floor. That is what the settle delay is for.
"""

from __future__ import annotations

import logging
import time

from csfs.ccapi.client import CCAPIClient
from csfs.ccapi.discovery import Capabilities
from csfs.ccapi.endpoints import SHUTTER_MODE_PREFERENCE
from csfs.ccapi.settings import choose_supported, read_setting, write_setting

logger = logging.getLogger(__name__)


def select_quietest_shutter(client: CCAPIClient, caps: Capabilities) -> str | None:
    """Select the shutter mode with the least mechanical movement available.

    Preference order is fully electronic, then electronic first curtain, then
    mechanical. Electronic first curtain still closes mechanically, but the
    shock that matters is the one at the *start* of the exposure, so it is a
    genuine improvement over fully mechanical when nothing better is on offer.

    Returns:
        The mode selected, or None if this body does not expose the setting.
        Not exposing it is not an error -- some bodies are electronic-only, and
        others simply do not publish the control.
    """
    setting = read_setting(client, caps, "shutter_mode")
    if setting is None:
        logger.info(
            "Camera does not expose a shutter-mode setting; using whatever it is set to."
        )
        return None

    choice = choose_supported(SHUTTER_MODE_PREFERENCE, setting)
    if choice is None:
        logger.warning(
            "None of the preferred shutter modes are available (camera offers: %s).",
            ", ".join(setting.ability) or "unknown",
        )
        return None

    if choice == setting.value:
        logger.info("Shutter mode already '%s'.", choice)
        return choice

    if write_setting(client, caps, "shutter_mode", choice):
        logger.info("Shutter mode set to '%s'.", choice)
        return choice
    return None


def release(client: CCAPIClient, caps: Capabilities, af: bool = False) -> None:
    """Fire the shutter once.

    Args:
        client: Connected CCAPI client.
        caps: Discovered capabilities.
        af: Whether to autofocus before firing. Defaults to False, and should
            stay False for film scanning: autofocus hunts on film grain, and
            refocusing between frames of a roll destroys the consistency that
            makes batch inversion predictable. Focus once, then leave it.
    """
    client.post(caps.path("shutter"), json={"af": af})


def capture(
    client: CCAPIClient,
    caps: Capabilities,
    settle_delay_s: float = 1.5,
    af: bool = False,
) -> None:
    """Wait for the rig to settle, then fire the shutter.

    The delay happens *before* the exposure, which is the whole point: it lets
    vibration from whatever you last touched damp out. Firing immediately and
    sleeping afterwards would look identical in code and be useless in practice.

    Args:
        settle_delay_s: Seconds to wait before firing. Values below roughly one
            second are unlikely to help on a typical copy stand.
    """
    if settle_delay_s > 0:
        logger.info("Settling for %.2fs before exposure.", settle_delay_s)
        time.sleep(settle_delay_s)
    release(client, caps, af=af)
