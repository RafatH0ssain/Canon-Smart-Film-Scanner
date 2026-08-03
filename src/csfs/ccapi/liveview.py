"""Pulling the live-view stream off the camera.

Three transports, preferred in this order: ``multipart`` (standard MJPEG),
``scroll`` (bare concatenated JPEGs over a chunked response), and ``flip``
(one JPEG per request) as a universal fallback.

Live view starts by choosing an output size and stops by setting that size to
off; there is no DELETE for it. This module deals only in encoded JPEG bytes --
decoding belongs to ``csfs.processing``.
"""

from __future__ import annotations

import logging
import struct
import time
from collections.abc import Iterator

from csfs.ccapi.client import CCAPIClient
from csfs.ccapi.discovery import Capabilities
from csfs.ccapi.endpoints import (
    LIVEVIEW_SIZE_OFF,
    LIVEVIEW_SIZE_PREFERENCE,
    LIVEVIEW_STREAM_PREFERENCE,
)
from csfs.ccapi.errors import CCAPIError, UnsupportedEndpointError

logger = logging.getLogger(__name__)

_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"

# Canon's binary framing: marker, data type, then a 4-byte length.
_SCROLL_HEADER = b"\xff\x00\x00"
_SCROLL_HEADER_LEN = len(_SCROLL_HEADER) + 4

# A live-view JPEG is far smaller than this; anything larger means lost framing.
_MAX_BUFFER_BYTES = 16 * 1024 * 1024


class MultipartJPEGParser:
    """Incremental parser for a ``multipart/x-mixed-replace`` JPEG stream.

    Uses ``Content-Length`` when a part declares one, otherwise scans for the
    next boundary. Accepts LF or CRLF header endings.
    """

    def __init__(self, boundary: str = "boundary") -> None:
        self._delimiter = b"--" + boundary.strip().strip('"').encode("ascii")
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        """Add received bytes and yield any frames they complete."""
        self._buffer.extend(chunk)
        if len(self._buffer) > _MAX_BUFFER_BYTES:
            logger.warning("Live-view buffer overran; resynchronising.")
            marker = self._buffer.rfind(_SOI)
            del self._buffer[: marker if marker > 0 else len(self._buffer)]

        while True:
            frame = self._next_frame()
            if frame is None:
                return
            yield frame

    def _next_frame(self) -> bytes | None:
        start = self._buffer.find(self._delimiter)
        if start < 0:
            return None

        header_start = start + len(self._delimiter)
        header_end, body_start = _find_header_end(self._buffer, header_start)
        if header_end < 0:
            return None

        headers = bytes(self._buffer[header_start:header_end])
        length = _content_length(headers)

        if length is not None:
            end = body_start + length
            if len(self._buffer) < end:
                return None
            frame = bytes(self._buffer[body_start:end])
            del self._buffer[:end]
            return frame

        next_boundary = self._buffer.find(self._delimiter, body_start)
        if next_boundary < 0:
            return None
        frame = bytes(self._buffer[body_start:next_boundary]).strip(b"\r\n")
        del self._buffer[:next_boundary]
        return frame or None


def _find_header_end(buffer: bytearray, start: int) -> tuple[int, int]:
    """Return ``(header_end, body_start)``, or ``(-1, -1)`` if incomplete."""
    for terminator in (b"\r\n\r\n", b"\n\n"):
        index = buffer.find(terminator, start)
        if index >= 0:
            return index, index + len(terminator)
    return -1, -1


def _content_length(headers: bytes) -> int | None:
    """Parse a Content-Length header, if present."""
    for line in headers.split(b"\n"):
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


class JPEGStreamParser:
    """Incremental parser for bare, concatenated JPEGs.

    What ``scroll`` delivers: chunked transfer encoding is handled by the HTTP
    library, leaving one JPEG after another, delimited only by their markers.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        """Add received bytes and yield any frames they complete."""
        self._buffer.extend(chunk)
        if len(self._buffer) > _MAX_BUFFER_BYTES:
            logger.warning("Live-view buffer overran; resynchronising.")
            marker = self._buffer.rfind(_SOI)
            del self._buffer[: marker if marker > 0 else len(self._buffer)]
        while True:
            frame = self._next_frame()
            if frame is None:
                return
            yield frame

    def _next_frame(self) -> bytes | None:
        soi = self._buffer.find(_SOI)
        if soi < 0:
            return None
        eoi = self._buffer.find(_EOI, soi + len(_SOI))
        if eoi < 0:
            return None
        end = eoi + len(_EOI)
        frame = bytes(self._buffer[soi:end])
        del self._buffer[:end]
        return frame


class LiveViewDetailParser:
    """Incremental parser for the ``flipdetail`` / ``scrolldetail`` framing.

    Those endpoints wrap each image in a marker, data type and 4-byte length.
    Not used by the default path, but kept because they also carry AF-frame and
    exposure information.

    The byte order of the length field is unconfirmed against hardware;
    big-endian is tried first and a length that does not yield a JPEG falls back
    to marker scanning, so the parser works either way.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        """Add received bytes and yield any frames they complete."""
        self._buffer.extend(chunk)
        while True:
            frame = self._next_frame()
            if frame is None:
                return
            yield frame

    def _next_frame(self) -> bytes | None:
        start = self._buffer.find(_SCROLL_HEADER)
        if start < 0 or len(self._buffer) < start + _SCROLL_HEADER_LEN:
            return None

        size_at = start + len(_SCROLL_HEADER)
        (declared,) = struct.unpack(">I", bytes(self._buffer[size_at : size_at + 4]))
        body_start = size_at + 4

        if 0 < declared <= _MAX_BUFFER_BYTES:
            end = body_start + declared
            if len(self._buffer) < end:
                return None
            candidate = bytes(self._buffer[body_start:end])
            if candidate.startswith(_SOI):
                del self._buffer[:end]
                return candidate

        soi = self._buffer.find(_SOI, body_start)
        if soi < 0:
            return None
        eoi = self._buffer.find(_EOI, soi)
        if eoi < 0:
            return None
        frame = bytes(self._buffer[soi : eoi + len(_EOI)])
        del self._buffer[: eoi + len(_EOI)]
        return frame


