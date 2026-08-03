"""Configuration loading.

Values come from three places, in increasing order of priority:

1. Built-in defaults (below) -- so the app runs against the mock with no setup.
2. ``config.yaml`` in the repository root -- git-ignored, holds your camera IP.
3. Environment variables named ``CSFS_<SECTION>_<KEY>`` -- highest priority.

The env-var layer exists so that tests and CI can point the app at a throwaway
mock server without writing a file, and so you can try a different camera
address for one run without editing anything.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, get_type_hints

import yaml

# The repository root: this file is <root>/src/csfs/config.py.
REPO_ROOT = Path(__file__).resolve().parents[2]

_ENV_PREFIX = "CSFS"


@dataclass
class CameraConfig:
    """How to reach the camera's CCAPI service."""

    base_url: str = "http://127.0.0.1:8080"
    timeout_s: float = 5.0
    # Live view is a long-lived streaming response, so it gets a longer read
    # timeout than a normal control call. Reusing timeout_s here would make a
    # healthy-but-slow stream look like a connection failure.
    liveview_timeout_s: float = 30.0

    # Only needed if an account is registered on the camera under its CCAPI
    # account settings, in which case CCAPI uses HTTP digest authentication.
    # Most setups have no account and leave these empty.
    username: str = ""
    password: str = ""


@dataclass
class CaptureConfig:
    """Shutter behaviour and where captured files land."""

    settle_delay_s: float = 1.5
    prefer_electronic_shutter: bool = True
    output_dir: str = "captures"
    delete_from_camera_after_download: bool = False

    # On connect, set drive mode to single and AF operation to one-shot.
    # Continuous drive turns one remote release into a burst, and servo AF keeps
    # refocusing between frames of a roll. Both are written to the camera, so
    # set this false if you would rather the app never changed your settings.
    prepare_camera_for_scanning: bool = True

    def resolved_output_dir(self) -> Path:
        """Absolute output directory, resolving relative paths against the repo."""
        p = Path(self.output_dir)
        return p if p.is_absolute() else (REPO_ROOT / p)


@dataclass
class FilmConfig:
    """Which inversion pipeline the preview starts in."""

    mode: str = "bw"  # "bw" or "color"; both are first-class


@dataclass
class ServerConfig:
    """The local web UI."""

    # Loopback by default: this server can fire your shutter, so it should not
    # be reachable from a network you do not control.
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class MockConfig:
    """The camera-free mock CCAPI server."""

    host: str = "127.0.0.1"
    port: int = 8080


@dataclass
class Config:
    camera: CameraConfig = field(default_factory=CameraConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    film: FilmConfig = field(default_factory=FilmConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    mock: MockConfig = field(default_factory=MockConfig)


@lru_cache(maxsize=None)
def _field_types(cls: type) -> dict[str, Any]:
    """Real types for a config dataclass's fields.

    ``dataclasses.fields()`` reports annotations as *strings* under
    ``from __future__ import annotations``, so comparing them to ``bool`` never
    matches and every value silently stays a string. ``get_type_hints`` resolves
    them to actual types. Cached because this is called per setting per load.
    """
    return get_type_hints(cls)


def _coerce(value: Any, target_type: Any) -> Any:
    """Convert a raw YAML/env value to the type the dataclass field declares.

    Environment variables arrive as strings, so ``"1.5"`` has to become a float
    and ``"false"`` has to become ``False`` -- note that plain ``bool("false")``
    is ``True``, which is exactly the bug this function exists to prevent.
    """
    if target_type is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if target_type is float:
        return float(value)
    if target_type is int:
        return int(value)
    if target_type is str:
        return str(value)
    return value


def _apply_mapping(section: Any, values: dict[str, Any], where: str) -> None:
    """Overlay a dict of values onto a config dataclass instance, in place."""
    known = _field_types(type(section))
    for key, value in values.items():
        if key not in known:
            raise ValueError(
                f"Unknown setting '{key}' in {where}. "
                f"Valid settings here: {', '.join(sorted(known))}."
            )
        setattr(section, key, _coerce(value, known[key]))


def _apply_env(config: Config) -> None:
    """Overlay ``CSFS_<SECTION>_<KEY>`` environment variables onto the config."""
    for section_field in fields(config):
        section = getattr(config, section_field.name)
        if not is_dataclass(section):
            continue
        types = _field_types(type(section))
        for f in fields(section):
            env_name = f"{_ENV_PREFIX}_{section_field.name}_{f.name}".upper()
            if env_name in os.environ:
                setattr(section, f.name, _coerce(os.environ[env_name], types[f.name]))


def load_config(path: Path | str | None = None) -> Config:
    """Load configuration from defaults, then ``config.yaml``, then the environment.

    Args:
        path: Explicit path to a YAML config file. If omitted, ``config.yaml``
            in the repository root is used when it exists. A missing default
            file is not an error -- the built-in defaults point at the mock
            server, so a fresh clone runs with no configuration at all.

    Returns:
        A fully populated :class:`Config`.

    Raises:
        FileNotFoundError: If an explicit ``path`` was given but does not exist.
        ValueError: If the file contains an unknown section or setting. This is
            deliberately strict: a silently ignored typo in your camera's
            address is far more annoying to debug than an error at startup.
    """
    config = Config()

    config_path = Path(path) if path is not None else REPO_ROOT / "config.yaml"
    if path is not None and not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{config_path} must contain a YAML mapping at the top level.")
        known_sections = {f.name for f in fields(config)}
        for section_name, values in raw.items():
            if section_name not in known_sections:
                raise ValueError(
                    f"Unknown section '{section_name}' in {config_path}. "
                    f"Valid sections: {', '.join(sorted(known_sections))}."
                )
            if values is None:
                continue
            if not isinstance(values, dict):
                raise ValueError(f"Section '{section_name}' in {config_path} must be a mapping.")
            _apply_mapping(getattr(config, section_name), values, f"{config_path} [{section_name}]")

    _apply_env(config)
    return config
