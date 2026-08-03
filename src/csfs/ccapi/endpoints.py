"""Path hints used to resolve capabilities against a camera's endpoint list.

This is the only module in the project that contains CCAPI path assumptions.
Canon licenses the API reference per developer, so the client resolves endpoints
from the inventory the camera itself publishes and matches them against the
hints below. Correcting a wrong hint therefore touches one file.

Run ``python -m csfs.tools.dump_endpoints`` against a camera to see which hints
matched, which capabilities are missing, and which endpoints went unused.

Verified against an EOS R7 (ver100/110/130, 102 endpoints): 32 of 34
capabilities resolved with no changes. It has no ``multipart`` live view and no
``lvzoom``, so the chunked ``scroll`` stream is its primary transport and the
software loupe is its only magnification.
"""

from __future__ import annotations

# Probed in order; the first returning a version list wins. The root carries the
# full endpoint inventory, so this is normally the only request discovery makes.
ROOT_PATH_CANDIDATES: tuple[str, ...] = (
    "/ccapi",
    "/ccapi/",
)

# Capability name -> path fragments that may identify it. Matching is
# case-insensitive against whole path segments, or against trailing segments for
# hints containing a slash. Earlier hints win.
CAPABILITY_HINTS: dict[str, tuple[str, ...]] = {
    "device_info": ("deviceinformation",),
    "battery": ("battery", "batterylist"),
    "storage": ("storage",),
    "temperature": ("temperature",),
    # Reports whether a lens is mounted and its name, but not whether it can
    # autofocus. Manual-lens detection lives in csfs.ccapi.focus.
    "lens": ("lens",),
    # Live view starts by POSTing an output size and stops with size "off".
    "liveview": ("liveview",),
    "liveview_flip": ("flip",),
    "liveview_multipart": ("multipart",),
    "liveview_scroll": ("scroll",),
    "liveview_flipdetail": ("flipdetail",),
    "liveview_scrolldetail": ("scrolldetail",),
    "shutter": ("shutterbutton",),
    "shutter_manual": ("shutterbutton/manual", "manual"),
    "focus_drive": ("drivefocus",),
    "af_control": ("af",),
    "af_method": ("afmethod",),
    "af_operation": ("afoperation",),
    # Camera-side live-view magnification. Absent on some bodies, including the
    # R7, in which case the software loupe is the only option.
    "lv_zoom": ("lvzoom",),
    "contents": ("contents",),
    "shooting_settings": ("shooting/settings", "settings"),
    "iso": ("iso",),
    "aperture": ("av",),
    "shutter_speed": ("tv",),
    "white_balance": ("wb",),
    "exposure": ("exposure",),
    "still_image_quality": ("stillimagequality",),
    "shutter_mode": ("shuttermode",),
    "drive_mode": ("drive",),
    "auto_power_off": ("autopoweroff",),
    "display_off": ("displayoff",),
    "viewfinder_off": ("viewfinderoff",),
    "beep": ("beep",),
    "event_polling": ("event/polling", "polling"),
    "event_monitoring": ("event/monitoring", "monitoring"),
}

# Multipart first: it is standard MJPEG and needs no custom parser.
LIVEVIEW_STREAM_PREFERENCE: tuple[str, ...] = (
    "liveview_multipart",
    "liveview_scroll",
)

# Fully electronic has no moving parts. Electronic first curtain still closes
# mechanically but avoids the shock at the start of the exposure.
SHUTTER_MODE_PREFERENCE: tuple[str, ...] = (
    "electronic",
    "elec_1st_curtain",
    "mechanical",
)

# "medium" is the largest size CCAPI offers; "off" stops the stream.
LIVEVIEW_SIZE_PREFERENCE: tuple[str, ...] = ("medium", "small")
LIVEVIEW_SIZE_OFF = "off"


def hints_for(capability: str) -> tuple[str, ...]:
    """Return the path hints for a capability, or an empty tuple if unknown."""
    return CAPABILITY_HINTS.get(capability, ())
