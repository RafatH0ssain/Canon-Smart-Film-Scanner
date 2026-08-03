"""A mock CCAPI camera.

Run it::

    python -m csfs.mock.mock_server

Then point ``camera.base_url`` at ``http://127.0.0.1:8080`` (which is already
the default) and the rest of the application cannot tell the difference for the
features it needs.

WHAT THIS IS AND IS NOT
-----------------------
This server defines its *own* endpoint paths. They are shaped to be plausible,
but they are not transcribed from Canon's reference and are not authoritative --
that document is licensed per-developer and cannot live in this repository.

That is deliberately safe, because the client never assumes a path: it reads the
endpoint list this server publishes, exactly as it reads the list a real camera
publishes (see ``csfs.ccapi.discovery``). The mock therefore exercises the real
discovery path rather than papering over it. If a real body names things
differently, discovery adapts and only ``csfs/ccapi/endpoints.py`` may need a
hint added.

It models the parts of a camera that matter for scanning: live view, shutter
with a settle delay, focus position, and a card holding captured files.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse

from csfs.mock.frames import make_negative

API_ROOT = "/ccapi"
VER = "ver110"
BASE = f"{API_ROOT}/{VER}"

# Live-view frame rate. Real bodies deliver roughly this over a good link, and
# matching it keeps the UI's latency behaviour honest during development.
LIVEVIEW_FPS = 15

# Boundary token for the multipart MJPEG stream.
_BOUNDARY = "csfsmockframe"


@dataclass
class CapturedFile:
    """One file on the mock camera's card."""

    name: str
    data: bytes
    created_at: float = field(default_factory=time.time)


@dataclass
class MockCameraState:
    """Everything the fake camera remembers between requests."""

    liveview_active: bool = False
    color_film: bool = True

    # Focus position in arbitrary steps. 0 is perfectly sharp; the magnitude
    # maps to blur radius, so driving focus in the UI visibly changes the image
    # and the sharpness metric responds -- which is the point of v0.2.
    focus_position: int = 0

    # "electronic" or "mechanical". v0.1 must select electronic to avoid
    # shutter shock on the copy stand.
    shutter_mode: str = "electronic"

    iso: str = "100"
    av: str = "8.0"
    tv: str = "1/125"
    white_balance: str = "daylight"

    files: list[CapturedFile] = field(default_factory=list)
    shutter_count: int = 0

    def focus_error(self) -> float:
        """Blur radius implied by the current focus position."""
        return abs(self.focus_position) / 4.0


state = MockCameraState()
app = FastAPI(title="Mock CCAPI Camera", docs_url="/__docs")


def _encode_jpeg(frame: np.ndarray, quality: int = 85) -> bytes:
    """Encode a BGR array as JPEG bytes."""
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise HTTPException(status_code=500, detail="Mock camera failed to encode a frame.")
    return buffer.tobytes()


