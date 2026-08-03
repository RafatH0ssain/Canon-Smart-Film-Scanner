"""A mock CCAPI camera.

Run it::

    python -m csfs.mock.mock_server

Then point ``camera.base_url`` at ``http://127.0.0.1:8080`` (which is already
the default) and the rest of the application cannot tell the difference for the
features it needs.

WHAT THIS IS AND IS NOT
-----------------------
The request and response shapes here were calibrated against Canon's CCAPI
Reference v1.4.0 on 2026-08-02, but this file contains none of Canon's text --
that document is licensed per-developer and cannot live in this repository.

The mock is not authoritative and no real body has confirmed it. That is safe,
because the client never assumes a path: it reads the endpoint inventory this
server publishes, exactly as it reads the inventory a real camera publishes (see
``csfs.ccapi.discovery``). The mock therefore exercises the real discovery path
rather than papering over it.

It models the parts of a camera that matter for scanning, including several
things that only bite in practice:

  - **Two API versions**, with the newer one exposing capabilities the older
    lacks, so capability detection is genuinely exercised.
  - **Live view is started by choosing an output size**, not by a bare "start",
    and is stopped by setting that size back to off.
  - **Auto power-off**, because a sleeping camera looks exactly like a wiring
    fault, and scanning a roll involves long idle stretches.
  - **Camera-side live-view zoom**, which returns real sensor detail rather than
    enlarged pixels.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import struct
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import cv2
import numpy as np
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from csfs.mock.frames import make_negative

API_ROOT = "/ccapi"

# Two versions, because real bodies publish several and the client must choose
# sensibly between duplicate listings of the same capability.
VER_OLD = "ver100"
VER = "ver110"
BASE_OLD = f"{API_ROOT}/{VER_OLD}"
BASE = f"{API_ROOT}/{VER}"

# Live-view frame rate. Real bodies deliver roughly this over a good link, and
# matching it keeps the UI's latency behaviour honest during development.
LIVEVIEW_FPS = 15

# CCAPI's multipart live-view stream uses a boundary token literally named
# "boundary". Matching it keeps the client's parser honest.
_BOUNDARY = "boundary"

# Live-view output sizes CCAPI accepts. "medium" is the largest on offer, and
# "off" is how live view is stopped -- there is no DELETE for it.
_LIVEVIEW_SIZES = ("off", "small", "medium")
_LIVEVIEW_PIXELS = {"small": (640, 424), "medium": (960, 640)}


@dataclass
class CapturedFile:
    """One file on the mock camera's card."""

    name: str
    data: bytes
    created_at: float = field(default_factory=time.time)


@dataclass
class MockCameraState:
    """Everything the fake camera remembers between requests."""

    # "off" means live view is not running; otherwise this is the output size.
    liveview_size: str = "off"
    cameradisplay: str = "keep"
    color_film: bool = True

    # Focus position in arbitrary steps. 0 is perfectly sharp; the magnitude
    # maps to blur radius, so driving focus in the UI visibly changes the image
    # and the sharpness metric responds -- which is the point of v0.2.
    focus_position: int = 0

    # One of elec_1st_curtain / mechanical / electronic. Defaults to the
    # mechanical-tinged option deliberately, so code that forgets to select
    # "electronic" produces a visibly wrong default rather than working by
    # accident.
    shutter_mode: str = "elec_1st_curtain"

    # Camera-side live-view magnification: "1", "5" or "10".
    lv_zoom: str = "1"

    auto_power_off: str = "1min"

    # Body profile. Real bodies differ in which live-view transports and
    # magnification they offer -- an EOS R7 has neither the multipart endpoint
    # nor camera-side live-view zoom, so the binary "scroll" framing is its
    # primary path rather than a fallback. Both configurations need testing.
    has_multipart: bool = True
    has_lv_zoom: bool = True

    lens_mounted: bool = True
    lens_name: str = "RF100mm F2.8 L MACRO IS USM (mock)"

    iso: str = "100"
    av: str = "8.0"
    tv: str = "1/125"
    white_balance: str = "daylight"

    files: list[CapturedFile] = field(default_factory=list)
    shutter_count: int = 0

    @property
    def liveview_active(self) -> bool:
        """Whether live view is currently producing frames."""
        return self.liveview_size != "off"

    def focus_error(self) -> float:
        """Blur radius implied by the current focus position.

        Camera-side zoom divides the visible blur, because magnifying real
        sensor detail is what makes misfocus obvious -- the whole reason the
        loupe should prefer camera zoom over enlarging pixels on the PC.
        """
        return abs(self.focus_position) / 4.0 / float(self.lv_zoom)


