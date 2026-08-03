"""Tests for configuration loading and precedence."""

from __future__ import annotations

from pathlib import Path

import pytest

from csfs.config import Config, load_config


def test_defaults_point_at_the_mock(tmp_path: Path) -> None:
    """A fresh clone with no config.yaml must still run against the mock."""
    config = load_config(_write(tmp_path, "camera: {}\n"))
    assert config.camera.base_url == "http://127.0.0.1:8080"
    assert config.capture.prefer_electronic_shutter is True


def test_yaml_overrides_defaults(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "camera:\n"
        "  base_url: http://10.0.0.5:8080\n"
        "  timeout_s: 2.5\n"
        "capture:\n"
        "  settle_delay_s: 3\n",
    )
    config = load_config(path)
    assert config.camera.base_url == "http://10.0.0.5:8080"
    assert config.camera.timeout_s == 2.5
    assert config.capture.settle_delay_s == 3.0


def test_env_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, "camera:\n  base_url: http://10.0.0.5:8080\n")
    monkeypatch.setenv("CSFS_CAMERA_BASE_URL", "http://192.168.9.9:8080")
    config = load_config(path)
    assert config.camera.base_url == "http://192.168.9.9:8080"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("false", False), ("False", False), ("0", False), ("no", False),
     ("true", True), ("1", True), ("yes", True), ("on", True)],
)
def test_bool_env_values_are_parsed_not_truthy_tested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    """``bool("false")`` is True; the loader must not fall into that trap.

    Getting this wrong would silently leave the mechanical shutter selected,
    which is exactly the vibration problem this project exists to solve.
    """
    monkeypatch.setenv("CSFS_CAPTURE_PREFER_ELECTRONIC_SHUTTER", raw)
    config = load_config(_write(tmp_path, "capture: {}\n"))
    assert config.capture.prefer_electronic_shutter is expected


def test_numeric_env_values_are_coerced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CSFS_CAPTURE_SETTLE_DELAY_S", "2.25")
    monkeypatch.setenv("CSFS_SERVER_PORT", "9001")
    config = load_config(_write(tmp_path, "capture: {}\n"))
    assert config.capture.settle_delay_s == 2.25
    assert isinstance(config.capture.settle_delay_s, float)
    assert config.server.port == 9001
    assert isinstance(config.server.port, int)


def test_unknown_setting_is_an_error_not_a_shrug(tmp_path: Path) -> None:
    """A typo in the camera address should fail loudly at startup."""
    path = _write(tmp_path, "camera:\n  base_urll: http://10.0.0.5:8080\n")
    with pytest.raises(ValueError, match="base_urll"):
        load_config(path)


def test_unknown_section_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "camrea:\n  base_url: http://10.0.0.5:8080\n")
    with pytest.raises(ValueError, match="camrea"):
        load_config(path)


def test_missing_explicit_config_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_relative_output_dir_resolves_against_repo_root() -> None:
    config = Config()
    resolved = config.capture.resolved_output_dir()
    assert resolved.is_absolute()
    assert resolved.name == "captures"


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path
