"""Pulling the live-view stream off the camera.

CCAPI offers three ways to get live-view frames, and this module prefers them
in the order that costs the least complexity:

1. ``multipart`` -- standard ``multipart/x-mixed-replace`` MJPEG. Ordinary
   format, no bespoke framing. This is what we want.
2. ``scroll`` -- a continuous stream in Canon's own binary framing. Supported
   here as a fallback for bodies that lack the multipart endpoint.
3. ``flip`` -- one JPEG per request. Slower and chattier, but it works
   everywhere and is the honest last resort.

Live view is *started by choosing an output size*, not by a bare "start" call,
and is stopped by setting that size back to off. There is no DELETE for it.

This module returns encoded JPEG bytes and never decodes them. Decoding and
inversion belong to ``csfs.processing``; keeping the split means the frame pump
can be tested without OpenCV in the picture.
"""

from __future__ import annotations

import logging
import struct
import time
from typing import Iterator

from csfs.ccapi.client import CCAPIClient
from csfs.ccapi.discovery import Capabilities
from csfs.ccapi.endpoints import (
    LIVEVIEW_SIZE_OFF,
    LIVEVIEW_SIZE_PREFERENCE,
    LIVEVIEW_STREAM_PREFERENCE,
)
from csfs.ccapi.errors import CCAPIError, UnsupportedEndpointError

logger = logging.getLogger(__name__)

# JPEG start-of-image and end-of-image markers.
_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"

# Canon's binary live-view framing: marker, data-type, then a 4-byte length.
_SCROLL_HEADER = b"\xff\x00\x00"
_SCROLL_HEADER_LEN = len(_SCROLL_HEADER) + 4

# Guard against a desynchronised stream eating all available memory. A live-view
# JPEG is far smaller than this; anything bigger means we have lost framing.
_MAX_BUFFER_BYTES = 16 * 1024 * 1024


class MultipartJPEGParser:
    """Incremental parser for a ``multipart/x-mixed-replace`` JPEG stream.

    Feed it arbitrary chunks; it yields complete JPEG frames.

    It uses ``Content-Length`` when the part declares one and falls back to
    scanning for the next boundary when it does not. Header lines may end with
    either LF or CRLF -- CCAPI documents LF, but tolerating both costs nothing
    and saves an obscure debugging session if a body differs.
    """

    def __init__(self, boundary: str = "boundary") -> None:
        self._delimiter = b"--" + boundary.strip().strip('"').encode("ascii")
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        """Add received bytes and yield any frames they complete."""
        self._buffer.extend(chunk)
        if len(self._buffer) > _MAX_BUFFER_BYTES:
            # Lost sync. Drop everything before the last plausible frame start
            # rather than growing without bound.
            logger.warning("Live-view buffer exceeded %d bytes; resynchronising.", _MAX_BUFFER_BYTES)
            marker = self._buffer.rfind(_SOI)
            del self._buffer[: marker if marker > 0 else len(self._buffer)]

        while True:
            frame = self._next_frame()
            if frame is None:
                return
            yield frame

    def _next_frame(self) -> bytes | None:
        """Extract one frame, or return None if more data is needed."""
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

        # No Content-Length: the frame runs to the next boundary.
        next_boundary = self._buffer.find(self._delimiter, body_start)
        if next_boundary < 0:
            return None
        frame = bytes(self._buffer[body_start:next_boundary]).strip(b"\r\n")
        del self._buffer[:next_boundary]
        return frame or None


def _find_header_end(buffer: bytearray, start: int) -> tuple[int, int]:
    """Locate the blank line ending a part's headers.

    Returns ``(header_end, body_start)``, or ``(-1, -1)`` if incomplete.
    """
    for terminator in (b"\r\n\r\n", b"\n\n"):
        index = buffer.find(terminator, start)
        if index >= 0:
            return index, index + len(terminator)
    return -1, -1


def _content_length(headers: bytes) -> int | None:
    """Parse a Content-Length header out of a part's headers, if present."""
    for line in headers.split(b"\n"):
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