state = MockCameraState()
app = FastAPI(title="Mock CCAPI Camera", docs_url="/__docs")

# Endpoints every API version offers.
router = APIRouter()
# Endpoints only the newer API version offers, so that capability detection has
# something real to detect.
modern_router = APIRouter()


def _encode_jpeg(frame: np.ndarray, quality: int = 85) -> bytes:
    """Encode a BGR array as JPEG bytes."""
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise HTTPException(status_code=500, detail="Mock camera failed to encode a frame.")
    return buffer.tobytes()


def _version_prefix(request: Request) -> str:
    """The ``/ccapi/verXXX`` prefix the caller actually used.

    Content listings must hand back URLs under the same version the client is
    talking, since the stored-file layout differs between versions on real
    bodies.
    """
    parts = request.url.path.split("/")
    # parts == ["", "ccapi", "verXXX", ...]
    if len(parts) >= 3 and parts[1] == "ccapi":
        return f"/{parts[1]}/{parts[2]}"
    return BASE


def _liveview_frame() -> np.ndarray:
    """Render the current live-view frame.

    Live view on a real body is a reduced-resolution stream, so the mock matches
    that: the loupe in the UI must magnify a genuinely limited source, otherwise
    focus confirmation would look better in development than it does in reality.
    """
    width, height = _LIVEVIEW_PIXELS.get(state.liveview_size, _LIVEVIEW_PIXELS["medium"])
    return make_negative(
        width=width,
        height=height,
        color=state.color_film,
        phase=time.time() % (2 * math.pi),
        focus_error=state.focus_error(),
        seed=None,
    )


def _require_liveview() -> None:
    """Reject a frame request when live view has not been started.

    A real body answers 503 here, and code that forgets to start the session
    should fail in development rather than at the copy stand.
    """
    if not state.liveview_active:
        raise HTTPException(status_code=503, detail={"message": "Live view not started"})


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _endpoint(path: str, get: bool = False, post: bool = False,
              put: bool = False, delete: bool = False) -> dict[str, Any]:
    """One entry in the endpoint inventory, in CCAPI's reported shape."""
    return {"path": path, "get": get, "post": post, "put": put, "delete": delete}


