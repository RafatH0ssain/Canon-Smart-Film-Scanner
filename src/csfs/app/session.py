"""The scanning session: one camera, one live-view pump, one set of preview settings.

Exactly one live-view stream exists at a time. A background thread pulls frames
and publishes the most recent one; every viewer reads from that, so a slow
viewer drops frames instead of backing up the camera, and a reloaded browser tab
cannot open a second stream.

The pump publishes raw frames. Inversion, peaking and the loupe are applied per
viewer at serve time, so toggling is instant and two windows can differ.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from csfs.ccapi import CCAPIClient, Capabilities, discover
from csfs.ccapi import contents as contents_api
from csfs.ccapi import focus as focus_api
from csfs.ccapi import settings as settings_api
from csfs.ccapi import shutter as shutter_api
from csfs.ccapi.errors import CCAPIError
from csfs.ccapi.liveview import LiveView
from csfs.config import Config

logger = logging.getLogger(__name__)

_REPORTED_SETTINGS = (
    "iso", "aperture", "shutter_speed", "white_balance",
    "shutter_mode", "af_operation", "drive_mode",
)

# An allow-list, so a typo cannot reach the camera as some other setting. Image
# quality is excluded: it reports a nested {raw, heif} object, not a flat value.
_WRITABLE_SETTINGS = frozenset(_REPORTED_SETTINGS)


class CaptureInProgressError(RuntimeError):
    """Raised when a capture or connect is requested while one is running."""


@dataclass
class PreviewSettings:
    """How the live preview is rendered. Applied per viewer, not in the pump."""

    invert: bool = True
    loupe: bool = False
    loupe_zoom: float = 4.0
    loupe_x: float = 0.5
    loupe_y: float = 0.5
    peaking: bool = False
    peaking_sensitivity: float = 0.5


class FramePump:
    """Background thread that keeps the latest live-view frame available."""

    def __init__(self, live_view: LiveView) -> None:
        self._live_view = live_view
        self._condition = threading.Condition()
        self._frame: bytes | None = None
        self._sequence = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._error: str | None = None

    @property
    def error(self) -> str | None:
        """The last error that stopped the pump, if any."""
        return self._error

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._error = None
        self._thread = threading.Thread(target=self._run, name="liveview-pump", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            for frame in self._live_view.frames():
                if not self._running:
                    break
                with self._condition:
                    self._frame = frame
                    self._sequence += 1
                    self._condition.notify_all()
        except CCAPIError as exc:
            # Record it for the UI rather than dying silently in a thread.
            self._error = str(exc)
            logger.warning("Live-view pump stopped: %s", exc)
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            logger.exception("Live-view pump crashed.")
        finally:
            self._running = False
            with self._condition:
                self._condition.notify_all()

    def stop(self) -> None:
        self._running = False
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def latest(self, after: int, timeout: float = 5.0) -> tuple[bytes, int] | None:
        """Wait for a frame newer than sequence ``after``.

        Returns ``(jpeg, sequence)``, or None on timeout or when the pump stops.
        Passing back the last sequence seen is what makes a slow viewer skip
        frames rather than queue them.
        """
        with self._condition:
            if self._sequence <= after:
                self._condition.wait(timeout=timeout)
            if self._frame is None or self._sequence <= after:
                return None
            return self._frame, self._sequence


@dataclass
class ScanSession:
    """Everything one scanning session needs."""

    config: Config
    client: CCAPIClient | None = None
    capabilities: Capabilities | None = None
    live_view: LiveView | None = None
    pump: FramePump | None = None
    preview: PreviewSettings = field(default_factory=PreviewSettings)
    shutter_mode: str | None = None
    last_capture: str | None = None
    focus: focus_api.FocusController | None = None
    # Settings the app changed on the camera, so the UI can say what it touched.
    prepared: dict[str, str] = field(default_factory=dict)
    # Focusing is a hill-climb, so a reading only means something next to the
    # best found so far.
    best_sharpness: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _connect_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def connected(self) -> bool:
        return self.client is not None and self.capabilities is not None

    def connect(self) -> Capabilities:
        """Connect, discover capabilities, and prepare the camera for scanning.

        Preparation is non-fatal: a body that will not accept the electronic
        shutter or disable auto power-off is still usable.

        Refuses to run twice at once, or an impatient second click would tear
        down the connection the first is still building.
        """
        if not self._connect_lock.acquire(blocking=False):
            raise CaptureInProgressError("Already connecting. Give it a moment.")
        try:
            return self._connect()
        finally:
            self._connect_lock.release()

    def _connect(self) -> Capabilities:
        self.disconnect()

        client = CCAPIClient(
            self.config.camera.base_url,
            timeout_s=self.config.camera.timeout_s,
            liveview_timeout_s=self.config.camera.liveview_timeout_s,
            username=self.config.camera.username,
            password=self.config.camera.password,
        )
        capabilities = discover(client)
        self.client = client
        self.capabilities = capabilities
        logger.info(
            "Connected to %s: %d endpoints, versions %s",
            self.config.camera.base_url,
            len(capabilities.endpoints),
            ", ".join(sorted(capabilities.api_versions)),
        )

        if self.config.capture.prefer_electronic_shutter:
            self.shutter_mode = shutter_api.select_quietest_shutter(client, capabilities)
        settings_api.disable_auto_power_off(client, capabilities)

        if self.config.capture.prepare_camera_for_scanning:
            self.prepared = focus_api.prepare_for_scanning(client, capabilities)
            if self.prepared:
                logger.info(
                    "Adjusted for scanning: %s",
                    ", ".join(f"{k}={v}" for k, v in self.prepared.items()),
                )
        else:
            self.prepared = {}

        self.focus = focus_api.FocusController(client, capabilities)

        self.live_view = LiveView(client, capabilities)
        self.live_view.start()
        self.pump = FramePump(self.live_view)
        self.pump.start()
        return capabilities

    def disconnect(self) -> None:
        """Tear the session down, leaving the camera in a sane state."""
        if self.pump is not None:
            self.pump.stop()
            self.pump = None
        if self.live_view is not None:
            self.live_view.stop()
            self.live_view = None
        if self.client is not None:
            self.client.close()
            self.client = None
        self.capabilities = None
        self.shutter_mode = None
        self.focus = None
        self.prepared = {}

    def capture(self) -> Path:
        """Settle, fire the shutter, then download what the camera wrote.

        Returns the local path of the downloaded file.

        Raises:
            RuntimeError: If not connected, or if no new file appeared.
            CaptureInProgressError: If a capture is already running.
        """
        if not self.connected:
            raise RuntimeError("Not connected to a camera.")
        assert self.client is not None and self.capabilities is not None

        # Refuse rather than queue: blocking here parked a thread per extra
        # click or browser tab behind the first capture.
        if not self._lock.acquire(blocking=False):
            raise CaptureInProgressError(
                "A capture is already in progress. Wait for it to finish."
            )
        try:
            # Snapshot first so the new file is found by difference, which
            # survives a card whose numbering has wrapped.
            before = set(contents_api.list_images(self.client, self.capabilities))

            shutter_api.capture(
                self.client,
                self.capabilities,
                settle_delay_s=self.config.capture.settle_delay_s,
                af=False,
            )

            remote = self._wait_for_new_image(before)
            if remote is None:
                raise RuntimeError(
                    "The shutter fired but no new file appeared on the card. "
                    "Check that a card is inserted and has space."
                )

            local = contents_api.download(
                self.client, self.capabilities, remote, self.config.capture.resolved_output_dir()
            )
            self.last_capture = local.name

            if self.config.capture.delete_from_camera_after_download:
                contents_api.delete(self.client, remote)
            return local
        finally:
            self._lock.release()

    def _wait_for_new_image(self, before: set[str], timeout_s: float = 30.0) -> str | None:
        """Poll the card until a file appears that was not there before.

        Writing takes a variable amount of time, so polling for the result beats
        sleeping for a guessed interval.
        """
        assert self.client is not None and self.capabilities is not None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            current = contents_api.list_images(self.client, self.capabilities)
            new = [path for path in current if path not in before]
            if new:
                return sorted(new)[-1]
            time.sleep(0.25)
        return None

    def read_settings(self) -> dict[str, dict[str, object]]:
        """Current camera settings for display, skipping unsupported ones."""
        if not self.connected:
            return {}
        assert self.client is not None and self.capabilities is not None
        found = settings_api.read_all(self.client, self.capabilities, _REPORTED_SETTINGS)
        return {
            name: {"value": s.value, "ability": list(s.ability)} for name, s in found.items()
        }

    def note_sharpness(self, value: float) -> None:
        """Record a sharpness reading, keeping the best seen."""
        self.best_sharpness = max(self.best_sharpness, value)

    def write_setting(self, name: str, value: str) -> tuple[bool, str]:
        """Change one camera setting. Returns ``(ok, reason_if_not)``."""
        if not self.connected:
            return False, "Not connected."
        assert self.client is not None and self.capabilities is not None

        if name not in _WRITABLE_SETTINGS:
            return False, f"'{name}' is not a setting this app changes."
        if not self.capabilities.has(name):
            return False, f"This camera does not expose '{name}'."

        # Check against what the camera says it accepts right now, so the user
        # gets a reason rather than a bare rejection.
        current = settings_api.read_setting(self.client, self.capabilities, name)
        if current is not None and not current.can_set(value):
            return False, (
                f"The camera will not accept {name}={value} in its current mode. "
                f"It offers: {', '.join(current.ability)}"
            )
        if settings_api.write_setting(self.client, self.capabilities, name, value):
            return True, ""
        return False, f"The camera refused {name}={value}."

    def set_camera_zoom(self, value: str) -> bool:
        """Set camera-side live-view magnification, where the body has it."""
        if not self.connected:
            return False
        assert self.client is not None and self.capabilities is not None
        return settings_api.write_setting(self.client, self.capabilities, "lv_zoom", value)
