"""Tests for concurrent access to a camera that serves one client at a time.

Covers the causes of a capture hang seen on hardware: a shared
requests.Session between the live-view pump and control calls, unserialised
concurrent requests, captures queueing on a lock, and a settings read costing
one request per setting on every poll.
"""

from __future__ import annotations

import threading
import time

import pytest

from csfs.app.session import CaptureInProgressError, ScanSession
from csfs.ccapi import CCAPIClient, discover
from csfs.ccapi.liveview import LiveView
from csfs.config import Config


@pytest.fixture
def session(mock_camera_url: str, reset_camera: None, tmp_path) -> ScanSession:
    config = Config()
    config.camera.base_url = mock_camera_url
    config.capture.output_dir = str(tmp_path)
    config.capture.settle_delay_s = 0.0
    scan = ScanSession(config=config)
    yield scan
    scan.disconnect()


# ---------------------------------------------------------------------------
# Session isolation
# ---------------------------------------------------------------------------


def test_streaming_uses_a_separate_http_session(mock_camera_url: str) -> None:
    """requests.Session is not thread-safe and the pump holds one open."""
    with CCAPIClient(mock_camera_url) as client:
        assert client._session is not client._stream_session


def test_control_calls_work_while_a_stream_is_open(client: CCAPIClient) -> None:
    """A live-view stream must not block ordinary requests."""
    caps = discover(client)
    with LiveView(client, caps) as live:
        stream = live.frames()
        next(stream)  # open the stream and pull one frame

        # With the stream still open, control calls must complete promptly.
        started = time.monotonic()
        for _ in range(5):
            client.get_json(caps.path("device_info"))
        assert time.monotonic() - started < 10.0


def test_concurrent_control_calls_are_serialised(client: CCAPIClient) -> None:
    """Several threads issuing control calls must not corrupt each other."""
    caps = discover(client)
    path = caps.path("device_info")
    errors: list[BaseException] = []
    results: list[str] = []

    def worker() -> None:
        try:
            for _ in range(5):
                results.append(client.get_json(path)["productname"])
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent control calls failed: {errors}"
    assert len(results) == 30
    assert all(name == results[0] for name in results)


# ---------------------------------------------------------------------------
# Capture serialisation
# ---------------------------------------------------------------------------


def test_second_capture_is_refused_not_queued(session: ScanSession) -> None:
    """Queueing parks a thread per extra click or browser tab."""
    session.connect()

    # Hold the capture lock as a running capture would.
    assert session._lock.acquire(blocking=False)
    try:
        with pytest.raises(CaptureInProgressError):
            session.capture()
    finally:
        session._lock.release()


def test_capture_lock_is_released_after_a_failure(session: ScanSession) -> None:
    """A failed capture must not leave the session permanently refusing."""
    session.connect()
    original = session._wait_for_new_image
    session._wait_for_new_image = lambda *a, **k: None  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            session.capture()
    finally:
        session._wait_for_new_image = original  # type: ignore[assignment]

    # The lock must be free again, so a later capture can run.
    assert session._lock.acquire(blocking=False)
    session._lock.release()


def test_capture_succeeds_while_live_view_streams(session: ScanSession) -> None:
    """Capture with the pump running, the shape the hang took."""
    session.connect()
    assert session.pump is not None and session.pump.running

    started = time.monotonic()
    path = session.capture()
    assert path.exists()
    assert time.monotonic() - started < 60.0
    assert session.pump.running, "live view should survive a capture"


def test_second_connect_is_refused_while_one_is_running(session: ScanSession) -> None:
    """A second click would otherwise tear down the first attempt."""
    assert session._connect_lock.acquire(blocking=False)
    try:
        with pytest.raises(CaptureInProgressError):
            session.connect()
    finally:
        session._connect_lock.release()


# ---------------------------------------------------------------------------
# Request volume
# ---------------------------------------------------------------------------


def test_reading_all_settings_costs_one_request(session: ScanSession) -> None:
    """Runs on every UI poll, against a camera already busy streaming."""
    session.connect()
    assert session.client is not None

    calls: list[str] = []
    original = session.client.request
    # Only this thread's requests: the pump shares the client and reconnects its
    # stream on its own thread.
    this_thread = threading.get_ident()

    def counting(method: str, path: str, **kwargs: object):
        if threading.get_ident() == this_thread:
            calls.append(path)
        return original(method, path, **kwargs)

    session.client.request = counting  # type: ignore[assignment]
    try:
        settings = session.read_settings()
    finally:
        session.client.request = original  # type: ignore[assignment]

    assert len(settings) >= 5, f"expected most settings, got {sorted(settings)}"
    assert len(calls) == 1, f"expected a single combined read, made {len(calls)}: {calls}"


def test_bulk_read_falls_back_when_unavailable(session: ScanSession) -> None:
    """A body without the combined endpoint must still report its settings."""
    session.connect()
    assert session.capabilities is not None
    session.capabilities.resolved.pop("shooting_settings", None)

    settings = session.read_settings()
    assert "iso" in settings
    assert settings["iso"].get("ability")
