"""End-to-end tests for the web app against the mock camera.

These drive the same HTTP API the browser uses, so they cover the wiring between
the CCAPI layer and the processing layer that neither's own tests can.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import numpy as np
import pytest
import requests
from fastapi.testclient import TestClient

from csfs.app import server as server_module
from csfs.app.session import PreviewSettings
from csfs.config import Config
from csfs.processing.codec import decode_jpeg


@pytest.fixture
def ui(mock_camera_url: str, reset_camera: None, tmp_path: Path) -> Iterator[TestClient]:
    """The web app, pointed at the mock camera, writing captures to tmp_path.

    The server keeps its session in a module-level global -- one camera, one
    process -- so the fixture has to reset preview state explicitly. Without
    this, a test that switches invert off leaks that into every later test.
    """
    config = Config()
    config.camera.base_url = mock_camera_url
    config.capture.output_dir = str(tmp_path)
    # Keep the tests quick; the delay's timing is covered in test_capture.py.
    config.capture.settle_delay_s = 0.0
    server_module.configure(config)
    server_module.session.preview = PreviewSettings()

    with TestClient(server_module.app) as client:
        yield client

    server_module.session.disconnect()


def test_status_before_connecting(ui: TestClient) -> None:
    body = ui.get("/api/status").json()
    assert body["connected"] is False
    assert body["streaming"] is False


def test_index_page_is_served(ui: TestClient) -> None:
    response = ui.get("/")
    assert response.status_code == 200
    assert "Canon Smart Film Scan" in response.text


def test_connect_reports_capabilities_and_starts_streaming(ui: TestClient) -> None:
    body = ui.post("/api/connect").json()
    assert body["connected"] is True
    assert body["streaming"] is True
    assert "shutter" in body["capabilities"]
    assert body["has_camera_zoom"] is True


def test_connect_selects_the_electronic_shutter(ui: TestClient) -> None:
    """v0.1's core promise: no mechanical shutter shock."""
    body = ui.post("/api/connect").json()
    assert body["shutter_mode"] == "electronic"
    assert body["settings"]["shutter_mode"]["value"] == "electronic"


def test_connect_failure_reports_an_actionable_message(
    ui: TestClient, tmp_path: Path
) -> None:
    server_module.session.config.camera.base_url = "http://127.0.0.1:9"
    response = ui.post("/api/connect")
    assert response.status_code == 502
    assert "config.yaml" in response.json()["detail"]


def test_stream_requires_a_connection(ui: TestClient) -> None:
    assert ui.get("/stream.mjpg").status_code == 409


