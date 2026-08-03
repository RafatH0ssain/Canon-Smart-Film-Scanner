"""Firing the shutter without shaking the rig.

On a copy stand the dominant cause of soft scans is vibration during the
exposure: touching the camera (solved by firing over the API), the mechanical
shutter (solved by selecting the electronic one), and residual vibration in the
stand from whatever you did just before (solved by the settle delay).
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

    Returns the mode selected, or None if the body does not expose the setting.
    Not exposing it is not an error -- some bodies are electronic-only.
    """
    setting = read_setting(client, caps, "shutter_mode")
    if setting is None:
        logger.info("Camera does not expose a shutter-mode setting.")
        return None

    choice = choose_supported(SHUTTER_MODE_PREFERENCE, setting)
    if choice is None:
        logger.warning(
            "No preferred shutter mode available (camera offers: %s).",
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

    ``af`` defaults to False and should stay that way for scanning: autofocus
    hunts on film grain, and refocusing between frames destroys the consistency
    that makes batch inversion predictable.
    """
    client.post(caps.path("shutter"), json={"af": af})


def capture(
    client: CCAPIClient,
    caps: Capabilities,
    settle_delay_s: float = 1.5,
    af: bool = False,
) -> None:
    """Wait for the rig to settle, then fire.

    The delay precedes the exposure so vibration can damp out; firing first and
    sleeping afterwards would look the same in code and do nothing.
    """
    if settle_delay_s > 0:
        logger.info("Settling for %.2fs before exposure.", settle_delay_s)
        time.sleep(settle_delay_s)
    release(client, caps, af=af)
