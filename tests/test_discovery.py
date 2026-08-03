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
    [
        "device_info", "liveview", "liveview_flip", "liveview_multipart",
        "shutter", "focus_drive", "contents", "shutter_mode", "lv_zoom",
        "lens", "auto_power_off",
    ],
)
def test_core_capabilities_resolve(client: CCAPIClient, capability: str) -> None:
    """Everything v0.1 and v0.2 depend on must resolve against the mock."""
    caps = discover(client)
    assert caps.has(capability), f"'{capability}' did not resolve; unmatched: {caps.unmatched}"
    assert caps.path(capability).startswith("/")


def test_root_returns_versions_mapped_to_endpoint_arrays(client: CCAPIClient) -> None:
    """CCAPI's root maps each version to an array of endpoint objects.

    Parsing this shape wrongly makes an activated camera look unactivated, so it
    is worth asserting on directly rather than only through its effects.
    """
    payload = client.get_json("/ccapi")
    assert payload, "root returned nothing"
    for version, entries in payload.items():
        assert version.startswith("ver")
        assert isinstance(entries, list)
        assert all("path" in e for e in entries)


def test_newer_api_version_wins_for_duplicated_capabilities(client: CCAPIClient) -> None:
    """The same capability is republished per version; use the newest.

    Without an explicit tiebreak this depends on dict ordering, which is a
    genuinely horrible bug to chase later.
    """
    caps = discover(client)
    assert len(caps.api_versions) > 1, "mock should publish more than one version"
    newest = caps.latest_version()
    assert newest in caps.path("device_info")


def test_capability_absent_from_older_version_still_resolves(client: CCAPIClient) -> None:
    """Shutter mode exists only in the newer API version, as on real bodies."""
    caps = discover(client)
    assert caps.has("shutter_mode")
    assert caps.latest_version() in caps.path("shutter_mode")


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


def test_match_requires_a_whole_segment_not_a_substring() -> None:
    """A loose substring hint must not claim a different endpoint.

    'drive' (drive mode) once resolved onto 'drivefocus', which would have sent
    drive-mode writes to the focus endpoint. A missing capability is
    diagnosable; a silently wrong one is not.
    """
    paths = ["/ccapi/ver110/shooting/control/drivefocus"]
    assert _match_capability(("drive",), paths) is None
    assert _match_capability(("drivefocus",), paths) == paths[0]


def test_match_supports_multi_segment_hints() -> None:
    """A hint with a slash matches a trailing run of segments."""
    paths = ["/ccapi/ver110/shooting/settings", "/ccapi/ver110/shooting/settings/iso"]
    assert _match_capability(("shooting/settings",), paths).endswith("/shooting/settings")


def test_unmatched_excludes_older_version_duplicates(client: CCAPIClient) -> None:
    """Older copies of a matched capability are not 'unused' endpoints."""
    caps = discover(client)
    assert len(caps.api_versions) > 1
    assert not any("deviceinformation" in path for path in caps.unmatched)


def test_match_prefers_shorter_path() -> None:
    """The live-view endpoint itself, not its /flip child."""
    paths = ["/ccapi/ver110/shooting/liveview/flip",
             "/ccapi/ver110/shooting/liveview"]
    assert _match_capability(("liveview",), paths).endswith("/liveview")


def test_match_prefers_newer_api_version() -> None:
    """Same path length, different version: the newer one must win."""
    endpoints = {
        "/ccapi/ver100/deviceinformation": Endpoint(
            "/ccapi/ver100/deviceinformation", frozenset({"get"}), "ver100"
        ),
        "/ccapi/ver140/deviceinformation": Endpoint(
            "/ccapi/ver140/deviceinformation", frozenset({"get"}), "ver140"
        ),
    }
    assert _match_capability(("deviceinformation",), endpoints) == (
        "/ccapi/ver140/deviceinformation"
    )


def test_match_returns_none_when_nothing_fits() -> None:
    assert _match_capability(("nonesuch",), ["/ccapi/ver110/deviceinformation"]) is None