def test_single_frame_is_served_and_decodable(ui: TestClient) -> None:
    ui.post("/api/connect")
    response = ui.get("/frame.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert decode_jpeg(response.content).shape == (640, 960, 3)


def test_invert_toggle_changes_the_served_frame(ui: TestClient) -> None:
    """The preview must actually invert, and default to doing so."""
    ui.post("/api/connect")

    ui.post("/api/preview", json={"invert": True})
    positive = decode_jpeg(ui.get("/frame.jpg").content).astype(float)

    ui.post("/api/preview", json={"invert": False})
    negative = decode_jpeg(ui.get("/frame.jpg").content).astype(float)

    # A negative's dense image area is dark; inverted it must be brighter.
    interior = (slice(200, 440), slice(300, 660))
    assert positive[interior].mean() > negative[interior].mean()


def test_invert_is_on_by_default(ui: TestClient) -> None:
    """Framing a positive is the point; you should not have to switch it on."""
    assert ui.get("/api/status").json()["preview"]["invert"] is True


def test_loupe_changes_the_view_without_changing_its_size(ui: TestClient) -> None:
    ui.post("/api/connect")
    full = decode_jpeg(ui.get("/frame.jpg").content)

    ui.post("/api/preview", json={"loupe": True, "loupe_zoom": 6.0})
    zoomed = decode_jpeg(ui.get("/frame.jpg").content)

    assert zoomed.shape == full.shape
    assert not np.array_equal(zoomed, full)


def test_loupe_position_is_clamped_by_validation(ui: TestClient) -> None:
    ui.post("/api/connect")
    assert ui.post("/api/preview", json={"loupe_x": 1.5}).status_code == 422
    assert ui.post("/api/preview", json={"loupe_zoom": 0}).status_code == 422


def test_preview_settings_persist(ui: TestClient) -> None:
    ui.post("/api/connect")
    ui.post("/api/preview", json={"loupe": True, "loupe_zoom": 8.0, "loupe_x": 0.25})
    preview = ui.get("/api/status").json()["preview"]
    assert preview["loupe"] is True
    assert preview["loupe_zoom"] == 8.0
    assert preview["loupe_x"] == 0.25


def test_camera_zoom_is_applied(ui: TestClient) -> None:
    ui.post("/api/connect")
    assert ui.post("/api/camera-zoom", json={"value": "10"}).status_code == 200


def test_camera_zoom_rejects_an_unsupported_value(ui: TestClient) -> None:
    ui.post("/api/connect")
    assert ui.post("/api/camera-zoom", json={"value": "99"}).status_code == 400


def test_capture_requires_a_connection(ui: TestClient) -> None:
    assert ui.post("/api/capture").status_code == 409


def test_capture_downloads_the_file(ui: TestClient, tmp_path: Path) -> None:
    ui.post("/api/connect")
    body = ui.post("/api/capture").json()

    saved = Path(body["saved"])
    assert saved.exists()
    assert saved.parent == tmp_path
    assert decode_jpeg(saved.read_bytes()).shape == (1600, 2400, 3)


def test_capture_keeps_the_original_un_inverted(ui: TestClient) -> None:
    """Non-destructive: the downloaded file is the camera's, untouched.

    The preview may be showing a positive, but what lands on disk must still be
    the negative the camera actually wrote.
    """
    ui.post("/api/connect")
    ui.post("/api/preview", json={"invert": True})
    saved = Path(ui.post("/api/capture").json()["saved"])

    image = decode_jpeg(saved.read_bytes()).astype(float)
    # An un-inverted colour negative is orange: red clearly exceeds blue.
    assert image[..., 2].mean() - image[..., 0].mean() > 30


def test_repeated_captures_each_produce_a_file(ui: TestClient) -> None:
    ui.post("/api/connect")
    first = ui.post("/api/capture").json()["name"]
    second = ui.post("/api/capture").json()["name"]
    assert first != second

    listed = ui.get("/api/captures").json()["files"]
    assert set(listed) >= {first, second}


def test_captures_listing_hides_partial_downloads(ui: TestClient, tmp_path: Path) -> None:
    (tmp_path / "IMG_9999.JPG.part").write_bytes(b"incomplete")
    files = ui.get("/api/captures").json()["files"]
    assert not any(name.endswith(".part") for name in files)


# ---------------------------------------------------------------------------
# v0.2: focus aids, focus drive, settings
# ---------------------------------------------------------------------------


def test_peaking_changes_the_served_frame(ui: TestClient) -> None:
    ui.post("/api/connect")
    plain = decode_jpeg(ui.get("/frame.jpg").content)

    ui.post("/api/preview", json={"peaking": True})
    peaked = decode_jpeg(ui.get("/frame.jpg").content)

    assert peaked.shape == plain.shape
    assert not np.array_equal(peaked, plain)


def test_peaking_is_off_by_default(ui: TestClient) -> None:
    """An overlay that is always on stops being information."""
    assert ui.get("/api/status").json()["preview"]["peaking"] is False


def test_peaking_sensitivity_is_validated(ui: TestClient) -> None:
    assert ui.post("/api/preview", json={"peaking_sensitivity": 2.0}).status_code == 422
    assert ui.post("/api/preview", json={"peaking_sensitivity": -0.1}).status_code == 422


def test_focus_drive_is_reported_available_with_an_af_lens(ui: TestClient) -> None:
    body = ui.post("/api/connect").json()
    assert body["focus"]["available"] is True
    assert "MACRO" in body["focus"]["lens"]


def test_focus_drive_moves_focus(ui: TestClient) -> None:
    ui.post("/api/connect")
    assert ui.post("/api/focus", json={"direction": "near", "size": "fine"}).status_code == 200
    assert ui.post("/api/focus", json={"direction": "far", "size": "coarse"}).status_code == 200


def test_focus_drive_rejects_an_invalid_direction(ui: TestClient) -> None:
    ui.post("/api/connect")
    assert ui.post("/api/focus", json={"direction": "up"}).status_code == 422


def test_focus_drive_requires_a_connection(ui: TestClient) -> None:
    assert ui.post("/api/focus", json={"direction": "near"}).status_code == 409


def test_manual_focus_is_reported_with_a_reason(ui: TestClient, mock_camera_url: str) -> None:
    """The UI must hide controls that cannot work, and say why."""
    ui.post("/api/connect")
    requests.post(f"{mock_camera_url}/__mock/lens", json={"manual_focus": True}, timeout=5)

    response = ui.post("/api/focus", json={"direction": "near"})
    assert response.status_code == 409
    assert "manual focus" in response.json()["detail"].lower()

    focus = ui.get("/api/status").json()["focus"]
    assert focus["available"] is False


def test_focus_recheck_recovers_after_switching_back_to_af(
    ui: TestClient, mock_camera_url: str
) -> None:
    ui.post("/api/connect")
    requests.post(f"{mock_camera_url}/__mock/lens", json={"manual_focus": True}, timeout=5)
    ui.post("/api/focus", json={"direction": "near"})
    assert ui.get("/api/status").json()["focus"]["available"] is False

    requests.post(f"{mock_camera_url}/__mock/lens", json={"manual_focus": False}, timeout=5)
    assert ui.post("/api/focus/recheck").json()["focus"]["available"] is True


def test_connect_prepares_the_camera_for_scanning(ui: TestClient) -> None:
    """Continuous drive would turn one release into a burst."""
    body = ui.post("/api/connect").json()
    assert body["prepared"] == {"drive_mode": "single", "af_operation": "oneshot"}
    assert body["settings"]["drive_mode"]["value"] == "single"
    assert body["settings"]["af_operation"]["value"] == "oneshot"


def test_preparation_can_be_switched_off(ui: TestClient) -> None:
    """It writes to the user's camera, so it must be refusable."""
    server_module.session.config.capture.prepare_camera_for_scanning = False
    try:
        body = ui.post("/api/connect").json()
        assert body["prepared"] == {}
        assert body["settings"]["drive_mode"]["value"] == "lowspeed"
    finally:
        server_module.session.config.capture.prepare_camera_for_scanning = True


def test_sharpness_reports_current_best_and_corners(ui: TestClient) -> None:
    ui.post("/api/connect")
    body = ui.get("/api/sharpness").json()
    assert body["sharpness"] > 0
    assert body["best"] >= body["sharpness"]
    assert set(body["corners"]) == {
        "top_left", "top_right", "bottom_left", "bottom_right", "center"
    }


def test_sharpness_best_is_retained_then_resettable(ui: TestClient) -> None:
    """Focusing is a hill-climb; the best so far is what makes a reading readable."""
    ui.post("/api/connect")
    first = ui.get("/api/sharpness").json()
    assert ui.get("/api/sharpness").json()["best"] >= first["sharpness"]

    assert ui.post("/api/sharpness/reset").json()["best"] == 0.0


def test_sharpness_requires_a_connection(ui: TestClient) -> None:
    assert ui.get("/api/sharpness").status_code == 409


def test_setting_can_be_changed(ui: TestClient) -> None:
    ui.post("/api/connect")
    body = ui.post("/api/setting", json={"name": "iso", "value": "800"}).json()
    assert body["settings"]["iso"]["value"] == "800"


def test_setting_rejects_a_value_the_camera_will_not_take(ui: TestClient) -> None:
    ui.post("/api/connect")
    response = ui.post("/api/setting", json={"name": "iso", "value": "999999"})
    assert response.status_code == 400
    assert "offers" in response.json()["detail"]


def test_setting_rejects_an_unknown_setting_name(ui: TestClient) -> None:
    """An allow-list, so a typo cannot reach the camera as something else."""
    ui.post("/api/connect")
    response = ui.post("/api/setting", json={"name": "sensorcleaning", "value": "on"})
    assert response.status_code == 400


def test_setting_requires_a_connection(ui: TestClient) -> None:
    assert ui.post("/api/setting", json={"name": "iso", "value": "800"}).status_code == 409


def test_status_reports_settings_with_their_selectable_values(ui: TestClient) -> None:
    """The UI builds dropdowns from these, so they must be present."""
    body = ui.post("/api/connect").json()
    iso = body["settings"]["iso"]
    assert iso["value"]
    assert len(iso["ability"]) > 1


def test_disconnect_stops_streaming(ui: TestClient) -> None:
    ui.post("/api/connect")
    body = ui.post("/api/disconnect").json()
    assert body["connected"] is False
    assert body["streaming"] is False
    assert ui.get("/stream.mjpg").status_code == 409