def _endpoints_for(base: str, *, modern: bool) -> list[dict[str, Any]]:
    """The endpoint inventory for one API version."""
    entries = [
        _endpoint(f"{base}/deviceinformation", get=True),
        _endpoint(f"{base}/devicestatus/storage", get=True),
        _endpoint(f"{base}/devicestatus/battery", get=True),
        _endpoint(f"{base}/devicestatus/lens", get=True),
        _endpoint(f"{base}/devicestatus/temperature", get=True),
        _endpoint(f"{base}/shooting/liveview", post=True),
        _endpoint(f"{base}/shooting/liveview/flip", get=True),
        _endpoint(f"{base}/shooting/liveview/scroll", get=True, delete=True),
        _endpoint(f"{base}/shooting/control/shutterbutton", post=True),
        _endpoint(f"{base}/shooting/control/shutterbutton/manual", post=True),
        _endpoint(f"{base}/shooting/control/drivefocus", post=True),
        _endpoint(f"{base}/shooting/control/af", post=True),
        _endpoint(f"{base}/shooting/settings", get=True),
        _endpoint(f"{base}/shooting/settings/iso", get=True, put=True),
        _endpoint(f"{base}/shooting/settings/av", get=True, put=True),
        _endpoint(f"{base}/shooting/settings/tv", get=True, put=True),
        _endpoint(f"{base}/shooting/settings/wb", get=True, put=True),
        _endpoint(f"{base}/functions/autopoweroff", get=True, put=True),
        _endpoint(f"{base}/event/polling", get=True),
        _endpoint(f"{base}/contents", get=True),
    ]
    if state.has_multipart:
        entries.append(
            _endpoint(f"{base}/shooting/liveview/multipart", get=True, delete=True)
        )
    if modern:
        entries.append(_endpoint(f"{base}/shooting/settings/shuttermode", get=True, put=True))
        if state.has_lv_zoom:
            entries.append(_endpoint(f"{base}/shooting/settings/lvzoom", get=True, put=True))
    return entries


@app.get(API_ROOT + "/")
@app.get(API_ROOT)
def api_root() -> dict[str, list[dict[str, Any]]]:
    """List every supported API version and its full endpoint inventory.

    This single response is what makes CCAPI self-describing: the client reads
    it and never has to assume a path.
    """
    return {
        VER_OLD: _endpoints_for(BASE_OLD, modern=False),
        VER: _endpoints_for(BASE, modern=True),
    }


@router.get("/deviceinformation")
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


@router.get("/devicestatus/storage")
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


@router.get("/devicestatus/battery")
def battery() -> dict[str, str]:
    return {"name": "LP-E6NH (mock)", "kind": "battery", "level": "full", "quality": "good"}


@router.get("/devicestatus/lens")
def lens() -> dict[str, Any]:
    """Report the mounted lens.

    Note this says nothing about whether the lens can autofocus -- CCAPI does
    not report that. Manual-lens detection has to come from whether the focus
    endpoint exists and actually responds.
    """
    return {"mount": state.lens_mounted, "name": state.lens_name if state.lens_mounted else ""}


@router.get("/devicestatus/temperature")
def temperature() -> dict[str, str]:
    return {"status": "normal"}


# ---------------------------------------------------------------------------
# Live view
# ---------------------------------------------------------------------------