def _liveview_frame() -> np.ndarray:
    """Render the current live-view frame.

    Live view on a real body is a reduced-resolution stream, so the mock matches
    that: the loupe in the UI must magnify a genuinely limited source, otherwise
    focus confirmation would look better in development than it does in reality.
    """
    return make_negative(
        width=960,
        height=640,
        color=state.color_film,
        phase=time.time() % (2 * math.pi),
        focus_error=state.focus_error(),
        seed=None,
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@app.get(API_ROOT + "/")
@app.get(API_ROOT)
def api_root() -> dict[str, str]:
    """List supported API versions, keyed by version name."""
    return {VER: f"{BASE}/"}


def _endpoint(path: str, **methods: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"path": path}
    entry.update({k: v for k, v in methods.items()})
    return entry


@app.get(BASE + "/")
@app.get(BASE)
def version_root() -> dict[str, list[dict[str, Any]]]:
    """List the endpoints this version supports.

    This is what makes the API self-describing, and what lets the client work
    across bodies that expose different subsets.
    """
    return {
        "deviceinformation": [_endpoint(f"{BASE}/deviceinformation", get=True)],
        "storage": [_endpoint(f"{BASE}/devicestatus/storage", get=True)],
        "battery": [_endpoint(f"{BASE}/devicestatus/battery", get=True)],
        "liveview": [_endpoint(f"{BASE}/shooting/liveview", get=True, post=True, delete=True)],
        "liveviewflip": [_endpoint(f"{BASE}/shooting/liveview/flip", get=True)],
        "liveviewscroll": [_endpoint(f"{BASE}/shooting/liveview/scroll", get=True)],
        "shutterbutton": [_endpoint(f"{BASE}/shooting/control/shutterbutton", post=True)],
        "drivefocus": [_endpoint(f"{BASE}/shooting/control/drivefocus", post=True)],
        "shootingsettings": [_endpoint(f"{BASE}/shooting/settings", get=True)],
        "iso": [_endpoint(f"{BASE}/shooting/settings/iso", get=True, put=True)],
        "av": [_endpoint(f"{BASE}/shooting/settings/av", get=True, put=True)],
        "tv": [_endpoint(f"{BASE}/shooting/settings/tv", get=True, put=True)],
        "wb": [_endpoint(f"{BASE}/shooting/settings/wb", get=True, put=True)],
        "shuttermode": [_endpoint(f"{BASE}/shooting/settings/shuttermode", get=True, put=True)],
        "contents": [_endpoint(f"{BASE}/contents", get=True)],
    }


@app.get(BASE + "/deviceinformation")
def device_information() -> dict[str, str]:
    """Identify the camera. Values are obviously fake on purpose."""
    return {
        "manufacturer": "Canon (mock)",
        "productname": "Mock CCAPI Body",
        "guid": "00000000-0000-0000-0000-000000000000",
        "serialnumber": "MOCK0000001",
        "macaddress": "00:00:00:00:00:00",
        "firmwareversion": "0.0.0",
    }


@app.get(BASE + "/devicestatus/storage")
def storage() -> dict[str, Any]:
    return {
        "storagelist": [
            {
                "name": "card1",
                "accesscapability": "readwrite",
                "maxsize": 64 * 1024**3,
                "spacesize": 64 * 1024**3 - sum(len(f.data) for f in state.files),
                "contentsnumber": len(state.files),
            }
        ]
    }


@app.get(BASE + "/devicestatus/battery")
def battery() -> dict[str, str]:
    return {"name": "LP-E6NH (mock)", "kind": "battery", "level": "full", "quality": "good"}


# ---------------------------------------------------------------------------
# Live view
# ---------------------------------------------------------------------------


@app.post(BASE + "/shooting/liveview")
def liveview_start(payload: dict[str, Any] | None = None) -> dict[str, str]:
    """Start a live-view session."""
    state.liveview_active = True
    return {"message": "ok"}


@app.delete(BASE + "/shooting/liveview")
def liveview_stop() -> dict[str, str]:
    """Stop the live-view session and free the camera."""
    state.liveview_active = False
    return {"message": "ok"}


@app.get(BASE + "/shooting/liveview")
def liveview_status() -> dict[str, Any]:
    return {"active": state.liveview_active, "fps": LIVEVIEW_FPS}


@app.get(BASE + "/shooting/liveview/flip")
def liveview_flip() -> Response:
    """Return exactly one live-view frame as a JPEG.

    Rejects the request when live view is not running, because a real body does
    too -- and code that forgets to start the session should fail in development
    rather than at the copy stand.
    """
    if not state.liveview_active:
        raise HTTPException(
            status_code=503,
            detail="Live view is not running. POST to the liveview endpoint first.",
        )
    return Response(content=_encode_jpeg(_liveview_frame()), media_type="image/jpeg")


async def _mjpeg_frames() -> AsyncIterator[bytes]:
    """Yield multipart MJPEG chunks until the client disconnects."""
    interval = 1.0 / LIVEVIEW_FPS
    while state.liveview_active:
        jpeg = await asyncio.to_thread(lambda: _encode_jpeg(_liveview_frame()))
        yield (
            f"--{_BOUNDARY}\r\n"
            f"Content-Type: image/jpeg\r\n"
            f"Content-Length: {len(jpeg)}\r\n\r\n"
        ).encode("ascii") + jpeg + b"\r\n"
        await asyncio.sleep(interval)


@app.get(BASE + "/shooting/liveview/scroll")
def liveview_scroll() -> StreamingResponse:
    """Continuous MJPEG live-view stream."""
    if not state.liveview_active:
        raise HTTPException(
            status_code=503,
            detail="Live view is not running. POST to the liveview endpoint first.",
        )
    return StreamingResponse(
        _mjpeg_frames(),
        media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
    )


# ---------------------------------------------------------------------------
# Shutter & focus
# ---------------------------------------------------------------------------


@app.post(BASE + "/shooting/control/shutterbutton")
def shutter_button(payload: dict[str, Any] | None = None) -> JSONResponse:
    """Fire the shutter and write a file to the fake card.

    The captured frame is rendered at a higher resolution than live view and
    with finer grain, mirroring the real gap between the preview stream and the
    file you actually scan from.
    """
    payload = payload or {}
    if payload.get("af") is True:
        # Autofocus on grain is unreliable; the app should not be requesting it.
        # Nudging focus here makes that mistake visible in the preview.
        state.focus_position += 1

    frame = make_negative(
        width=2400,
        height=1600,
        color=state.color_film,
        phase=time.time() % (2 * math.pi),
        focus_error=state.focus_error(),
        grain=0.012,
        seed=state.shutter_count,
    )
    state.shutter_count += 1
    name = f"IMG_{state.shutter_count:04d}.JPG"
    state.files.append(CapturedFile(name=name, data=_encode_jpeg(frame, quality=92)))
    return JSONResponse({"message": "ok", "name": name, "path": f"{BASE}/contents/card1/100CANON/{name}"})


@app.post(BASE + "/shooting/control/drivefocus")
def drive_focus(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Step focus. ``{"direction": "near"|"far", "steps": int}``.

    A real AF lens has finite travel, so the position is clamped: code that
    assumes it can always step further needs to handle the limit.
    """
    payload = payload or {}
    direction = str(payload.get("direction", "near")).lower()
    if direction not in {"near", "far"}:
        raise HTTPException(status_code=400, detail="direction must be 'near' or 'far'")
    try:
        steps = int(payload.get("steps", 1))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="steps must be an integer") from None

    delta = steps if direction == "far" else -steps
    state.focus_position = max(-40, min(40, state.focus_position + delta))
    return {"message": "ok", "position": state.focus_position}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@app.get(BASE + "/shooting/settings")
def shooting_settings() -> dict[str, Any]:
    return {
        "iso": {"value": state.iso, "ability": ["100", "200", "400", "800", "1600"]},
        "av": {"value": state.av, "ability": ["2.8", "4.0", "5.6", "8.0", "11", "16"]},
        "tv": {"value": state.tv, "ability": ["1/30", "1/60", "1/125", "1/250", "1/500"]},
        "wb": {"value": state.white_balance, "ability": ["auto", "daylight", "tungsten"]},
        "shuttermode": {"value": state.shutter_mode, "ability": ["electronic", "mechanical"]},
    }


_SETTING_FIELDS = {
    "iso": ("iso", ["100", "200", "400", "800", "1600"]),
    "av": ("av", ["2.8", "4.0", "5.6", "8.0", "11", "16"]),
    "tv": ("tv", ["1/30", "1/60", "1/125", "1/250", "1/500"]),
    "wb": ("white_balance", ["auto", "daylight", "tungsten"]),
    "shuttermode": ("shutter_mode", ["electronic", "mechanical"]),
}


@app.get(BASE + "/shooting/settings/{setting}")
def get_setting(setting: str) -> dict[str, Any]:
    if setting not in _SETTING_FIELDS:
        raise HTTPException(status_code=404, detail=f"Unknown setting '{setting}'")
    attr, ability = _SETTING_FIELDS[setting]
    return {"value": getattr(state, attr), "ability": ability}


@app.put(BASE + "/shooting/settings/{setting}")
def put_setting(setting: str, payload: dict[str, Any]) -> dict[str, Any]:
    if setting not in _SETTING_FIELDS:
        raise HTTPException(status_code=404, detail=f"Unknown setting '{setting}'")
    attr, ability = _SETTING_FIELDS[setting]
    value = str(payload.get("value", ""))
    if value not in ability:
        raise HTTPException(
            status_code=400, detail=f"'{value}' is not settable; allowed: {ability}"
        )
    setattr(state, attr, value)
    return {"message": "ok", "value": value}


# ---------------------------------------------------------------------------
# Contents (the card)
# ---------------------------------------------------------------------------


@app.get(BASE + "/contents")
def contents_root() -> dict[str, list[str]]:
    return {"path": [f"{BASE}/contents/card1/100CANON"]}


@app.get(BASE + "/contents/card1/100CANON")
def contents_list() -> dict[str, list[str]]:
    return {"path": [f"{BASE}/contents/card1/100CANON/{f.name}" for f in state.files]}


@app.get(BASE + "/contents/card1/100CANON/{name}")
def contents_download(name: str) -> Response:
    for f in state.files:
        if f.name == name:
            return Response(content=f.data, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail=f"No file named '{name}' on the mock card.")


@app.delete(BASE + "/contents/card1/100CANON/{name}")
def contents_delete(name: str) -> dict[str, str]:
    for i, f in enumerate(state.files):
        if f.name == name:
            del state.files[i]
            return {"message": "ok"}
    raise HTTPException(status_code=404, detail=f"No file named '{name}' on the mock card.")


# ---------------------------------------------------------------------------
# Mock-only controls (no equivalent on a real camera)
# ---------------------------------------------------------------------------


@app.post("/__mock/film")
def set_film_type(payload: dict[str, Any]) -> dict[str, Any]:
    """Switch the synthetic film between colour and B&W. Test helper only."""
    mode = str(payload.get("mode", "color")).lower()
    if mode not in {"color", "bw"}:
        raise HTTPException(status_code=400, detail="mode must be 'color' or 'bw'")
    state.color_film = mode == "color"
    return {"message": "ok", "mode": mode}


@app.post("/__mock/reset")
def reset_state() -> dict[str, str]:
    """Reset the mock camera to a clean state. Test helper only."""
    global state
    state = MockCameraState()
    return {"message": "ok"}


def main() -> None:
    """Entry point for ``python -m csfs.mock.mock_server``."""
    import uvicorn

    from csfs.config import load_config

    parser = argparse.ArgumentParser(description="Run the mock CCAPI camera.")
    parser.add_argument("--host", default=None, help="Bind address (default from config).")
    parser.add_argument("--port", type=int, default=None, help="Port (default from config).")
    args = parser.parse_args()

    config = load_config()
    host = args.host or config.mock.host
    port = args.port or config.mock.port

    print(f"Mock CCAPI camera listening on http://{host}:{port}{API_ROOT}/")
    print("Point camera.base_url at that address (minus the /ccapi path).")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
