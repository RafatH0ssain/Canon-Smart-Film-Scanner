"""Tests for HTTP transport and error translation.

The value of this layer is that a failure at the copy stand names its real
cause. These tests assert the translation, not the status codes.
"""

from __future__ import annotations

import pytest
import requests

from csfs.ccapi import CCAPIClient, discover
from csfs.ccapi.errors import (
    CameraUnreachableError,
    CCAPIAuthError,
    CCAPIBusyError,
    CCAPIResponseError,
)


def test_url_building_joins_discovered_paths(mock_camera_url: str) -> None:
    with CCAPIClient(mock_camera_url) as client:
        assert client.url_for("/ccapi/ver110/x") == f"{mock_camera_url}/ccapi/ver110/x"
        assert client.url_for("ccapi") == f"{mock_camera_url}/ccapi"


def test_absolute_urls_pass_through(mock_camera_url: str) -> None:
    """Listings hand back absolute paths; an absolute URL must not be re-joined."""
    with CCAPIClient(mock_camera_url) as client:
        assert client.url_for("http://example.invalid/x") == "http://example.invalid/x"


def test_trailing_slash_on_base_url_is_harmless(mock_camera_url: str) -> None:
    with CCAPIClient(mock_camera_url + "/") as client:
        assert discover(client).api_versions


def test_503_becomes_a_busy_error_naming_the_usual_causes(client: CCAPIClient) -> None:
    """A 503 means the camera is busy or another device holds the connection."""
    caps = discover(client)
    with pytest.raises(CCAPIBusyError) as excinfo:
        client.get(caps.path("liveview_flip"))  # live view not started
    message = str(excinfo.value)
    assert "only one" in message.lower()
    assert "camera connect" in message.lower()


def test_expect_status_false_returns_the_response_instead_of_raising(
    client: CCAPIClient,
) -> None:
    """Probing whether something works must not raise."""
    caps = discover(client)
    response = client.get(caps.path("liveview_flip"), expect_status=False)
    assert response.status_code == 503


def test_401_becomes_an_auth_error_explaining_accounts(mock_camera_url: str) -> None:
    """Digest auth is only involved when an account is registered on the camera.

    The message has to say that, because otherwise a 401 on a camera the user
    never configured an account for is baffling.
    """
    with CCAPIClient(f"{mock_camera_url}/__mock/needs-auth") as client:
        with pytest.raises(CCAPIAuthError) as excinfo:
            client.get("")
    message = str(excinfo.value)
    assert "config.yaml" in message
    assert "account" in message.lower()


def test_credentials_are_sent_as_digest_when_configured(mock_camera_url: str) -> None:
    """Digest, not basic: the password must not go over the wire in the clear."""
    with CCAPIClient(mock_camera_url, username="scanner", password="secret") as client:
        assert isinstance(client._session.auth, requests.auth.HTTPDigestAuth)


def test_no_auth_configured_leaves_the_session_unauthenticated(
    mock_camera_url: str,
) -> None:
    with CCAPIClient(mock_camera_url) as client:
        assert client._session.auth is None


def test_other_error_statuses_keep_the_generic_error(client: CCAPIClient) -> None:
    caps = discover(client)
    with pytest.raises(CCAPIResponseError) as excinfo:
        client.put(caps.path("shutter_mode"), json={"value": "telepathic"})
    assert excinfo.value.status_code == 400


def test_unreachable_address_names_the_things_to_check() -> None:
    with CCAPIClient("http://127.0.0.1:9", timeout_s=1.0) as client:
        with pytest.raises(CameraUnreachableError) as excinfo:
            client.get("/ccapi")
    message = str(excinfo.value)
    assert "powered on" in message.lower()
    assert "config.yaml" in message


def test_non_json_response_says_so_clearly(mock_camera_url: str) -> None:
    """An address owned by a router, not a camera, is a common mistake."""
    with CCAPIClient(mock_camera_url) as client:
        with pytest.raises(CCAPIResponseError) as excinfo:
            client.get_json("/__docs")
    assert "Expected JSON" in str(excinfo.value)
