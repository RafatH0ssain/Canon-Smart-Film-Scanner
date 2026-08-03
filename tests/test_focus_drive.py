"""Tests for driving focus over CCAPI.

The payload shape here is the one Canon documents -- a single ``value`` field
carrying one of six fixed increments -- not a direction plus a step count. An
earlier version of this project invented the latter, which is exactly the class
of mistake these tests exist to prevent.
"""

from __future__ import annotations

import pytest
import requests

from csfs.ccapi import CCAPIClient, discover
from csfs.ccapi.focus import (
    DIRECTIONS,
    STEP_SIZES,
    FocusController,
    focus_value,
    prepare_for_scanning,
    read_lens,
)
from csfs.ccapi.settings import read_setting


# ---------------------------------------------------------------------------
# Drive values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("direction", "size", "expected"),
    [
        ("near", "fine", "near1"),
        ("near", "medium", "near2"),
        ("near", "coarse", "near3"),
        ("far", "fine", "far1"),
        ("far", "medium", "far2"),
        ("far", "coarse", "far3"),
    ],
)
def test_focus_values_match_the_documented_increments(
    direction: str, size: str, expected: str
) -> None:
    assert focus_value(direction, size) == expected


def test_every_direction_and_size_combination_is_valid() -> None:
    for direction in DIRECTIONS:
        for size in STEP_SIZES:
            assert focus_value(direction, size)


def test_invalid_direction_is_rejected_before_reaching_the_camera() -> None:
    with pytest.raises(ValueError, match="direction"):
        focus_value("sideways", "fine")


def test_invalid_size_is_rejected_before_reaching_the_camera() -> None:
    with pytest.raises(ValueError, match="size"):
        focus_value("near", "enormous")


# ---------------------------------------------------------------------------
# Driving
# ---------------------------------------------------------------------------


def test_focus_drive_is_accepted_by_the_camera(client: CCAPIClient) -> None:
    caps = discover(client)
    controller = FocusController(client, caps)
    assert controller.available
    assert controller.drive("near", "fine") is True
    assert controller.drive("far", "coarse") is True


def test_focus_drive_actually_changes_focus(client: CCAPIClient) -> None:
    """Proves the payload is understood, not merely accepted."""
    caps = discover(client)
    client.post(caps.path("liveview"), json={"liveviewsize": "medium", "cameradisplay": "keep"})
    controller = FocusController(client, caps)

    import cv2
    import numpy as np

    def acutance() -> float:
        payload = client.get(caps.path("liveview_flip")).content
        frame = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
        return float(cv2.Laplacian(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())

    sharp = acutance()
    for _ in range(3):
        controller.drive("far", "coarse")
    assert acutance() < sharp


def test_manual_focus_makes_focus_drive_unavailable(
    client: CCAPIClient, mock_camera_url: str
) -> None:
    """CCAPI cannot drive focus in manual focus -- the constraint that makes
    the camera's own peaking and remote focus drive mutually exclusive."""
    caps = discover(client)
    requests.post(f"{mock_camera_url}/__mock/lens", json={"manual_focus": True}, timeout=5)

    controller = FocusController(client, caps)
    assert controller.drive("near", "fine") is False
    assert controller.available is False
    assert "manual focus" in controller.unavailable_reason().lower()


def test_controller_recovers_after_the_switch_goes_back_to_af(
    client: CCAPIClient, mock_camera_url: str
) -> None:
    """Flicking the lens switch back must not require reconnecting."""
    caps = discover(client)
    requests.post(f"{mock_camera_url}/__mock/lens", json={"manual_focus": True}, timeout=5)
    controller = FocusController(client, caps)
    controller.drive("near", "fine")
    assert not controller.available

    requests.post(f"{mock_camera_url}/__mock/lens", json={"manual_focus": False}, timeout=5)
    controller.recheck()
    assert controller.available
    assert controller.drive("near", "fine") is True


def test_no_lens_makes_focus_drive_unavailable(
    client: CCAPIClient, mock_camera_url: str
) -> None:
    caps = discover(client)
    requests.post(f"{mock_camera_url}/__mock/lens", json={"mounted": False}, timeout=5)
    controller = FocusController(client, caps)
    assert controller.available is False
    assert "lens" in controller.unavailable_reason().lower()


def test_camera_without_focus_drive_reports_why(client: CCAPIClient) -> None:
    caps = discover(client)
    caps.resolved.pop("focus_drive")
    controller = FocusController(client, caps)
    assert controller.available is False
    assert "does not support" in controller.unavailable_reason()


def test_lens_name_is_reported_for_the_ui(client: CCAPIClient) -> None:
    caps = discover(client)
    lens = read_lens(client, caps)
    assert lens.mounted
    assert "MACRO" in lens.description


# ---------------------------------------------------------------------------
# Preparing the camera for scanning
# ---------------------------------------------------------------------------


def test_preparation_sets_single_drive_and_oneshot_af(client: CCAPIClient) -> None:
    """Continuous drive turns one remote release into a burst; servo AF drifts."""
    caps = discover(client)
    assert read_setting(client, caps, "drive_mode").value == "lowspeed"
    assert read_setting(client, caps, "af_operation").value == "servo"

    applied = prepare_for_scanning(client, caps)

    assert applied == {"drive_mode": "single", "af_operation": "oneshot"}
    assert read_setting(client, caps, "drive_mode").value == "single"
    assert read_setting(client, caps, "af_operation").value == "oneshot"


def test_preparation_does_not_rewrite_correct_settings(client: CCAPIClient) -> None:
    """Nothing to report means nothing was changed on the user's camera."""
    caps = discover(client)
    prepare_for_scanning(client, caps)
    assert prepare_for_scanning(client, caps) == {}


def test_preparation_skips_settings_the_camera_lacks(client: CCAPIClient) -> None:
    caps = discover(client)
    caps.resolved.pop("drive_mode")
    applied = prepare_for_scanning(client, caps)
    assert "drive_mode" not in applied
    assert applied == {"af_operation": "oneshot"}
