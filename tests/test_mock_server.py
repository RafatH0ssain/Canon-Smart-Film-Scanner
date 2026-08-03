"""Tests for the mock camera itself.

The mock is test infrastructure, so it needs its own tests: if it silently
stopped rejecting live-view requests before the session is started, every test
built on it would keep passing while diverging from a real body.

The request and response shapes asserted here were calibrated against Canon's
CCAPI reference. Where this file encodes an expectation about CCAPI's own
behaviour -- live view being started by choosing a size, shutter mode values,
the 503 for a stopped stream -- that expectation is the thing under test.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
import requests

from csfs.ccapi import CCAPIClient, discover
from csfs.ccapi.endpoints import SHUTTER_MODE_PREFERENCE


def _decode(payload: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None, "response was not a decodable JPEG"
    return image


def _start_liveview(client: CCAPIClient, caps, size: str = "medium") -> None:
    client.post(caps.path("liveview"), json={"liveviewsize": size, "cameradisplay": "keep"})


# ---------------------------------------------------------------------------
# Live view
# ---------------------------------------------------------------------------


def test_liveview_refuses_frames_before_a_size_is_chosen(client: CCAPIClient) -> None:
    caps = discover(client)
    response = client.get(caps.path("liveview_flip"), expect_status=False)
    assert response.status_code == 503


def test_liveview_flip_returns_a_jpeg_frame(client: CCAPIClient) -> None:
    caps = discover(client)
    _start_liveview(client, caps)
    response = client.get(caps.path("liveview_flip"))
    assert response.headers["Content-Type"] == "image/jpeg"
    assert _decode(response.content).shape == (640, 960, 3)


def test_liveview_is_stopped_by_setting_size_off(client: CCAPIClient) -> None:
    """There is no DELETE for live view; "off" is how it stops."""
    caps = discover(client)
    _start_liveview(client, caps)
    assert client.get(caps.path("liveview_flip"), expect_status=False).status_code == 200

    client.post(caps.path("liveview"), json={"liveviewsize": "off"})
    assert client.get(caps.path("liveview_flip"), expect_status=False).status_code == 503


def test_liveview_size_changes_frame_dimensions(client: CCAPIClient) -> None:
    caps = discover(client)
    _start_liveview(client, caps, size="small")
    small = _decode(client.get(caps.path("liveview_flip")).content)
    _start_liveview(client, caps, size="medium")
    medium = _decode(client.get(caps.path("liveview_flip")).content)
    assert medium.shape[1] > small.shape[1]


def test_liveview_rejects_an_invalid_size(client: CCAPIClient) -> None:
    caps = discover(client)
    response = client.post(
        caps.path("liveview"), json={"liveviewsize": "enormous"}, expect_status=False
    )
    assert response.status_code == 400


def test_multipart_stream_delivers_multiple_frames(client: CCAPIClient) -> None:
    """The preferred stream is standard MJPEG, so it needs no custom parser."""
    caps = discover(client)
    _start_liveview(client, caps)
    with client.stream(caps.path("liveview_multipart")) as response:
        assert "multipart/x-mixed-replace" in response.headers["Content-Type"]
        collected = b""
        seen = 0
        for chunk in response.iter_content(chunk_size=8192):
            collected += chunk
            seen = collected.count(b"--boundary")
            if seen >= 3:
                break
    assert seen >= 3
    assert b"Content-Type: image/jpeg" in collected


def test_scroll_stream_sends_bare_jpegs(client: CCAPIClient) -> None:
    """'scroll' carries concatenated JPEGs, with no framing of Canon's own.

    The binary framing belongs to 'scrolldetail'. Confusing the two is not a
    cosmetic mistake: a parser expecting a length header finds none and yields
    no frames at all.
    """
    caps = discover(client)
    _start_liveview(client, caps)
    with client.stream(caps.path("liveview_scroll")) as response:
        assert "octet-stream" in response.headers["Content-Type"]
        collected = b""
        for chunk in response.iter_content(chunk_size=8192):
            collected += chunk
            if len(collected) > 40_000:
                break
    assert collected.startswith(b"\xff\xd8")  # JPEG start-of-image
    assert not collected.startswith(b"\xff\x00\x00")


def test_scrolldetail_stream_uses_canon_binary_framing(client: CCAPIClient) -> None:
    """The 'detail' variant is the one with the marker/type/length header."""
    caps = discover(client)
    _start_liveview(client, caps)
    with client.stream(caps.path("liveview_scrolldetail")) as response:
        collected = b""
        for chunk in response.iter_content(chunk_size=8192):
            collected += chunk
            if len(collected) > 40_000:
                break
    assert collected.startswith(b"\xff\x00\x00")


def test_scroll_reports_already_started_for_a_second_stream(client: CCAPIClient) -> None:
    """A leftover session must announce itself the way a real body does."""
    caps = discover(client)
    _start_liveview(client, caps)
    path = caps.path("liveview_scroll")
    with client.stream(path):
        second = client.get(path, expect_status=False)
        assert second.status_code == 503
        assert "Already started" in second.text
    client.delete(path, expect_status=False)


# ---------------------------------------------------------------------------
# Shutter
# ---------------------------------------------------------------------------


def test_shutter_writes_a_file_to_the_card(client: CCAPIClient) -> None:
    caps = discover(client)
    assert client.post(caps.path("shutter"), json={"af": False}).json() == {}

    card = client.get_json(caps.path("contents"))["path"][0]
    folder = client.get_json(card)["path"][0]
    files = client.get_json(folder)["path"]
    assert len(files) == 1

    downloaded = client.get(files[0])
    assert _decode(downloaded.content).shape == (1600, 2400, 3)


def test_shutter_rejects_a_non_boolean_af_flag(client: CCAPIClient) -> None:
    caps = discover(client)
    response = client.post(caps.path("shutter"), json={"af": "yes"}, expect_status=False)
    assert response.status_code == 400


def test_capture_is_higher_resolution_than_live_view(client: CCAPIClient) -> None:
    """The preview is a reduced stream; the scan must come from the real file."""
    caps = discover(client)
    _start_liveview(client, caps)
    preview = _decode(client.get(caps.path("liveview_flip")).content)

    client.post(caps.path("shutter"), json={"af": False})
    card = client.get_json(caps.path("contents"))["path"][0]
    folder = client.get_json(card)["path"][0]
    capture = _decode(client.get(client.get_json(folder)["path"][0]).content)

    assert capture.shape[0] > preview.shape[0]


def test_event_polling_reports_the_new_capture(client: CCAPIClient) -> None:
    """Beats sleeping for a guessed interval after firing the shutter."""
    caps = discover(client)
    assert client.get_json(caps.path("event_polling"))["addedcontents"] == []
    client.post(caps.path("shutter"), json={"af": False})
    assert client.get_json(caps.path("event_polling"))["addedcontents"] == ["IMG_0001.JPG"]


# ---------------------------------------------------------------------------
# Focus
# ---------------------------------------------------------------------------


def test_focus_drive_accepts_the_documented_values(client: CCAPIClient) -> None:
    """CCAPI drives focus in six fixed increments via a single 'value' field."""
    caps = discover(client)
    path = caps.path("focus_drive")
    for value in ("near1", "near2", "near3", "far1", "far2", "far3"):
        assert client.post(path, json={"value": value}).json() == {}


def test_focus_drive_rejects_an_invented_payload(client: CCAPIClient) -> None:
    """Guards against reintroducing the {direction, steps} shape this project
    once invented before checking the reference."""
    caps = discover(client)
    response = client.post(
        caps.path("focus_drive"), json={"direction": "near", "steps": 3}, expect_status=False
    )
    assert response.status_code == 400


def test_focus_drive_rejects_an_unknown_value(client: CCAPIClient) -> None:
    caps = discover(client)
    response = client.post(
        caps.path("focus_drive"), json={"value": "sideways1"}, expect_status=False
    )
    assert response.status_code == 400


def test_focus_drive_fails_without_a_lens(client: CCAPIClient, mock_camera_url: str) -> None:
    """Stands in for a fully manual lens: the app must degrade, not crash."""
    caps = discover(client)
    requests.post(f"{mock_camera_url}/__mock/lens", json={"mounted": False}, timeout=5)
    response = client.post(
        caps.path("focus_drive"), json={"direction": "near"}, expect_status=False
    )
    assert response.status_code == 503


def test_lens_info_reports_mount_state(client: CCAPIClient, mock_camera_url: str) -> None:
    """Note CCAPI does not report AF capability, only what is mounted."""
    caps = discover(client)
    assert client.get_json(caps.path("lens"))["mount"] is True

    requests.post(f"{mock_camera_url}/__mock/lens", json={"mounted": False}, timeout=5)
    info = client.get_json(caps.path("lens"))
    assert info["mount"] is False
    assert info["name"] == ""


def test_defocusing_reduces_measured_sharpness(client: CCAPIClient) -> None:
    """Ties the mock's focus model to something the UI can actually display."""
    caps = discover(client)
    _start_liveview(client, caps)

    def acutance() -> float:
        frame = _decode(client.get(caps.path("liveview_flip")).content)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    sharp = acutance()
    for _ in range(3):
        client.post(caps.path("focus_drive"), json={"value": "far3"})
    assert acutance() < sharp


