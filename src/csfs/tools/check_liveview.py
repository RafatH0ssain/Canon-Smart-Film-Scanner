"""Verify the live-view path against a real camera.

Bodies differ in which live-view transports they offer. Notably, not every
camera has the standard multipart MJPEG endpoint, and the ones that don't fall
back to Canon's own binary framing -- whose 4-byte length field this project
could not confirm the byte order of from the reference alone.

This tool answers that empirically:

    python -m csfs.tools.check_liveview

It reports which transport will be used, inspects the raw framing byte by byte
if that transport is the binary one, then pulls frames through the real parser
and decodes them. Output is safe to paste into a bug report -- it contains
frame sizes and endpoint paths, no image content and no camera address.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

from csfs.ccapi import CCAPIClient, discover
from csfs.ccapi.errors import CCAPIError
from csfs.ccapi.liveview import LiveView
from csfs.config import load_config
from csfs.processing.codec import DecodeError, decode_jpeg

_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"
_SCROLL_HEADER = b"\xff\x00\x00"


def _analyse_framing(raw: bytes) -> None:
    """Report how the chunked live-view stream is actually framed.

    Two shapes are possible and are easy to confuse. The ``scroll`` endpoint
    sends bare, concatenated JPEGs with no framing of Canon's own; the
    ``scrolldetail`` endpoint wraps each frame in a marker, data type and 4-byte
    length. This measures which one arrived, and for the framed shape settles
    the byte order of the length field by comparing it against the JPEG's real
    length as measured from its markers.
    """
    print("\nSTREAM FRAMING ANALYSIS")
    print(f"  captured {len(raw)} raw bytes")
    print(f"  first 16 bytes: {raw[:16].hex(' ')}")

    if raw.startswith(_SOI):
        print("  => Stream begins with a JPEG start-of-image marker.")
        print("     FORMAT: bare concatenated JPEGs, no Canon framing.")
        print("     This matches the expectation for 'scroll'; JPEGStreamParser handles it.")
        count = raw.count(_SOI)
        print(f"     start-of-image markers in the sample: {count}")
        return

    start = raw.find(_SCROLL_HEADER)
    if start < 0:
        print("  Neither a JPEG marker nor a 0xFF 0x00 0x00 header at the start.")
        print("  The framing differs from anything this project expects -- please report")
        print("  the byte dump above.")
        return

    print("  => Stream uses Canon's binary framing (marker + type + length).")

    print(f"  header found at offset {start}")
    size_field = raw[start + 3 : start + 7]
    if len(size_field) < 4:
        print("  Not enough data after the header to read the length field.")
        return

    (big,) = struct.unpack(">I", size_field)
    (little,) = struct.unpack("<I", size_field)
    print(f"  length field bytes : {size_field.hex(' ')}")
    print(f"    as big-endian    : {big}")
    print(f"    as little-endian : {little}")

    body = start + 7
    if raw[body : body + 2] == _SOI:
        print(f"  JPEG starts immediately after the header (offset {body}) -- as expected")
    else:
        soi = raw.find(_SOI, start)
        print(f"  JPEG does NOT start at offset {body}; first SOI at {soi}")
        body = soi if soi >= 0 else body

    eoi = raw.find(_EOI, body)
    if eoi < 0:
        print("  No end-of-image marker in the captured bytes; frame is larger than the sample.")
        return

    actual = eoi + 2 - body
    print(f"  measured JPEG length: {actual}")
    if actual == big:
        print("  => VERDICT: the length field is BIG-endian. Matches the current parser.")
    elif actual == little:
        print("  => VERDICT: the length field is LITTLE-endian.")
        print("     The parser recovers via marker scanning, so live view still works,")
        print("     but ScrollFrameParser in csfs/ccapi/liveview.py should be updated.")
    else:
        print("  => VERDICT: neither interpretation matches the measured length.")
        print("     The parser falls back to marker scanning, which is why frames still")
        print("     decode. Worth reporting with this output.")


def main(argv: list[str] | None = None) -> int:
    """Check the live-view path. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=None, help="Camera base URL.")
    parser.add_argument("--frames", type=int, default=5, help="How many frames to pull.")
    parser.add_argument(
        "--save", default=None, help="Write the first decoded frame to this path."
    )
    args = parser.parse_args(argv)

    config = load_config()
    base_url = args.base_url or config.camera.base_url

    print(f"Querying {base_url} ...")
    try:
        with CCAPIClient(
            base_url,
            timeout_s=config.camera.timeout_s,
            liveview_timeout_s=config.camera.liveview_timeout_s,
            username=config.camera.username,
            password=config.camera.password,
        ) as client:
            caps = discover(client)
            return _run(client, caps, args)
    except CCAPIError as exc:
        print(f"\nCould not check live view.\n\n{exc}", file=sys.stderr)
        return 1


