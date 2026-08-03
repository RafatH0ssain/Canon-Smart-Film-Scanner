"""Tests for the mock camera itself.

The mock is test infrastructure, so it needs its own tests: if it silently
stopped rejecting live-view requests before the session is started, every test
built on it would keep passing while diverging from a real body.
"""

from __future__ import annotations

import cv2
import numpy as np
import requests

from csfs.ccapi import CCAPIClient, discover


def _decode(payload: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None, "response was not a decodable JPEG"
    return image


def test_liveview_refuses_frames_before_the_session_starts(client: CCAPIClient) -> None:
    caps = discover(client)
    response = client.get(caps.path("liveview_flip"), expect_status=False)
    assert response.status_code == 503


def test_liveview_flip_returns_a_jpeg_frame(client: CCAPIClient) -> None:
    caps = discover(client)
    client.post(caps.path("liveview_start"), json={})
    response = client.get(caps.path("liveview_flip"))
    assert response.headers["Content-Type"] == "image/jpeg"
    assert _decode(response.content).shape == (640, 960, 3)


def test_liveview_stream_delivers_multiple_frames(client: CCAPIClient) -> None:
    caps = discover(client)
    client.post(caps.path("liveview_start"), json={})
    with client.stream(caps.path("liveview_scroll")) as response:
        assert "multipart/x-mixed-replace" in response.headers["Content-Type"]
        seen = 0
        collected = b""
        for chunk in response.iter_content(chunk_size=8192):
            collected += chunk
            seen = collected.count(b"--csfsmockframe")
            if seen >= 3:
                break
    assert seen >= 3


def test_shutter_writes_a_file_to_the_card(client: CCAPIClient) -> None:
    caps = discover(client)
    result = client.post(caps.path("shutter"), json={"af": False}).json()
    assert result["name"].endswith(".JPG")

    listed = client.get_json(caps.path("contents"))["path"]
    files = client.get_json(listed[0])["path"]
    assert len(files) == 1

    downloaded = client.get(files[0])
    assert _decode(downloaded.content).shape == (1600, 2400, 3)


def test_capture_is_higher_resolution_than_live_view(client: CCAPIClient) -> None:
    """The preview is a reduced stream; the scan must come from the real file."""
    caps = discover(client)
    client.post(caps.path("liveview_start"), json={})
    preview = _decode(client.get(caps.path("liveview_flip")).content)

    client.post(caps.path("shutter"), json={"af": False})
    files = client.get_json(client.get_json(caps.path("contents"))["path"][0])["path"]
    capture = _decode(client.get(files[0]).content)

    assert capture.shape[0] > preview.shape[0]


def test_focus_drive_moves_and_clamps(client: CCAPIClient) -> None:
    caps = discover(client)
    path = caps.path("focus_drive")

    assert client.post(path, json={"direction": "far", "steps": 3}).json()["position"] == 3
    assert client.post(path, json={"direction": "near", "steps": 1}).json()["position"] == 2

    # Real lenses hit a hard stop; callers must not assume infinite travel.
    far = client.post(path, json={"direction": "far", "steps": 500}).json()["position"]
    assert far == 40


def test_focus_drive_rejects_a_bad_direction(client: CCAPIClient) -> None:
    caps = discover(client)
    response = client.post(
        caps.path("focus_drive"), json={"direction": "sideways"}, expect_status=False
    )
    assert response.status_code == 400


def test_defocusing_reduces_measured_sharpness(client: CCAPIClient) -> None:
    """Ties the mock's focus model to something the UI can actually display."""
    caps = discover(client)
    client.post(caps.path("liveview_start"), json={})

    def acutance() -> float:
        frame = _decode(client.get(caps.path("liveview_flip")).content)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    sharp = acutance()
    client.post(caps.path("focus_drive"), json={"direction": "far", "steps": 30})
    assert acutance() < sharp


def test_shutter_mode_can_be_set_to_electronic(client: CCAPIClient) -> None:
    """v0.1 depends on selecting the electronic shutter to avoid rig vibration."""
    caps = discover(client)
    path = caps.path("shutter_mode")
    client.put(path, json={"value": "electronic"})
    assert client.get_json(path)["value"] == "electronic"


def test_setting_rejects_an_unsupported_value(client: CCAPIClient) -> None:
    caps = discover(client)
    response = client.put(
        caps.path("shutter_mode"), json={"value": "telepathic"}, expect_status=False
    )
    assert response.status_code == 400


def test_film_type_switches_between_color_and_bw(
    client: CCAPIClient, mock_camera_url: str
) -> None:
    """Both film types must be exercisable without a camera."""
    caps = discover(client)
    client.post(caps.path("liveview_start"), json={})

    def red_minus_blue() -> float:
        frame = _decode(client.get(caps.path("liveview_flip")).content).astype(float)
        return float(frame[..., 2].mean() - frame[..., 0].mean())

    requests.post(f"{mock_camera_url}/__mock/film", json={"mode": "color"}, timeout=5)
    color_cast = red_minus_blue()
    requests.post(f"{mock_camera_url}/__mock/film", json={"mode": "bw"}, timeout=5)
    bw_cast = red_minus_blue()

    assert color_cast > 30
    assert abs(bw_cast) < 15
