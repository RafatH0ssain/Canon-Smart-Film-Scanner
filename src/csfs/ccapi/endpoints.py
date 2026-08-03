"""The one place in this project where CCAPI path assumptions are allowed.

WHY THIS FILE EXISTS
--------------------
Canon licenses the CCAPI reference documentation per developer and forbids
redistributing it, so this repository cannot contain Canon's endpoint tables.
Guessing paths from memory is worse than useless: a wrong path fails in a way
that looks like a broken camera, and costs an afternoon to diagnose.

So the client resolves capabilities the self-describing way instead. CCAPI's
root returns the endpoints each supported version actually offers; discovery
reads that list at connect time and matches it against the *hints* below. The
reference itself recommends exactly this -- driving calls from the URLs the
camera reports, because they vary by model and firmware. That means:

  - Different camera bodies exposing different subsets of the API just work.
  - When a hint is wrong, exactly one file needs correcting -- this one.
  - Nothing else in the codebase contains a literal CCAPI path.

CALIBRATION STATUS
------------------
The hints below were calibrated against a local copy of Canon's CCAPI Reference
v1.4.0 (EN) on 2026-08-02, which documents 4 API versions (ver100-ver140). They
are hints matched against whatever a camera reports, not transcriptions of
Canon's tables, and no camera has confirmed them yet.

Verify against your own body with::

    python -m csfs.tools.dump_endpoints

Anything reported MISSING that your reference says your camera supports needs a
distinguishing path segment added to the relevant tuple below. You do not need
to enumerate every endpoint -- only enough to pick the right one out of the
camera's own list unambiguously.

NOTES WORTH KNOWING
-------------------
Paths genuinely move between API versions, which is why matching beats
hardcoding. The clearest example is stored files: the ver100 listing is rooted
at a storage name, later versions use a card name, and ver140 inserts a
directory level. Discovery follows whatever the camera reports.

CONFIRMED AGAINST REAL HARDWARE
-------------------------------
**EOS R7** (firmware current as of 2026-08-03, 102 endpoints, ver100/110/130):
32 of 34 capabilities resolved with no hint changes needed. Two are genuinely
absent from the body, and both matter:

  - **No** ``liveview/multipart``. The R7 offers only flip, flipdetail, scroll,
    scrolldetail and rtp, so Canon's binary ``scroll`` framing is this body's
    *primary* live-view path rather than a fallback. Do not assume multipart
    exists; ``LIVEVIEW_STREAM_PREFERENCE`` handles the fallback automatically.
  - **No** ``lvzoom``. There is no camera-side live-view magnification on this
    body, so the software loupe is the only option. The UI hides the control
    via capability detection rather than offering a button that does nothing.

The R7 also exposes ``functions/wificonnection``, which the reference says is
unsupported on wired-LAN-capable cameras -- a neat confirmation that this body
is Wi-Fi only, and that CCAPI has no USB control path.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Root path candidates
# ---------------------------------------------------------------------------
# Discovery probes these against ``camera.base_url`` in order and uses the first
# that returns a JSON body listing API versions. Probing rather than assuming
# means a wrong first guess costs one extra request, not a failed connection.
#
# The root returns every version mapped to its full endpoint inventory, so this
# is normally the only request discovery has to make.
ROOT_PATH_CANDIDATES: tuple[str, ...] = (
    "/ccapi",
    "/ccapi/",
)

# ---------------------------------------------------------------------------
# Capability hints
# ---------------------------------------------------------------------------
# Maps a logical capability name (used throughout this codebase) to substrings
# that may appear in the camera's reported path for it.
#
# Matching is case-insensitive and checks whole path segments first, then falls
# back to substring matching. Order within a tuple is priority order: an earlier
# hint wins when several endpoints match.
#
# Add hints; do not rename the capability keys, since the rest of the codebase
# refers to them.
CAPABILITY_HINTS: dict[str, tuple[str, ...]] = {
    # --- identity & status -------------------------------------------------
    "device_info": ("deviceinformation",),
    "battery": ("battery", "batterylist"),
    "storage": ("storage",),
    "temperature": ("temperature",),
    # Lens information reports whether a lens is mounted and its name. Note it
    # does NOT report whether that lens can autofocus, so manual-lens detection
    # keys off whether the focus-drive endpoint exists and responds -- see
    # csfs.ccapi.focus.
    "lens": ("lens",),
    # --- live view (v0.1) --------------------------------------------------
    # Live view is started by POSTing an output size, not by a bare "start", and
    # stopped by setting that size to off. There is no DELETE on this endpoint.
    "liveview": ("liveview",),
    # A single JPEG frame. Simple, pollable, and the reliable fallback.
    "liveview_flip": ("flip",),
    # Standard multipart/x-mixed-replace MJPEG over a chunked response. This is
    # the preferred stream: ordinary HTTP parsing, no custom framing.
    "liveview_multipart": ("multipart",),
    # Also a continuous stream, but with Canon's own binary framing rather than
    # multipart. Kept as a fallback for bodies lacking the multipart endpoint.
    "liveview_scroll": ("scroll",),
    "liveview_flipdetail": ("flipdetail",),
    "liveview_scrolldetail": ("scrolldetail",),
    # --- shutter (v0.1) ----------------------------------------------------
    # One-shot release. Body carries an AF enable flag.
    "shutter": ("shutterbutton",),
    # Half/full press held under explicit control. Useful later for decoupling
    # metering from release; not needed for v0.1.
    "shutter_manual": ("shutterbutton/manual", "manual"),
    # --- focus (v0.2) ------------------------------------------------------
    "focus_drive": ("drivefocus",),
    "af_control": ("af",),
    "af_method": ("afmethod",),
    "af_operation": ("afoperation",),
    # Camera-side live-view magnification, typically 1x / 5x / 10x. Worth far
    # more than a software loupe for judging grain: magnifying the downscaled
    # live-view stream on the PC only enlarges pixels the camera already threw
    # away, whereas this returns genuine sensor detail.
    "lv_zoom": ("lvzoom",),
    # --- files: listing and downloading captures (v0.1) --------------------
    "contents": ("contents",),
    # --- settings ----------------------------------------------------------
    "shooting_settings": ("shooting/settings", "settings"),
    "iso": ("iso",),
    "aperture": ("av",),
    "shutter_speed": ("tv",),
    "white_balance": ("wb",),
    "exposure": ("exposure",),
    "still_image_quality": ("stillimagequality",),
    # Values are elec_1st_curtain / mechanical / electronic. v0.1 selects
    # "electronic" to remove mechanical shutter shock on the copy stand.
    "shutter_mode": ("shuttermode",),
    "drive_mode": ("drive",),
    # --- camera behaviour during a long scanning session -------------------
    # A sleeping camera looks exactly like a wiring fault, so being able to
    # disable auto power-off over the API removes a whole class of confusion.
    "auto_power_off": ("autopoweroff",),
    "display_off": ("displayoff",),
    "viewfinder_off": ("viewfinderoff",),
    "beep": ("beep",),
    # --- event notification ------------------------------------------------
    # Polling reports state changes, including that a capture has been written,
    # which beats guessing at a fixed delay after the shutter.
    "event_polling": ("event/polling", "polling"),
    "event_monitoring": ("event/monitoring", "monitoring"),
}

# Live-view stream endpoints in preference order. Multipart first: it is a
# standard format, so it needs no custom parser and no bespoke tests.
LIVEVIEW_STREAM_PREFERENCE: tuple[str, ...] = (
    "liveview_multipart",
    "liveview_scroll",
)

# Shutter modes in preference order for scanning. Fully electronic has no moving
# parts at all; electronic first curtain still closes mechanically but avoids
# the shock at the start of the exposure, which is the part that matters.
SHUTTER_MODE_PREFERENCE: tuple[str, ...] = (
    "electronic",
    "elec_1st_curtain",
    "mechanical",
)

# Live-view output sizes, largest first. "medium" is the largest CCAPI offers;
# "off" stops the stream rather than selecting a size.
LIVEVIEW_SIZE_PREFERENCE: tuple[str, ...] = ("medium", "small")
LIVEVIEW_SIZE_OFF = "off"


def hints_for(capability: str) -> tuple[str, ...]:
    """Return the path hints for a capability, or an empty tuple if unknown."""
    return CAPABILITY_HINTS.get(capability, ())
