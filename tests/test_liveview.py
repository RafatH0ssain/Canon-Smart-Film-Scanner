"""Tests for the live-view transport.

The parsers are tested directly on synthetic byte streams, including the awkward
cases that only show up over a real socket -- frames split across chunk
boundaries, headers arriving one byte at a time -- and then end to end against
the mock camera.
"""

from __future__ import annotations

import struct

import pytest

from csfs.ccapi import CCAPIClient, discover
from csfs.ccapi.liveview import (
    JPEGStreamParser,
    LiveView,
    LiveViewDetailParser,
    MultipartJPEGParser,
    _boundary_from_content_type,
)
from csfs.processing.codec import decode_jpeg, encode_jpeg
from csfs.mock.frames import make_negative

JPEG_A = encode_jpeg(make_negative(width=64, height=48, seed=1))
JPEG_B = encode_jpeg(make_negative(width=64, height=48, seed=2))


def _multipart(payloads: list[bytes], boundary: str = "boundary",
               with_length: bool = True, newline: bytes = b"\n") -> bytes:
    out = b""
    for payload in payloads:
        headers = b"Content-Type: image/jpeg" + newline
        if with_length:
            headers += b"Content-Length: " + str(len(payload)).encode() + newline
        out += b"--" + boundary.encode() + newline + headers + newline + payload + newline
    return out


# ---------------------------------------------------------------------------
# Multipart parsing
# ---------------------------------------------------------------------------


def test_multipart_parses_consecutive_frames() -> None:
    parser = MultipartJPEGParser()
    frames = list(parser.feed(_multipart([JPEG_A, JPEG_B])))
    assert frames == [JPEG_A, JPEG_B]


def test_multipart_handles_frames_split_across_chunks() -> None:
    """A frame will not arrive in one read; that is the normal case, not an edge case."""
    stream = _multipart([JPEG_A, JPEG_B])
    parser = MultipartJPEGParser()
    frames = []
    for i in range(0, len(stream), 7):  # deliberately awkward chunk size
        frames.extend(parser.feed(stream[i : i + 7]))
    assert frames == [JPEG_A, JPEG_B]


def test_multipart_handles_one_byte_at_a_time() -> None:
    stream = _multipart([JPEG_A])
    parser = MultipartJPEGParser()
    frames = []
    for i in range(len(stream)):
        frames.extend(parser.feed(stream[i : i + 1]))
    assert frames == [JPEG_A]


def test_multipart_without_content_length_falls_back_to_boundary_scan() -> None:
    parser = MultipartJPEGParser()
    stream = _multipart([JPEG_A, JPEG_B], with_length=False)
    # The final frame has no following boundary, so only the first completes.
    assert list(parser.feed(stream)) == [JPEG_A]


def test_multipart_accepts_crlf_headers() -> None:
    """CCAPI documents LF, but tolerating CRLF costs nothing."""
    parser = MultipartJPEGParser()
    assert list(parser.feed(_multipart([JPEG_A], newline=b"\r\n"))) == [JPEG_A]


def test_multipart_respects_a_custom_boundary() -> None:
    parser = MultipartJPEGParser("myboundary")
    assert list(parser.feed(_multipart([JPEG_A], boundary="myboundary"))) == [JPEG_A]


def test_multipart_yields_nothing_for_a_partial_frame() -> None:
    parser = MultipartJPEGParser()
    partial = _multipart([JPEG_A])[:-20]
    assert list(parser.feed(partial)) == []


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("multipart/x-mixed-replace;boundary=boundary", "boundary"),
        ("multipart/x-mixed-replace; boundary=abc123", "abc123"),
        ('multipart/x-mixed-replace; boundary="quoted"', "quoted"),
        ("image/jpeg", None),
        ("", None),
    ],
)
def test_boundary_extraction(header: str, expected: str | None) -> None:
    assert _boundary_from_content_type(header) == expected


# ---------------------------------------------------------------------------
# Bare JPEG stream -- what 'scroll' actually delivers
# ---------------------------------------------------------------------------


def test_jpeg_stream_parser_splits_concatenated_frames() -> None:
    """'scroll' sends bare JPEGs back to back, delimited only by their markers."""
    assert list(JPEGStreamParser().feed(JPEG_A + JPEG_B)) == [JPEG_A, JPEG_B]


def test_jpeg_stream_parser_handles_split_chunks() -> None:
    stream = JPEG_A + JPEG_B
    parser = JPEGStreamParser()
    frames = []
    for i in range(0, len(stream), 11):
        frames.extend(parser.feed(stream[i : i + 11]))
    assert frames == [JPEG_A, JPEG_B]


def test_jpeg_stream_parser_waits_for_a_complete_frame() -> None:
    parser = JPEGStreamParser()
    assert list(parser.feed(JPEG_A[:-10])) == []
    assert list(parser.feed(JPEG_A[-10:])) == [JPEG_A]


def test_jpeg_stream_parser_ignores_leading_junk() -> None:
    """Resynchronises on the next start-of-image rather than giving up."""
    assert list(JPEGStreamParser().feed(b"\x00\x01\x02" + JPEG_A)) == [JPEG_A]


# ---------------------------------------------------------------------------
# Canon binary framing -- used by the 'detail' endpoints, not by 'scroll'
# ---------------------------------------------------------------------------


def test_detail_parser_reads_declared_lengths() -> None:
    stream = b""
    for payload in (JPEG_A, JPEG_B):
        stream += b"\xff\x00\x00" + struct.pack(">I", len(payload)) + payload + b"\xff\xff"
    assert list(LiveViewDetailParser().feed(stream)) == [JPEG_A, JPEG_B]


