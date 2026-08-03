"""The one place in this project where CCAPI path assumptions are allowed.

WHY THIS FILE EXISTS
--------------------
Canon licenses the CCAPI reference documentation per developer and forbids
redistributing it, so this repository cannot contain Canon's endpoint tables.
Guessing paths from memory is worse than useless: a wrong path fails in a way
that looks like a broken camera, and costs an afternoon to diagnose.

So the client resolves capabilities the self-describing way instead. CCAPI
reports the endpoints a given body actually supports; discovery reads that list
at connect time and matches it against the *hints* below. That means:

  - Different camera bodies exposing different subsets of the API just work.
  - When a hint is wrong, exactly one file needs correcting -- this one.
  - Nothing else in the codebase contains a literal CCAPI path.

CALIBRATING THIS FILE AGAINST YOUR REFERENCE
--------------------------------------------
Everything below is marked UNVERIFIED. It is a starting guess, not documentation.
To correct it:

  1. Open your own copy of Canon's CCAPI reference (never commit it -- the path
     ``ccapi_reference/`` is git-ignored for exactly this).
  2. Run ``python -m csfs.tools.dump_endpoints`` against your camera. It prints
     every path the camera reports, and which capability each one matched.
  3. For anything reported as unmatched, add the distinguishing path segment to
     the relevant tuple below.

You do not need to enumerate every endpoint -- only enough of a hint to pick the
right one out of the camera's own list unambiguously.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Root path candidates -- UNVERIFIED
# ---------------------------------------------------------------------------
# Discovery probes these against ``camera.base_url`` in order and uses the first
# that returns a JSON body listing API versions. Probing rather than assuming
# means a wrong first guess costs one extra request, not a failed connection.
#
# If none of these work, your reference's "service discovery" or "API root"
# section names the correct one; add it at the front of this tuple.
ROOT_PATH_CANDIDATES: tuple[str, ...] = (
    "/ccapi/",
    "/ccapi",
)

# ---------------------------------------------------------------------------
# Capability hints -- UNVERIFIED
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
    # --- identity & capability reporting ---
    "device_info": ("deviceinformation", "devicestatus/deviceinformation"),
    "battery": ("battery", "batterylist"),
    "storage": ("storage", "devicestatus/storage"),
    # --- live view (v0.1) ---
    # Canon exposes live view as a "start/stop the session" endpoint plus a
    # separate one to actually pull frames, hence three capabilities.
    "liveview_start": ("liveview",),
    "liveview_flip": ("flip", "liveview/flip"),
    "liveview_scroll": ("scroll", "liveview/scroll"),
    # --- shutter (v0.1) ---
    "shutter": ("shutterbutton", "shutter"),
    "shutter_manual": ("shutterbuttonmanual",),
    # --- focus (v0.2) ---
    "focus_drive": ("drivefocus", "focus"),
    "af_mode": ("afmethod", "afoperation"),
    # --- files: listing and downloading captures (v0.1) ---
    "contents": ("contents",),
    # --- settings, for locking exposure across a roll (v0.6) ---
    "shooting_settings": ("shootingsetting", "shooting/settings"),
    "iso": ("iso",),
    "aperture": ("av", "aperture"),
    "shutter_speed": ("tv", "shutterspeed"),
    "white_balance": ("wb", "whitebalance"),
    "shutter_mode": ("shuttermode",),  # needed to select the electronic shutter
    # --- event polling, used to notice that a capture finished ---
    "event_polling": ("event/polling", "polling"),
    "event_monitoring": ("event/monitoring", "monitoring"),
}


def hints_for(capability: str) -> tuple[str, ...]:
    """Return the path hints for a capability, or an empty tuple if unknown."""
    return CAPABILITY_HINTS.get(capability, ())