def _run(client: CCAPIClient, caps, args) -> int:  # noqa: ANN001
    print("\nAVAILABLE LIVE-VIEW ENDPOINTS")
    for capability in (
        "liveview", "liveview_flip", "liveview_multipart",
        "liveview_scroll", "liveview_flipdetail", "liveview_scrolldetail",
    ):
        mark = "yes" if caps.has(capability) else " no"
        path = caps.path(capability) if caps.has(capability) else "-"
        print(f"  [{mark}] {capability:<22} {path}")

    live = LiveView(client, caps)
    chosen = live.stream_capability()
    print(f"\nCHOSEN TRANSPORT: {chosen or 'none -- will poll single frames'}")
    if chosen == "liveview_scroll":
        print("  This body has no multipart endpoint, so the chunked stream is the")
        print("  primary path rather than a fallback. Analysing its framing below.")

    live.start()
    try:
        # A single frame first: the cheapest possible proof the stream works at
        # all, and it isolates 'live view is broken' from 'the parser is broken'.
        print("\nSINGLE FRAME (flip)")
        single = live.single_frame()
        if single is None:
            print("  No frame yet. This is normal immediately after starting live view;")
            print("  the camera may also be showing a menu. Retrying once...")
            single = live.single_frame()
        if single is None:
            print("  Still no frame. Take the camera off any menu screen and retry.")
        else:
            image = decode_jpeg(single)
            print(f"  ok: {len(single)} bytes, {image.shape[1]}x{image.shape[0]}")

        if chosen == "liveview_scroll":
            path = caps.path("liveview_scroll")
            # Clear any session a previous run left open, or the camera answers
            # 503 "Already started" and the analysis never happens.
            client.delete(path, expect_status=False)
            raw = b""
            try:
                with client.stream(path) as response:
                    print(f"\n  stream Content-Type: {response.headers.get('Content-Type')}")
                    for chunk in response.iter_content(chunk_size=8192):
                        raw += chunk
                        if len(raw) > 200_000:
                            break
            except CCAPIError as exc:
                print(f"\n  Could not open the chunked stream: {exc}")
            finally:
                client.delete(path, expect_status=False)
            if raw:
                _analyse_framing(raw)

        print(f"\nPULLING {args.frames} FRAMES THROUGH THE PARSER")
        decoded = 0
        first = None
        for frame in live.frames():
            try:
                image = decode_jpeg(frame)
            except DecodeError as exc:
                print(f"  frame {decoded + 1}: FAILED to decode ({exc})")
                continue
            decoded += 1
            if first is None:
                first = image
            print(f"  frame {decoded}: {len(frame)} bytes, {image.shape[1]}x{image.shape[0]}")
            if decoded >= args.frames:
                break

        if decoded == 0:
            print("\n  RESULT: no frames decoded. Live view is not usable on this path.")
            return 1

        print(f"\n  RESULT: {decoded}/{args.frames} frames decoded successfully.")
        if args.save and first is not None:
            import cv2

            target = Path(args.save)
            target.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(target), first)
            print(f"  first frame written to {target}")
        return 0
    finally:
        live.stop()
        print("\nLive view stopped.")


if __name__ == "__main__":
    raise SystemExit(main())