def test_detail_parser_survives_the_wrong_byte_order() -> None:
    """The length field's endianness is unconfirmed, so this must not be fatal.

    With a little-endian length the declared size is nonsense, and the parser has
    to recover by scanning for the JPEG markers instead.
    """
    stream = b"\xff\x00\x00" + struct.pack("<I", len(JPEG_A)) + JPEG_A + b"\xff\xff"
    frames = list(LiveViewDetailParser().feed(stream))
    assert len(frames) == 1
    assert decode_jpeg(frames[0]).shape == (48, 64, 3)


def test_detail_parser_handles_split_chunks() -> None:
    stream = b"\xff\x00\x00" + struct.pack(">I", len(JPEG_A)) + JPEG_A + b"\xff\xff"
    parser = LiveViewDetailParser()
    frames = []
    for i in range(0, len(stream), 13):
        frames.extend(parser.feed(stream[i : i + 13]))
    assert frames == [JPEG_A]


# ---------------------------------------------------------------------------
# End to end against the mock camera
# ---------------------------------------------------------------------------


def test_liveview_streams_decodable_frames(client: CCAPIClient) -> None:
    caps = discover(client)
    with LiveView(client, caps) as live:
        assert live.stream_capability() == "liveview_multipart"
        frames = []
        for frame in live.frames():
            frames.append(frame)
            if len(frames) >= 3:
                break
    assert len(frames) == 3
    for frame in frames:
        assert decode_jpeg(frame).shape == (640, 960, 3)


def test_liveview_stops_on_context_exit(client: CCAPIClient) -> None:
    """Leaving live view running holds camera resources and drains the battery."""
    caps = discover(client)
    with LiveView(client, caps):
        assert client.get(caps.path("liveview_flip"), expect_status=False).status_code == 200
    assert client.get(caps.path("liveview_flip"), expect_status=False).status_code == 503


def test_liveview_single_frame_returns_none_when_not_started(client: CCAPIClient) -> None:
    """A 503 means 'retry', not 'fail' -- the camera may just be showing a menu."""
    caps = discover(client)
    assert LiveView(client, caps).single_frame() is None


def test_liveview_size_is_requested(client: CCAPIClient) -> None:
    caps = discover(client)
    with LiveView(client, caps, size="small") as live:
        frame = live.single_frame()
    assert frame is not None
    assert decode_jpeg(frame).shape == (424, 640, 3)


# ---------------------------------------------------------------------------
# Bodies without the multipart endpoint (e.g. EOS R7)
# ---------------------------------------------------------------------------


@pytest.fixture
def r7_client(client: CCAPIClient, mock_camera_url: str) -> CCAPIClient:
    """The mock configured like a real EOS R7: no multipart, no camera zoom."""
    import requests

    requests.post(f"{mock_camera_url}/__mock/body-profile", json={"profile": "r7"}, timeout=5)
    return client


def test_r7_shaped_body_has_no_multipart_endpoint(r7_client: CCAPIClient) -> None:
    caps = discover(r7_client)
    assert not caps.has("liveview_multipart")
    assert caps.has("liveview_scroll")


def test_r7_shaped_body_falls_back_to_binary_framing(r7_client: CCAPIClient) -> None:
    """On these bodies Canon's binary framing is the primary path, not a fallback."""
    caps = discover(r7_client)
    assert LiveView(r7_client, caps).stream_capability() == "liveview_scroll"


def test_r7_shaped_body_streams_decodable_frames(r7_client: CCAPIClient) -> None:
    """The end-to-end proof that the binary path actually works."""
    caps = discover(r7_client)
    with LiveView(r7_client, caps) as live:
        frames = []
        for frame in live.frames():
            frames.append(frame)
            if len(frames) >= 3:
                break
    assert len(frames) == 3
    for frame in frames:
        assert decode_jpeg(frame).shape == (640, 960, 3)


def test_stale_stream_session_is_cleared_before_streaming(
    r7_client: CCAPIClient, mock_camera_url: str
) -> None:
    """A crashed client leaves the camera answering 503 "Already started".

    Recovering automatically turns a baffling failure into a non-event.
    """
    import requests

    caps = discover(r7_client)
    live = LiveView(r7_client, caps)
    live.start()
    try:
        # Simulate a leftover session from a previous process.
        requests.get(
            f"{mock_camera_url}{caps.path('liveview_scroll')}", stream=True, timeout=5
        )
        frames = []
        for frame in live.frames():
            frames.append(frame)
            if len(frames) >= 2:
                break
    finally:
        live.stop()
    assert len(frames) == 2


def test_r7_shaped_body_hides_camera_zoom(r7_client: CCAPIClient) -> None:
    """No lvzoom means the UI must not offer a control that does nothing."""
    caps = discover(r7_client)
    assert not caps.has("lv_zoom")


def test_body_with_no_stream_endpoints_polls_single_frames(client: CCAPIClient) -> None:
    """The universal fallback: some bodies may offer neither continuous stream."""
    caps = discover(client)
    caps.resolved.pop("liveview_multipart", None)
    caps.resolved.pop("liveview_scroll", None)

    live = LiveView(client, caps)
    assert live.stream_capability() is None

    live.start()
    try:
        frames = []
        for frame in live.frames():
            frames.append(frame)
            if len(frames) >= 2:
                break
    finally:
        live.stop()
    assert len(frames) == 2
    assert decode_jpeg(frames[0]).shape == (640, 960, 3)
