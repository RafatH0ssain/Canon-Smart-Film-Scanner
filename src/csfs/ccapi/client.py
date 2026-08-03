"""Base HTTP session for talking to a CCAPI camera.

This module is intentionally dumb. It does transport and error translation --
turning a ``requests`` exception into something that names the real-world cause
-- and nothing else. Knowledge of *which* URL means *what* lives in
``discovery.py``; knowledge of what to do with the bytes lives further up.
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any, Iterator
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
        base_url: Root of the camera's CCAPI service, e.g.
            ``"http://192.168.1.50:8080"``. No trailing path.
        timeout_s: Read/connect timeout for ordinary control calls.
        liveview_timeout_s: Read timeout for long-lived streaming requests.
        session: Optional pre-built ``requests.Session``, mainly for tests.
        username: CCAPI account name, if one is registered on the camera.
        password: That account's password.

    Credentials are only needed when an account has been registered on the
    camera under its CCAPI account settings; CCAPI then uses HTTP digest
    authentication. Leave them unset for the usual no-account setup.

    Example:
        >>> with CCAPIClient("http://127.0.0.1:8080") as client:  # doctest: +SKIP
        ...     caps = discover(client)
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
        # Strip the trailing slash so urljoin() semantics stay predictable when
        # we later join discovered absolute paths onto it.
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.liveview_timeout_s = liveview_timeout_s
        self._session = session or requests.Session()
        self._owns_session = session is None

        # A second session used only for long-lived streaming requests.
        #
        # requests.Session is not thread-safe, and the live-view pump holds a
        # streaming response open for the entire session while control calls run
        # on other threads. Sharing one session between them corrupted state and
        # produced indefinite hangs on a real camera.
        self._stream_session = requests.Session()

        # Serialises control requests. The camera accepts one client and does not
        # cope with concurrent requests while streaming: several in flight at once
        # left threads blocked in socket reads for minutes. Streaming requests
        # hold this only until response headers arrive, never while the body is
        # being consumed, so a live-view stream cannot block control calls.
        self._request_lock = threading.Lock()

        # Digest, not basic: CCAPI hashes the credentials rather than sending
        # them in the clear, and requests handles the challenge/response for us.
        if username:
            auth = HTTPDigestAuth(username, password)
            self._session.auth = auth
            self._stream_session.auth = HTTPDigestAuth(username, password)

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP sessions."""
        self._stream_session.close()
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> CCAPIClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- request plumbing ---------------------------------------------------

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
            expect_status: Raise :class:`CCAPIResponseError` on a 4xx/5xx.
                Set ``False`` when a non-2xx is a meaningful answer, such as
                probing whether an endpoint exists at all.
            **kwargs: Passed through to ``requests``.

        Raises:
            CameraUnreachableError: The camera did not respond at all.
            CCAPIResponseError: The camera responded with an error status.
        """
        url = self.url_for(path)
        effective_timeout = timeout_s if timeout_s is not None else self.timeout_s
        session = self._stream_session if stream else self._session
        try:
            # The lock is released as soon as the call returns. For a streaming
            # request that is when the headers arrive, so consuming the body
            # afterwards leaves control calls free to proceed.
            with self._request_lock:
                response = session.request(
                    method, url, timeout=effective_timeout, stream=stream, **kwargs
                )
        except requests.exceptions.RequestException as exc:
            # Connection refused, DNS failure, timeout, cable pulled mid-roll.
            # All of these mean the same thing to the person at the copy stand.
            raise CameraUnreachableError(self.base_url, cause=exc) from exc

        if expect_status and not response.ok:
            # Read the body before raising: it often carries Canon's own reason
            # string, which is far more useful than the bare status code.
            # This applies to streaming requests too -- an error response is a
            # short JSON body, not a stream, and skipping it here threw away the
            # only useful diagnostic when a live-view stream was refused.
            try:
                body = response.text
            except Exception:  # noqa: BLE001 - diagnostics must never mask the real error
                body = ""
            # Translate the two statuses with a specific real-world cause into
            # errors that name it, rather than a bare status code the reader
            # then has to go and look up.
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
            CCAPIResponseError: If the body is not valid JSON. A camera that
                answers a CCAPI URL with HTML is usually a router or a captive
                portal at that address, not a camera -- worth saying plainly.
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
        """Open a long-lived streaming GET, e.g. the live-view feed.

        Yields the streaming response and always closes it, so a dropped
        connection cannot leak a socket and leave the camera believing a
        live-view session is still open.
        """
        response = self.request(
            "GET", path, stream=True, timeout_s=self.liveview_timeout_s, **kwargs
        )
        try:
            yield response
        finally:
            response.close()
