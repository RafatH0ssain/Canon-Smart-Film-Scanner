"""First-run behaviour of the packaged app.

None of this runs from a checkout, which is exactly why it needs testing:
the first person to meet it is someone who double-clicked a download.
"""

from __future__ import annotations

import socket
import threading

import pytest

from csfs import paths
from csfs.app import server as server_module
from csfs.config import Config


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path), raising=False)
    return tmp_path


def test_first_run_seeds_a_config_the_user_can_edit(frozen, monkeypatch, tmp_path):
    """The seed only exists if config.example.yaml is genuinely bundled.

    If the build stops shipping it, this fails rather than silently handing
    users an app with no settings file and no way to enter a camera address.
    """
    (frozen / "config.example.yaml").write_text(
        (paths.REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    folder = tmp_path / "userdata"
    monkeypatch.setattr(server_module, "user_data_dir", lambda: folder)

    created = server_module.prepare_user_folder()

    assert created == folder / "config.yaml"
    assert (folder / "captures").is_dir()
    assert "base_url" in created.read_text()
    assert "#" in created.read_text(), "the seeded config lost its comments"


def test_first_run_never_overwrites_an_existing_config(frozen, monkeypatch, tmp_path):
    folder = tmp_path / "userdata"
    folder.mkdir()
    (folder / "config.yaml").write_text("camera:\n  base_url: http://192.0.2.1:8080\n")
    monkeypatch.setattr(server_module, "user_data_dir", lambda: folder)

    server_module.prepare_user_folder()

    assert "192.0.2.1" in (folder / "config.yaml").read_text()


def test_a_checkout_creates_no_user_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module, "user_data_dir", lambda: tmp_path / "nope")
    assert server_module.prepare_user_folder() is None
    assert not (tmp_path / "nope").exists()


def test_the_bundled_mock_starts_when_nothing_real_is_configured():
    config = Config()  # defaults point camera.base_url at the mock
    config.mock.port = _free_port()
    config.camera.base_url = f"http://{config.mock.host}:{config.mock.port}"

    assert server_module.start_mock_camera_if_wanted(config) is True


def test_the_bundled_mock_stays_out_of_the_way_of_a_real_camera():
    """Pointing at a real camera must not start a mock behind it.

    Silently binding a second server while the user believes they are talking
    to their camera is the kind of thing that wastes an evening.
    """
    config = Config()
    config.camera.base_url = "http://192.0.2.55:8080"

    assert server_module.start_mock_camera_if_wanted(config) is False


def test_the_bundled_mock_does_not_fight_one_already_running():
    config = Config()
    config.mock.port = _free_port()
    config.camera.base_url = f"http://{config.mock.host}:{config.mock.port}"

    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind((config.mock.host, config.mock.port))
    holder.listen(1)
    try:
        assert server_module.start_mock_camera_if_wanted(config) is False
    finally:
        holder.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