class LiveView:
    """A live-view session on one camera.

    Use as a context manager; leaving live view running holds camera resources
    and drains the battery.
    """

    def __init__(
        self,
        client: CCAPIClient,
        capabilities: Capabilities,
        size: str | None = None,
        camera_display: str = "keep",
    ) -> None:
        """
        Args:
            client: Connected CCAPI client.
            capabilities: The camera's discovered capabilities.
            size: Output size; defaults to the largest available.
            camera_display: Whether the camera's own screen stays on. Left
                unchanged by default.
        """
        self._client = client
        self._caps = capabilities
        self._size = size or LIVEVIEW_SIZE_PREFERENCE[0]
        self._camera_display = camera_display
        self._started = False

    def start(self) -> None:
        """Begin live-view output at the configured size."""
        self._client.post(
            self._caps.path("liveview"),
            json={"liveviewsize": self._size, "cameradisplay": self._camera_display},
        )
        self._started = True
        logger.info("Live view started at size '%s'.", self._size)

    def stop(self) -> None:
        """Stop live-view output. Never raises."""
        if not self._started:
            return
        try:
            # Both fields are required; sending only liveviewsize gets a 400 and
            # leaves live view running.
            self._client.post(
                self._caps.path("liveview"),
                json={
                    "liveviewsize": LIVEVIEW_SIZE_OFF,
                    "cameradisplay": self._camera_display,
                },
            )
        except CCAPIError as exc:
            logger.warning("Could not stop live view cleanly: %s", exc)
        finally:
            self._started = False

    def __enter__(self) -> LiveView:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def stream_capability(self) -> str | None:
        """Which streaming endpoint this camera supports, if any."""
        for capability in LIVEVIEW_STREAM_PREFERENCE:
            if self._caps.has(capability):
                return capability
        return None

    def frames(self) -> Iterator[bytes]:
        """Yield JPEG frames until the caller stops consuming or the stream ends."""
        capability = self.stream_capability()
        if capability is None:
            logger.info("No streaming endpoint; polling single frames instead.")
            yield from self.poll_frames()
            return

        path = self._caps.path(capability)
        self._clear_stale_stream(capability, path)

        parser: MultipartJPEGParser | JPEGStreamParser | LiveViewDetailParser
        if capability == "liveview_multipart":
            parser = MultipartJPEGParser()
        elif capability.endswith("detail"):
            parser = LiveViewDetailParser()
        else:
            # 'scroll' is bare JPEGs; the binary framing belongs to 'scrolldetail'.
            parser = JPEGStreamParser()

        with self._client.stream(path) as response:
            if capability == "liveview_multipart":
                boundary = _boundary_from_content_type(response.headers.get("Content-Type", ""))
                if boundary:
                    parser = MultipartJPEGParser(boundary)
            for chunk in response.iter_content(chunk_size=16384):
                if not chunk:
                    continue
                yield from parser.feed(chunk)

    def _clear_stale_stream(self, capability: str, path: str) -> None:
        """Close a stream session the camera still believes is open.

        A dropped connection or killed process leaves the camera answering 503
        "Already started" until the endpoint's DELETE is called. Best effort.
        """
        endpoint = self._caps.resolved.get(capability)
        if endpoint is None or not endpoint.supports("delete"):
            return
        try:
            self._client.delete(path, expect_status=False)
        except CCAPIError as exc:
            logger.debug("Pre-emptive stream close failed: %s", exc)

    def poll_frames(self, interval_s: float = 0.1) -> Iterator[bytes]:
        """Yield frames by repeatedly requesting a single image."""
        while True:
            frame = self.single_frame()
            if frame is not None:
                yield frame
            time.sleep(interval_s)

    def single_frame(self) -> bytes | None:
        """Fetch one live-view frame, or None if not available yet.

        A 503 means retry, not failure: the camera may be showing a menu or may
        have only just started live view.
        """
        try:
            path = self._caps.path("liveview_flip")
        except UnsupportedEndpointError:
            return None
        response = self._client.get(path, expect_status=False)
        if response.status_code == 503 or not response.ok:
            return None
        return response.content


def _boundary_from_content_type(content_type: str) -> str | None:
    """Extract the multipart boundary token from a Content-Type header."""
    for part in content_type.split(";"):
        name, _, value = part.partition("=")
        if name.strip().lower() == "boundary":
            return value.strip().strip('"') or None
    return None
