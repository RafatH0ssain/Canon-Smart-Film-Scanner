"""The scanning session: one camera, one live-view pump, one set of preview settings.

Two things here are worth understanding before changing anything.

**Exactly one live-view stream.** The camera will not sensibly serve several
concurrent live-view requests, and a browser tab that reloads must not open a
second one. So a single background thread pulls frames and publishes the most
recent one; every viewer reads from that. A slow viewer therefore drops frames
instead of backing up the camera, which is the right trade for a preview.

**The pump publishes raw frames.** Inversion and the loupe are applied per
viewer, at serve time, not in the pump. Toggling invert then costs nothing and
takes effect on the next frame, and two browser windows can show different
things without a second stream.
"""

from __future__ import annotations

import logging
import threading
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

# Settings surfaced in the UI. Each is optional -- a body that lacks one simply
# does not show it.
_REPORTED_SETTINGS = (
    "iso", "aperture", "shutter_speed", "white_balance",
    "shutter_mode", "af_operation", "drive_mode",
)

# Settings the UI may change. Deliberately a short allow-list: these are the
# ones that matter for scanning and are simple flat values. Others -- image
# quality reports a nested {raw, heif} object rather than a single value --
# need their own handling and are not exposed by accident.
_WRITABLE_SETTINGS = frozenset(
    {"iso", "aperture", "shutter_speed", "white_balance", "shutter_mode",
     "af_operation", "drive_mode"}
)


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
            # The camera went away mid-stream. Record it for the UI rather than
            # dying silently in a thread nobody is watching.
            self._error = str(exc)
            logger.warning("Live-view pump stopped: %s", exc)
        except Exception as exc:  # noqa: BLE001 - a thread must not die unexplained
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
        Viewers pass back the sequence they last saw, which is what makes a slow
        viewer skip frames rather than queue them.
        """
        with self._condition:
            if self._sequence <= after:
                self._condition.wait(timeout=timeout)
            if self._frame is None or self._sequence <= after:
                return None
            return self._frame, self._sequence


@dataclass
class ScanSession:
    """Everything one scanning session needs. Not thread-safe for connect/disconnect."""

    config: Config
    client: CCAPIClient | None = None
    capabilities: Capabilities | None = None
    live_view: LiveView | None = None
    pump: FramePump | None = None
    preview: PreviewSettings = field(default_factory=PreviewSettings)
    shutter_mode: str | None = None
    last_capture: str | None = None
    focus: focus_api.FocusController | None = None
    # Settings the app changed on the camera at connect time, so the UI can say
    # plainly what it touched rather than silently reconfiguring someone's body.
    prepared: dict[str, str] = field(default_factory=dict)
    # Best sharpness seen since the last reset. Focusing is a hill-climb, so a
    # reading only means something next to the best found so far.
    best_sharpness: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def connected(self) -> bool:
        return self.client is not None and self.capabilities is not None

    def connect(self) -> Capabilities:
        """Connect, discover capabilities, and prepare the camera for scanning.

        Preparation is deliberately non-fatal: a body that will not let us
        select the electronic shutter or disable auto power-off is still
        perfectly usable for scanning, just slightly less convenient. Refusing
        to connect over it would be the wrong call.
        """
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
            # Changes the user's camera, so it is announced and switchable off.
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

    # -- actions ------------------------------------------------------------

    def capture(self) -> Path:
        """Settle, fire the shutter, then download what the camera wrote.

        Serialised with a lock: firing a second exposure while the first is
        still being identified would make the two indistinguishable, and it is
        an easy thing to do by leaning on a keyboard shortcut.

        Returns:
            The local path of the downloaded file.

        Raises:
            RuntimeError: If not connected, or if no new file appeared.
        """
        if not self.connected:
            raise RuntimeError("Not connected to a camera.")
        assert self.client is not None and self.capabilities is not None

        with self._lock:
            # Snapshot first so the new file can be identified by difference.
            # More reliable than assuming the highest-numbered name is new, which
            # breaks on a card whose numbering has wrapped.
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

    def _wait_for_new_image(self, before: set[str], timeout_s: float = 30.0) -> str | None:
        """Poll the card until a file appears that was not there before.

        Writing a file takes a variable amount of time -- buffer depth, card
        speed, image size -- so polling for the actual result beats sleeping for
        a guessed interval.
        """
        import time

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
        """Record a sharpness reading, keeping the best seen.

        Focusing is a hill-climb: you turn past the peak and come back. Knowing
        the best value so far is what makes the current reading interpretable.
        """
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

        # Check against what the camera says it will accept right now, so the
        # user gets a clear reason instead of a bare rejection from the body.
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
        """Set camera-side live-view magnification.

        Preferred over the software loupe where available, because it returns
        real sensor detail rather than enlarging pixels the camera discarded.
        """
        if not self.connected:
            return False
        assert self.client is not None and self.capabilities is not None
        return settings_api.write_setting(self.client, self.capabilities, "lv_zoom", value)