def test_camera_zoom_reveals_detail_software_zoom_cannot(client: CCAPIClient) -> None:
    """Camera-side zoom returns real sensor detail, not enlarged pixels.

    This is why the focus loupe should drive lvzoom rather than upscaling the
    live-view frame on the PC.
    """
    caps = discover(client)
    _start_liveview(client, caps)
    for _ in range(2):
        client.post(caps.path("focus_drive"), json={"value": "far3"})

    def acutance() -> float:
        frame = _decode(client.get(caps.path("liveview_flip")).content)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    at_1x = acutance()
    client.put(caps.path("lv_zoom"), json={"value": "10"})
    assert acutance() > at_1x


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_shutter_mode_offers_the_documented_values(client: CCAPIClient) -> None:
    caps = discover(client)
    ability = client.get_json(caps.path("shutter_mode"))["ability"]
    assert set(ability) == {"elec_1st_curtain", "mechanical", "electronic"}


def test_electronic_shutter_is_our_first_preference(client: CCAPIClient) -> None:
    """v0.1 depends on this: a fully electronic shutter has no moving parts."""
    caps = discover(client)
    ability = client.get_json(caps.path("shutter_mode"))["ability"]
    chosen = next(m for m in SHUTTER_MODE_PREFERENCE if m in ability)
    assert chosen == "electronic"

    client.put(caps.path("shutter_mode"), json={"value": chosen})
    assert client.get_json(caps.path("shutter_mode"))["value"] == "electronic"


