"""Tests for connecting to a camera and learning what it supports.

This is the slice that proves the pipeline: address, activation, and a live
answer from the device.
"""

from __future__ import annotations

import pytest

from csfs.ccapi import CCAPIClient, discover
from csfs.ccapi.discovery import Endpoint, _match_capability
from csfs.ccapi.errors import (
    CameraUnreachableError,
    CCAPINotActivatedError,
    UnsupportedEndpointError,
)


def test_discovery_finds_api_versions(client: CCAPIClient) -> None:
    caps = discover(client)
    assert caps.api_versions, "camera reported no API versions"
    assert caps.latest_version()


def test_discovery_enumerates_endpoints(client: CCAPIClient) -> None:
    caps = discover(client)
    assert len(caps.endpoints) > 5


@pytest.mark.parametrize(
    "capability",
    ["device_info", "liveview_start", "shutter", "focus_drive", "contents", "shutter_mode"],
)
def test_core_capabilities_resolve(client: CCAPIClient, capability: str) -> None:
    """Everything v0.1 and v0.2 depend on must resolve against the mock."""
    caps = discover(client)
    assert caps.has(capability), f"'{capability}' did not resolve; caps: {caps.unmatched}"
    assert caps.path(capability).startswith("/")


def test_resolved_paths_are_actually_reachable(client: CCAPIClient) -> None:
    """A resolved path that 404s would mean discovery matched the wrong endpoint."""
    caps = discover(client)
    response = client.get(caps.path("device_info"))
    assert response.status_code == 200
    assert "productname" in response.json()


def test_shutter_and_shutter_mode_do_not_collide(client: CCAPIClient) -> None:
    """Overlapping hints must not resolve two capabilities to one endpoint.

    'shutter' and 'shuttermode' share a prefix, and picking the settings
    endpoint for the shutter release would fire nothing at all.
    """
    caps = discover(client)
    assert caps.path("shutter") != caps.path("shutter_mode")
    assert "settings" not in caps.path("shutter")


def test_unknown_capability_raises_a_helpful_error(client: CCAPIClient) -> None:
    caps = discover(client)
    with pytest.raises(UnsupportedEndpointError) as excinfo:
        caps.path("teleportation")
    assert "teleportation" in str(excinfo.value)
    assert "endpoints.py" in str(excinfo.value)


def test_unreachable_camera_gives_a_diagnosable_error() -> None:
    """Point at a closed port: the message must name the real causes."""
    with CCAPIClient("http://127.0.0.1:9", timeout_s=1.0) as client:
        with pytest.raises(CameraUnreachableError) as excinfo:
            discover(client)
    message = str(excinfo.value)
    assert "CCAPI" in message and "config.yaml" in message


def test_non_ccapi_server_reports_activation_not_connection(
    mock_camera_url: str,
) -> None:
    """Something answering that is not CCAPI is an activation problem, not a wiring one.

    The mock's own docs URL answers HTTP but is not a CCAPI root, which is the
    same shape as a camera on the network that was never activated.
    """
    with CCAPIClient(f"{mock_camera_url}/__docs", timeout_s=5.0) as client:
        with pytest.raises(CCAPINotActivatedError) as excinfo:
            discover(client)
    assert "activation" in str(excinfo.value).lower()


def test_endpoint_supports_unknown_methods_optimistically() -> None:
    """No reported methods means unknown, not unsupported."""
    assert Endpoint("/x").supports("get")
    assert Endpoint("/x", frozenset({"post"})).supports("post")
    assert not Endpoint("/x", frozenset({"post"})).supports("get")


def test_match_prefers_whole_segment_over_substring() -> None:
    paths = ["/ccapi/ver110/shooting/settings/shuttermode",
             "/ccapi/ver110/shooting/control/shutterbutton"]
    assert _match_capability(("shutterbutton", "shutter"), paths).endswith("shutterbutton")


def test_match_returns_none_when_nothing_fits() -> None:
    assert _match_capability(("nonesuch",), ["/ccapi/ver110/deviceinformation"]) is None
