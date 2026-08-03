"""Shared test fixtures.

The mock camera is run as a *real* HTTP server on a throwaway port rather than
through an in-process test client. That is slightly slower, and it is worth it:
the CCAPI client's job is transport -- timeouts, streaming, connection loss --
and an in-process client would stub out precisely the layer under test.

None of this needs a camera.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Iterator

import pytest
import requests
import uvicorn

from csfs.ccapi.client import CCAPIClient
from csfs.mock import mock_server


def _free_port() -> int:
    """Ask the OS for an unused port, so parallel test runs cannot collide."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def mock_camera_url() -> Iterator[str]:
    """Run the mock CCAPI camera for the whole test session; yield its base URL."""
    port = _free_port()
    config = uvicorn.Config(
        mock_server.app, host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.02)
    else:
        raise RuntimeError("Mock camera did not start within 15s.")

    yield base_url

    server.should_exit = True
    thread.join(timeout=10.0)


@pytest.fixture
def reset_camera(mock_camera_url: str) -> Iterator[None]:
    """Return the mock camera to a clean state before and after each test."""
    requests.post(f"{mock_camera_url}/__mock/reset", timeout=5)
    yield
    requests.post(f"{mock_camera_url}/__mock/reset", timeout=5)


@pytest.fixture
def client(mock_camera_url: str, reset_camera: None) -> Iterator[CCAPIClient]:
    """A CCAPI client pointed at a freshly reset mock camera."""
    with CCAPIClient(mock_camera_url, timeout_s=10.0) as c:
        yield c
