"""The local web server.

Run it::

    python -m csfs.app.server

then open the printed URL and put the browser fullscreen on your big monitor.

Bound to loopback by default. Do not expose this to a network you do not
control -- it can fire your shutter.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Literal
from collections.abc import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from csfs.app.session import CaptureInProgressError, ScanSession
from csfs.ccapi.errors import CCAPIError
from csfs.config import Config, load_config
from csfs.processing.codec import DecodeError, decode_jpeg, encode_jpeg
from csfs.processing.invert import invert_linear
from csfs.processing.loupe import crop_zoom
from csfs.processing.peaking import apply_peaking
from csfs.processing.sharpness import corner_sharpness, sharpness_of_region

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Boundary for the MJPEG we serve to the browser. Unrelated to the camera's.
_BOUNDARY = "csfsframe"

app = FastAPI(title="Canon Smart Film Scan")
session = ScanSession(config=Config())


def configure(config: Config) -> None:
    """Point the server at a configuration. Called before serving."""
    session.config = config


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PreviewRequest(BaseModel):
    """Preview settings. Every field is optional; omitted ones are unchanged."""

    invert: bool | None = None
    loupe: bool | None = None
    loupe_zoom: float | None = Field(default=None, gt=0, le=32)
    loupe_x: float | None = Field(default=None, ge=0, le=1)
    loupe_y: float | None = Field(default=None, ge=0, le=1)
    peaking: bool | None = None
    peaking_sensitivity: float | None = Field(default=None, ge=0, le=1)


class ZoomRequest(BaseModel):
    value: str


class FocusRequest(BaseModel):
    """One focus increment. CCAPI moves focus in fixed steps, not by distance."""

    direction: Literal["near", "far"]
    size: Literal["fine", "medium", "coarse"] = "fine"


class SettingRequest(BaseModel):
    """Change one camera setting to a value the camera says it accepts."""

    name: str
    value: str


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


@app.get("/api/status")
def status() -> dict[str, Any]:
    """Everything the UI needs to render itself.

    Capabilities are included so the UI can hide controls the body lacks rather
    than offering buttons that do nothing.
    """
    caps = session.capabilities
    payload: dict[str, Any] = {
        "connected": session.connected,
        "base_url": session.config.camera.base_url,
        "settle_delay_s": session.config.capture.settle_delay_s,
        "output_dir": str(session.config.capture.resolved_output_dir()),
        "shutter_mode": session.shutter_mode,
        "last_capture": session.last_capture,
        "preview": {
            "invert": session.preview.invert,
            "loupe": session.preview.loupe,
            "loupe_zoom": session.preview.loupe_zoom,
            "loupe_x": session.preview.loupe_x,
            "loupe_y": session.preview.loupe_y,
            "peaking": session.preview.peaking,
            "peaking_sensitivity": session.preview.peaking_sensitivity,
        },
        "prepared": session.prepared,
        "streaming": session.pump is not None and session.pump.running,
        "stream_error": session.pump.error if session.pump is not None else None,
    }
    if caps is not None:
        payload["api_versions"] = sorted(caps.api_versions)
        payload["capabilities"] = sorted(caps.resolved)
        payload["has_camera_zoom"] = caps.has("lv_zoom")
        payload["settings"] = session.read_settings()
    if session.focus is not None:
        payload["focus"] = {
            "available": session.focus.available,
            "reason": session.focus.unavailable_reason(),
            "lens": session.focus.lens.description,
        }
    return payload


@app.post("/api/connect")
def connect() -> dict[str, Any]:
    """Connect to the camera and start live view."""
    try:
        session.connect()
    except CaptureInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CCAPIError as exc:
        # These messages name the real-world cause; pass them through verbatim.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return status()


@app.post("/api/disconnect")
def disconnect() -> dict[str, Any]:
    session.disconnect()
    return status()


# ---------------------------------------------------------------------------
# Live preview
# ---------------------------------------------------------------------------


def _render(frame_bytes: bytes) -> bytes | None:
    """Apply the current preview settings to one raw live-view frame.

    Returns None for an undecodable frame; live-view streams occasionally
    deliver a partial image, and dropping one is invisible at 15fps.
    """
    try:
        image = decode_jpeg(frame_bytes)
    except DecodeError:
        return None

    if session.preview.invert:
        image = invert_linear(image)

    # Peaking runs at native resolution, before the loupe: see peaking.py.
    if session.preview.peaking:
        image = apply_peaking(image, sensitivity=session.preview.peaking_sensitivity)

    if session.preview.loupe:
        image = crop_zoom(
            image,
            center_x=session.preview.loupe_x,
            center_y=session.preview.loupe_y,
            zoom=session.preview.loupe_zoom,
        )

    return encode_jpeg(image)


def _mjpeg_stream() -> Iterator[bytes]:
    """Yield the processed preview as multipart MJPEG."""
    pump = session.pump
    if pump is None:
        return
    seen = 0
    while pump.running:
        result = pump.latest(after=seen, timeout=5.0)
        if result is None:
            continue
        frame_bytes, seen = result
        rendered = _render(frame_bytes)
        if rendered is None:
            continue
        yield (
            f"--{_BOUNDARY}\r\n"
            f"Content-Type: image/jpeg\r\n"
            f"Content-Length: {len(rendered)}\r\n\r\n"
        ).encode("ascii") + rendered + b"\r\n"


@app.get("/stream.mjpg")
def stream() -> StreamingResponse:
    """The processed live view, ready to drop into an <img> tag."""
    if session.pump is None or not session.pump.running:
        raise HTTPException(status_code=409, detail="Not connected, or live view is not running.")
    return StreamingResponse(
        _mjpeg_stream(),
        media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
        # A preview must never be served from cache, by the browser or anything
        # sitting in front of it.
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/frame.jpg")
def single_frame() -> Response:
    """One processed frame, without opening a stream."""
    if session.pump is None:
        raise HTTPException(status_code=409, detail="Not connected.")
    result = session.pump.latest(after=0, timeout=5.0)
    if result is None:
        raise HTTPException(status_code=503, detail="No live-view frame available yet.")
    rendered = _render(result[0])
    if rendered is None:
        raise HTTPException(status_code=503, detail="Live-view frame could not be decoded.")
    return Response(content=rendered, media_type="image/jpeg")


@app.post("/api/preview")
def set_preview(request: PreviewRequest) -> dict[str, Any]:
    """Update preview settings. Takes effect on the next frame."""
    for field_name, value in request.model_dump(exclude_none=True).items():
        setattr(session.preview, field_name, value)
    return status()


@app.post("/api/camera-zoom")
def set_camera_zoom(request: ZoomRequest) -> dict[str, Any]:
    """Set camera-side live-view magnification (typically 1, 5 or 10)."""
    if not session.connected:
        raise HTTPException(status_code=409, detail="Not connected.")
    if not session.set_camera_zoom(request.value):
        raise HTTPException(
            status_code=400, detail=f"Camera would not accept zoom '{request.value}'."
        )
    return status()


# ---------------------------------------------------------------------------
# Focus
# ---------------------------------------------------------------------------


@app.post("/api/focus")
def drive_focus(request: FocusRequest) -> dict[str, Any]:
    """Move focus one increment.

    A refusal is a 409 with the reason: the usual cause is the lens set to
    manual focus, which the user fixes on the rig.
    """
    if not session.connected or session.focus is None:
        raise HTTPException(status_code=409, detail="Not connected.")
    if not session.focus.drive(request.direction, request.size):
        raise HTTPException(
            status_code=409,
            detail=session.focus.unavailable_reason() or "Focus drive was refused.",
        )
    return {"ok": True}


@app.post("/api/focus/recheck")
def recheck_focus() -> dict[str, Any]:
    """Look again after the user has moved the AF/MF switch."""
    if not session.connected or session.focus is None:
        raise HTTPException(status_code=409, detail="Not connected.")
    session.focus.recheck()
    return status()


@app.get("/api/sharpness")
def measure_sharpness() -> dict[str, Any]:
    """Sharpness of the region the loupe is on, plus per-corner values.

    Relative only: turn focus and watch it rise. A corner consistently lower
    than the others means the stand is not parallel to the film.
    """
    if session.pump is None:
        raise HTTPException(status_code=409, detail="Not connected.")
    result = session.pump.latest(after=0, timeout=5.0)
    if result is None:
        raise HTTPException(status_code=503, detail="No live-view frame available yet.")
    try:
        image = decode_jpeg(result[0])
    except DecodeError:
        raise HTTPException(status_code=503, detail="Frame could not be decoded.") from None

    # Measured on the raw frame: the loupe's enlargement would inflate it.
    value = sharpness_of_region(
        image,
        center_x=session.preview.loupe_x,
        center_y=session.preview.loupe_y,
        size=0.25,
    )
    session.note_sharpness(value)
    return {
        "sharpness": round(value, 2),
        "best": round(session.best_sharpness, 2),
        "corners": {k: round(v, 2) for k, v in corner_sharpness(image).items()},
    }


@app.post("/api/sharpness/reset")
def reset_sharpness() -> dict[str, Any]:
    """Forget the best-so-far reading, e.g. when moving to a new negative."""
    session.best_sharpness = 0.0
    return {"best": 0.0}


# ---------------------------------------------------------------------------
# Camera settings
# ---------------------------------------------------------------------------


@app.post("/api/setting")
def set_setting(request: SettingRequest) -> dict[str, Any]:
    """Change one camera setting.

    The value must be one the camera currently reports as selectable.
    """
    if not session.connected:
        raise HTTPException(status_code=409, detail="Not connected.")
    ok, reason = session.write_setting(request.name, request.value)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    return status()


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


@app.post("/api/capture")
def capture() -> dict[str, Any]:
    """Settle, fire the shutter, and download the resulting file."""
    if not session.connected:
        raise HTTPException(status_code=409, detail="Not connected.")
    try:
        path = session.capture()
    except CaptureInProgressError as exc:
        # 409, not 500: the request is fine, the timing is not.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CCAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"saved": str(path), "name": path.name}


@app.get("/api/captures")
def captures() -> dict[str, Any]:
    """List what has been downloaded to the output folder so far."""
    output_dir = session.config.capture.resolved_output_dir()
    if not output_dir.is_dir():
        return {"dir": str(output_dir), "files": []}
    files = sorted(
        (p for p in output_dir.iterdir() if p.is_file() and not p.name.endswith(".part")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return {"dir": str(output_dir), "files": [p.name for p in files[:50]]}


# Mounted last so it cannot shadow the API routes above.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    """Entry point for ``python -m csfs.app.server``."""
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the Canon Smart Film Scan UI.")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=None, help="Path to a config file.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    config = load_config(args.config)
    configure(config)
    host = args.host or config.server.host
    port = args.port or config.server.port

    print(f"\n  Canon Smart Film Scan -> http://{host}:{port}\n")
    print(f"  Camera:  {config.camera.base_url}")
    print(f"  Output:  {config.capture.resolved_output_dir()}\n")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