@router.post("/shooting/liveview")
def liveview_settings(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Configure live-view output.

    ``{"liveviewsize": "off"|"small"|"medium", "cameradisplay": "on"|"off"|"keep"}``

    There is no separate start/stop: choosing a size starts it, and "off" stops
    it.
    """
    payload = payload or {}
    size = str(payload.get("liveviewsize", "medium"))
    if size not in _LIVEVIEW_SIZES:
        raise HTTPException(
            status_code=400,
            detail={"message": f"Invalid parameter: liveviewsize must be one of {_LIVEVIEW_SIZES}"},
        )
    display = str(payload.get("cameradisplay", "keep"))
    if display not in ("on", "off", "keep"):
        raise HTTPException(
            status_code=400,
            detail={"message": "Invalid parameter: cameradisplay must be on, off or keep"},
        )
    state.liveview_size = size
    state.cameradisplay = display
    return {}


@router.get("/shooting/liveview/flip")
def liveview_flip() -> Response:
    """Return exactly one live-view frame as a JPEG."""
    _require_liveview()
    return Response(content=_encode_jpeg(_liveview_frame()), media_type="image/jpeg")


async def _multipart_frames() -> AsyncIterator[bytes]:
    """Yield multipart MJPEG chunks until live view stops or the client leaves."""
    interval = 1.0 / LIVEVIEW_FPS
    while state.liveview_active:
        jpeg = await asyncio.to_thread(lambda: _encode_jpeg(_liveview_frame()))
        yield (
            f"--{_BOUNDARY}\n"
            f"Content-Type: image/jpeg\n"
            f"Content-Length: {len(jpeg)}\n\n"
        ).encode("ascii") + jpeg + b"\n"
        await asyncio.sleep(interval)
    yield f"--{_BOUNDARY}--\n".encode("ascii")


@router.get("/shooting/liveview/multipart")
def liveview_multipart() -> StreamingResponse:
    """Continuous live view as standard multipart MJPEG.

    Preferred over the ``scroll`` variant: it is an ordinary format that needs
    no custom parser.
    """
    _require_liveview()
    return StreamingResponse(
        _multipart_frames(),
        media_type=f"multipart/x-mixed-replace;boundary={_BOUNDARY}",
    )


@router.delete("/shooting/liveview/multipart")
def liveview_multipart_stop() -> dict[str, Any]:
    """Stop the multipart stream without changing the live-view size."""
    return {}


async def _scroll_frames() -> AsyncIterator[bytes]:
    """Yield live-view frames in CCAPI's own binary framing.

    Each frame is a header (marker, data-type, 4-byte length), then the JPEG,
    then an end marker.

    UNVERIFIED: the byte order of the 4-byte length is not something this
    project has confirmed against a real body, which is one reason the multipart
    endpoint is preferred. If you ever need this path, check it against your
    reference first.
    """
    interval = 1.0 / LIVEVIEW_FPS
    while state.liveview_active:
        jpeg = await asyncio.to_thread(lambda: _encode_jpeg(_liveview_frame()))
        yield b"\xff\x00\x00" + struct.pack(">I", len(jpeg)) + jpeg + b"\xff\xff"
        await asyncio.sleep(interval)


@router.get("/shooting/liveview/scroll")
def liveview_scroll() -> StreamingResponse:
    """Continuous live view in Canon's binary chunk framing."""
    _require_liveview()
    return StreamingResponse(_scroll_frames(), media_type="image/octet-stream")


# ---------------------------------------------------------------------------
# Shutter & focus
# ---------------------------------------------------------------------------


@router.post("/shooting/control/shutterbutton")
def shutter_button(payload: dict[str, Any] | None = None) -> JSONResponse:
    """Fire the shutter and write a file to the fake card. ``{"af": bool}``.

    The captured frame is rendered at a higher resolution than live view and
    with finer grain, mirroring the real gap between the preview stream and the
    file you actually scan from.
    """
    payload = payload or {}
    af = payload.get("af", False)
    if not isinstance(af, bool):
        raise HTTPException(status_code=400, detail={"message": "Invalid parameter"})
    if af:
        # Autofocus on film grain is unreliable and can hunt. Nudging focus here
        # makes that mistake visible in the preview rather than silent.
        state.focus_position += 1

    frame = make_negative(
        width=2400,
        height=1600,
        color=state.color_film,
        phase=time.time() % (2 * math.pi),
        focus_error=abs(state.focus_position) / 4.0,
        grain=0.012,
        seed=state.shutter_count,
    )
    state.shutter_count += 1
    name = f"IMG_{state.shutter_count:04d}.JPG"
    state.files.append(CapturedFile(name=name, data=_encode_jpeg(frame, quality=92)))
    return JSONResponse({})


@router.post("/shooting/control/shutterbutton/manual")
def shutter_button_manual(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Half/full press control: ``{"action": ..., "af": bool}``."""
    payload = payload or {}
    action = str(payload.get("action", ""))
    if action not in ("release", "half_press", "full_press"):
        raise HTTPException(status_code=400, detail={"message": "Invalid parameter"})
    if action == "full_press":
        shutter_button({"af": bool(payload.get("af", False))})
    return {}


@router.post("/shooting/control/af")
def af_control(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {}


@router.post("/shooting/control/drivefocus")
def drive_focus(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Step focus. ``{"direction": "near"|"far", "steps": int}``.

    A real AF lens has finite travel, so the position is clamped: code that
    assumes it can always step further needs to handle the limit. A manual lens
    cannot be driven at all, which the mock simulates when no lens is mounted.
    """
    payload = payload or {}
    if not state.lens_mounted:
        raise HTTPException(
            status_code=503, detail={"message": "Device busy: focus cannot be driven"}
        )
    direction = str(payload.get("direction", "near")).lower()
    if direction not in {"near", "far"}:
        raise HTTPException(status_code=400, detail={"message": "Invalid parameter: direction"})
    try:
        steps = int(payload.get("steps", 1))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400, detail={"message": "Invalid parameter: steps"}
        ) from None

    delta = steps if direction == "far" else -steps
    state.focus_position = max(-40, min(40, state.focus_position + delta))
    return {"position": state.focus_position}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

# setting name -> (state attribute, selectable values)
_COMMON_SETTINGS = {
    "iso": ("iso", ["100", "200", "400", "800", "1600"]),
    "av": ("av", ["2.8", "4.0", "5.6", "8.0", "11", "16"]),
    "tv": ("tv", ["1/30", "1/60", "1/125", "1/250", "1/500"]),
    "wb": ("wb", ["auto", "daylight", "tungsten"]),
}
_MODERN_SETTINGS = {
    "shuttermode": ("shutter_mode", ["elec_1st_curtain", "mechanical", "electronic"]),
    "lvzoom": ("lv_zoom", ["1", "5", "10"]),
}
# The mock's attribute name differs from the API name for a couple of settings.
_ATTR_OVERRIDES = {"wb": "white_balance"}


def _setting_spec(setting: str, allow_modern: bool) -> tuple[str, list[str]]:
    table = dict(_COMMON_SETTINGS)
    if allow_modern:
        table.update(_MODERN_SETTINGS)
    if setting not in table:
        raise HTTPException(status_code=404, detail={"message": f"Unknown setting '{setting}'"})
    attr, ability = table[setting]
    return _ATTR_OVERRIDES.get(setting, attr), ability


@router.get("/shooting/settings")
def shooting_settings() -> dict[str, Any]:
    """All shooting settings in one response, each as {value, ability}."""
    out: dict[str, Any] = {}
    for name, (attr, ability) in {**_COMMON_SETTINGS, **_MODERN_SETTINGS}.items():
        out[name] = {"value": getattr(state, _ATTR_OVERRIDES.get(name, attr)), "ability": ability}
    return out


@router.get("/shooting/settings/{setting}")
def get_setting(setting: str) -> dict[str, Any]:
    attr, ability = _setting_spec(setting, allow_modern=False)
    return {"value": getattr(state, attr), "ability": ability}


@router.put("/shooting/settings/{setting}")
def put_setting(setting: str, payload: dict[str, Any]) -> dict[str, Any]:
    attr, ability = _setting_spec(setting, allow_modern=False)
    return _apply_setting(attr, ability, payload)


@modern_router.get("/shooting/settings/{setting}")
def get_setting_modern(setting: str) -> dict[str, Any]:
    attr, ability = _setting_spec(setting, allow_modern=True)
    return {"value": getattr(state, attr), "ability": ability}


@modern_router.put("/shooting/settings/{setting}")
def put_setting_modern(setting: str, payload: dict[str, Any]) -> dict[str, Any]:
    attr, ability = _setting_spec(setting, allow_modern=True)
    return _apply_setting(attr, ability, payload)


def _apply_setting(attr: str, ability: list[str], payload: dict[str, Any]) -> dict[str, Any]:
    value = str(payload.get("value", ""))
    if value not in ability:
        raise HTTPException(
            status_code=400,
            detail={"message": f"Invalid parameter: '{value}' not in {ability}"},
        )
    setattr(state, attr, value)
    return {}


@router.get("/functions/autopoweroff")
def get_auto_power_off() -> dict[str, Any]:
    return {
        "value": state.auto_power_off,
        "ability": ["disable", "1min", "2min", "4min", "8min", "15min"],
    }


@router.put("/functions/autopoweroff")
def put_auto_power_off(payload: dict[str, Any]) -> dict[str, Any]:
    """Disabling this is what stops the camera sleeping mid-roll."""
    value = str(payload.get("value", ""))
    if value not in ("disable", "1min", "2min", "4min", "8min", "15min"):
        raise HTTPException(status_code=400, detail={"message": "Invalid parameter"})
    state.auto_power_off = value
    return {}


@router.get("/event/polling")
def event_polling() -> dict[str, Any]:
    """Report state changes since the last poll.

    Real bodies use this to announce that a capture has been written, which is
    more reliable than sleeping for a guessed interval after the shutter.
    """
    latest = state.files[-1].name if state.files else None
    return {"addedcontents": [latest] if latest else []}


# ---------------------------------------------------------------------------
# Contents (the card)
# ---------------------------------------------------------------------------


@router.get("/contents")
def contents_root(request: Request) -> dict[str, list[str]]:
    return {"path": [f"{_version_prefix(request)}/contents/card1"]}


@router.get("/contents/card1")
def contents_card(request: Request) -> dict[str, list[str]]:
    return {"path": [f"{_version_prefix(request)}/contents/card1/100CANON"]}


@router.get("/contents/card1/100CANON")
def contents_list(request: Request) -> dict[str, list[str]]:
    prefix = _version_prefix(request)
    return {"path": [f"{prefix}/contents/card1/100CANON/{f.name}" for f in state.files]}


@router.get("/contents/card1/100CANON/{name}")
def contents_download(name: str) -> Response:
    for f in state.files:
        if f.name == name:
            return Response(content=f.data, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail={"message": f"Not found: '{name}'"})


@router.delete("/contents/card1/100CANON/{name}")
def contents_delete(name: str) -> dict[str, Any]:
    for i, f in enumerate(state.files):
        if f.name == name:
            del state.files[i]
            return {}
    raise HTTPException(status_code=404, detail={"message": f"Not found: '{name}'"})


# Mount the shared routes under both versions, and the newer-only routes under
# the newer version alone. Order matters: the modern router must be included
# first so its more specific settings handler wins for that prefix.
app.include_router(modern_router, prefix=BASE)
app.include_router(router, prefix=BASE)
app.include_router(router, prefix=BASE_OLD)


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


@app.post("/__mock/body-profile")
def set_body_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Switch which capabilities the mock body advertises. Test helper only.

    ``"r7"`` reproduces a real EOS R7: no multipart live-view endpoint and no
    camera-side live-view zoom, which makes Canon's binary framing the primary
    stream rather than a fallback. ``"modern"`` advertises everything.
    """
    profile = str(payload.get("profile", "modern")).lower()
    if profile not in {"modern", "r7"}:
        raise HTTPException(status_code=400, detail="profile must be 'modern' or 'r7'")
    state.has_multipart = profile == "modern"
    state.has_lv_zoom = profile == "modern"
    return {"message": "ok", "profile": profile}


@app.post("/__mock/lens")
def set_lens(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach or detach the lens, to exercise the manual-lens fallback."""
    state.lens_mounted = bool(payload.get("mounted", True))
    return {"message": "ok", "mounted": state.lens_mounted}


@app.get("/__mock/needs-auth")
@app.get("/__mock/needs-auth/{rest:path}")
def needs_auth(rest: str = "") -> Response:
    """Always answers 401, to exercise the digest-auth error path. Test helper only."""
    return Response(
        status_code=401,
        content='{"message":"Unauthorized"}',
        media_type="application/json",
        headers={"WWW-Authenticate": 'Digest realm="ccapi", nonce="mock"'},
    )


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

    print(f"Mock CCAPI camera listening on http://{host}:{port}{API_ROOT}")
    print("Point camera.base_url at that address (minus the /ccapi path).")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