class JPEGStreamParser:
    """Incremental parser for a stream of bare, concatenated JPEGs.

    This is what the ``scroll`` endpoint delivers: JPEG data split across HTTP
    chunks with no framing of Canon's own on top. HTTP's chunked transfer
    encoding is handled by the HTTP library, so what arrives here is simply one
    JPEG after another.

    Frames are therefore delimited by the JPEG markers themselves -- start of
    image, then end of image.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        """Add received bytes and yield any frames they complete."""
        self._buffer.extend(chunk)
        if len(self._buffer) > _MAX_BUFFER_BYTES:
            logger.warning("Live-view buffer overran; resynchronising on the next frame.")
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
    """Incremental parser for Canon's binary live-view framing.

    Used by the ``flipdetail`` and ``scrolldetail`` endpoints, which wrap the
    image (and optional incidental information) in a header of a marker, a data
    type, and a 4-byte length, followed by an end marker.

    Not used by the default live-view path. It is kept because the detail
    endpoints carry the AF-frame and exposure information later milestones may
    want, and because ``scroll`` and ``scrolldetail`` are easy to confuse --
    only the latter has this framing.

    UNVERIFIED: the byte order of the length field is not confirmed against real
    hardware. Big-endian is tried first, and a length that does not yield a JPEG
    falls back to scanning for the markers, so the parser works either way.
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

        # Declared length did not yield a JPEG. Fall back to marker scanning.
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

    Use as a context manager so the session is always closed -- leaving live
    view running holds camera resources and drains the battery.

    Example:
        >>> with LiveView(client, caps) as lv:  # doctest: +SKIP
        ...     for jpeg in lv.frames():
        ...         ...
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
            size: Live-view output size. Defaults to the largest on offer,
                since more pixels means better focus confirmation.
            camera_display: Whether the camera's own screen stays on. Left as
                "keep" by default -- silently changing the camera's display is
                surprising, and the user may want it as a second monitor.
        """
        self._client = client
        self._caps = capabilities
        self._size = size or LIVEVIEW_SIZE_PREFERENCE[0]
        self._camera_display = camera_display
        self._started = False

    # -- session lifecycle --------------------------------------------------

    def start(self) -> None:
        """Begin live-view output at the configured size."""
        self._client.post(
            self._caps.path("liveview"),
            json={"liveviewsize": self._size, "cameradisplay": self._camera_display},
        )
        self._started = True
        logger.info("Live view started at size '%s'.", self._size)

    def stop(self) -> None:
        """Stop live-view output.

        Never raises: this runs on the way out of a context manager, often while
        another error is already propagating, and masking that error with a
        secondary failure would hide the real problem.
        """
        if not self._started:
            return
        try:
            # Both fields must be sent. A real EOS R7 rejects a body carrying
            # only liveviewsize with 400 "Invalid parameter", so omitting
            # cameradisplay here left live view running on the camera.
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

    # -- frames -------------------------------------------------------------

    def stream_capability(self) -> str | None:
        """Which streaming endpoint this camera supports, if any."""
        for capability in LIVEVIEW_STREAM_PREFERENCE:
            if self._caps.has(capability):
                return capability
        return None

    def frames(self) -> Iterator[bytes]:
        """Yield JPEG frames until the caller stops consuming or the stream ends.

        Picks the best transport the camera offers and falls back to polling
        single frames if it offers no continuous stream at all.
        """
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
            # 'scroll' carries bare concatenated JPEGs, not Canon's binary
            # framing -- that belongs to 'scrolldetail'. Confusing the two
            # yields no frames at all rather than wrong ones.
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
        """Terminate a stream session the camera still believes is open.

        The camera answers 503 "Already started" if a previous run's stream was
        never closed -- a dropped connection or a killed process is enough. The
        endpoint's own DELETE clears that, and doing it up front turns an
        otherwise baffling failure into a non-event.

        Only attempted where the camera reports DELETE is supported, and never
        allowed to raise: this is a best-effort tidy-up before the real request.
        """
        endpoint = self._caps.resolved.get(capability)
        if endpoint is None or not endpoint.supports("delete"):
            return
        try:
            self._client.delete(path, expect_status=False)
        except CCAPIError as exc:
            logger.debug("Pre-emptive stream close failed (harmless): %s", exc)

    def poll_frames(self, interval_s: float = 0.1) -> Iterator[bytes]:
        """Yield frames by repeatedly requesting a single image.

        The universal fallback. Used when the body exposes no streaming
        endpoint, and available directly for a one-off frame grab.
        """
        while True:
            frame = self.single_frame()
            if frame is not None:
                yield frame
            time.sleep(interval_s)

    def single_frame(self) -> bytes | None:
        """Fetch exactly one live-view frame, or None if not available yet.

        Returns None rather than raising on a 503: a camera briefly showing its
        menu, or one that has just started live view, is not an error state --
        Canon's reference explicitly says to retry.
        """
        try:
            path = self._caps.path("liveview_flip")
        except UnsupportedEndpointError:
            return None
        response = self._client.get(path, expect_status=False)
        if response.status_code == 503:
            return None
        if not response.ok:
            return None
        return response.content


def _boundary_from_content_type(content_type: str) -> str | None:
    """Extract the multipart boundary token from a Content-Type header."""
    for part in content_type.split(";"):
        name, _, value = part.partition("=")
        if name.strip().lower() == "boundary":
            return value.strip().strip('"') or None
    return None