def test_shutter_mode_does_not_default_to_electronic(client: CCAPIClient) -> None:
    """Code that forgets to select it must not pass by accident."""
    caps = discover(client)
    assert client.get_json(caps.path("shutter_mode"))["value"] != "electronic"


def test_setting_rejects_an_unsupported_value(client: CCAPIClient) -> None:
    caps = discover(client)
    response = client.put(
        caps.path("shutter_mode"), json={"value": "telepathic"}, expect_status=False
    )
    assert response.status_code == 400


def test_auto_power_off_can_be_disabled(client: CCAPIClient) -> None:
    """A sleeping camera looks exactly like a wiring fault. Turn it off."""
    caps = discover(client)
    assert client.get_json(caps.path("auto_power_off"))["value"] != "disable"
    client.put(caps.path("auto_power_off"), json={"value": "disable"})
    assert client.get_json(caps.path("auto_power_off"))["value"] == "disable"


@pytest.mark.parametrize("setting", ["iso", "aperture", "shutter_speed", "white_balance"])
def test_exposure_settings_report_value_and_ability(
    client: CCAPIClient, setting: str
) -> None:
    """v0.6 locks these per roll, so both fields must be present."""
    caps = discover(client)
    payload = client.get_json(caps.path(setting))
    assert "value" in payload
    assert isinstance(payload["ability"], list) and payload["ability"]


# ---------------------------------------------------------------------------
# Film type
# ---------------------------------------------------------------------------


def test_film_type_switches_between_color_and_bw(
    client: CCAPIClient, mock_camera_url: str
) -> None:
    """Both film types must be exercisable without a camera."""
    caps = discover(client)
    _start_liveview(client, caps)

    def red_minus_blue() -> float:
        frame = _decode(client.get(caps.path("liveview_flip")).content).astype(float)
        return float(frame[..., 2].mean() - frame[..., 0].mean())

    requests.post(f"{mock_camera_url}/__mock/film", json={"mode": "color"}, timeout=5)
    color_cast = red_minus_blue()
    requests.post(f"{mock_camera_url}/__mock/film", json={"mode": "bw"}, timeout=5)
    bw_cast = red_minus_blue()

    assert color_cast > 30
    assert abs(bw_cast) < 15
