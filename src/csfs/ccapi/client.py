"""Base HTTP session for talking to a CCAPI camera.

Transport and error translation only. Which URL means what lives in
``discovery.py``; what to do with the bytes lives further up.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

import requests
from requests.auth import HTTPDigestAuth

from csfs.ccapi.errors import (
    CameraUnreachableError,
    CCAPIAuthError,
    CCAPIBusyError,
    CCAPIResponseError,
)


class CCAPIClient:
    """A connection to one camera's CCAPI service.

    Args:
        base_url: Root of the service, e.g. ``"http://192.168.1.50:8080"``.
        timeout_s: Read/connect timeout for ordinary control calls.
        liveview_timeout_s: Read timeout for long-lived streaming requests.
        session: Optional pre-built ``requests.Session``, mainly for tests.
        username: CCAPI account name, if one is registered on the camera.
        password: That account's password.

    Credentials are only needed when an account is registered on the camera,
    in which case CCAPI uses HTTP digest authentication.
    """

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 5.0,
        liveview_timeout_s: float = 30.0,
        session: requests.Session | None = None,
        username: str = "",
        password: str = "",
    ) -> None:
        # Strip the trailing slash to keep urljoin() predictable.
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.liveview_timeout_s = liveview_timeout_s
        self._session = session or requests.Session()
        self._owns_session = session is None

        # Streaming gets its own session: requests.Session is not thread-safe,
        # and the live-view pump holds a response open while control calls run
        # on other threads.
        self._stream_session = requests.Session()

        # Serialises control requests. The camera accepts one client and copes
        # badly with concurrent requests while streaming. Streaming requests
        # hold this only until headers arrive, never while reading the body.
        self._request_lock = threading.Lock()

        if username:
            self._session.auth = HTTPDigestAuth(username, password)
            self._stream_session.auth = HTTPDigestAuth(username, password)

    def close(self) -> None:
        """Close the underlying HTTP sessions."""
        self._stream_session.close()
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> CCAPIClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def url_for(self, path: str) -> str:
        """Build an absolute URL from a path returned by the camera."""
        if path.startswith(("http://", "https://")):
            return path
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def request(
        self,
        method: str,
        path: str,
        *,
        timeout_s: float | None = None,
        stream: bool = False,
        expect_status: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Issue one HTTP request to the camera.

        Args:
            method: HTTP verb.
            path: Absolute URL, or a path relative to ``base_url``.
            timeout_s: Override the default timeout for this call.
            stream: Leave the response body unread, for live view.
            expect_status: Raise on a 4xx/5xx. Set False when a non-2xx is a
                meaningful answer, such as probing whether an endpoint exists.
            **kwargs: Passed through to ``requests``.

        Raises:
            CameraUnreachableError: The camera did not respond.
            CCAPIAuthError: Credentials are required.
            CCAPIBusyError: The camera is busy or already has a client.
            CCAPIResponseError: Any other error status.
        """
        url = self.url_for(path)
        effective_timeout = timeout_s if timeout_s is not None else self.timeout_s
        session = self._stream_session if stream else self._session
        try:
            with self._request_lock:
                response = session.request(
                    method, url, timeout=effective_timeout, stream=stream, **kwargs
                )
        except requests.exceptions.RequestException as exc:
            raise CameraUnreachableError(self.base_url, cause=exc) from exc

        if expect_status and not response.ok:
            # Read the body even when streaming: an error response is a short
            # JSON body carrying Canon's own reason string, and skipping it
            # discards the only useful diagnostic.
            try:
                body = response.text
            except Exception:
                body = ""
            if response.status_code == 401:
                raise CCAPIAuthError(self.base_url)
            if response.status_code == 503:
                raise CCAPIBusyError(url, body.strip()[:200])
            raise CCAPIResponseError(method.upper(), url, response.status_code, body)
        return response

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        """GET ``path``."""
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> requests.Response:
        """POST to ``path``."""
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> requests.Response:
        """PUT to ``path``."""
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> requests.Response:
        """DELETE ``path``."""
        return self.request("DELETE", path, **kwargs)

    def get_json(self, path: str, **kwargs: Any) -> Any:
        """GET ``path`` and parse the response as JSON.

        Raises:
            CCAPIResponseError: If the body is not valid JSON, which usually
                means something other than a camera owns this address.
        """
        response = self.get(path, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise CCAPIResponseError(
                "GET",
                self.url_for(path),
                response.status_code,
                f"Expected JSON but got {response.headers.get('Content-Type', 'unknown')}: "
                f"{response.text[:200]}",
            ) from exc

    @contextlib.contextmanager
    def stream(self, path: str, **kwargs: Any) -> Iterator[requests.Response]:
        """Open a long-lived streaming GET, closing it on the way out.

        A leaked socket can leave the camera believing a live-view session is
        still open.
        """
        response = self.request(
            "GET", path, stream=True, timeout_s=self.liveview_timeout_s, **kwargs
        )
        try:
            yield response
        finally:
            response.close()
