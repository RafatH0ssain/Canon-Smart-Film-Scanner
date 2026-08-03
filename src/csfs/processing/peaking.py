"""Focus peaking: highlight the sharpest edges so focus is obvious, not a guess.

Cameras show this in manual focus only. Computing it here instead has a
concrete advantage for this project: CCAPI cannot drive focus when the camera is
in manual focus, so the camera's own peaking and remote focus control are
mutually exclusive. Ours works in either mode.

**Order matters in the preview pipeline.** Peaking is computed at the live
view's native resolution, *before* any loupe magnification. Nearest-neighbour
enlargement leaves flat blocks with a single step at each block boundary, so
only those boundaries can clear the threshold: the overlay grows sparser the
further you zoom in, which is precisely backwards for a tool you reach for when
you want a closer look. Peaking first, then magnifying, keeps the marks
consistent at any zoom.
"""

from __future__ import annotations

import cv2
import numpy as np

# Default overlay colour, BGR. Red reads clearly against film's oranges and
# greys, and matches what cameras conventionally use.
DEFAULT_PEAK_COLOR = (0, 0, 255)

# Edge-strength thresholds, in Scharr-magnitude units, at each end of the
# sensitivity range.
#
# These are ABSOLUTE, and that is the whole design. An adaptive threshold -- a
# percentile of the frame's own edge strengths -- marks a fixed fraction of
# pixels by construction, so it reports the same coverage on a tack-sharp frame
# as on mush and cannot indicate focus at all. Peaking only means something if
# defocusing makes the marks go away.
#
# Calibrated against measured distributions from both synthetic negatives and
# real EOS R7 live-view frames: a sharp frame puts a few percent of pixels above
# ~400, while the same frame defocused puts essentially none there.
_MAX_THRESHOLD = 1200.0  # sensitivity 0.0: only the very sharpest edges
_MIN_THRESHOLD = 150.0   # sensitivity 1.0: much more permissive


def edge_strength(frame: np.ndarray) -> np.ndarray:
    """Per-pixel edge magnitude as float32.

    Uses a Scharr gradient rather than Sobel: it is the more accurate 3x3
    rotational approximation, which matters because film grain is fine,
    high-frequency, and has no preferred orientation.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    gray = gray.astype(np.float32)
    dx = cv2.Scharr(gray, cv2.CV_32F, 1, 0)
    dy = cv2.Scharr(gray, cv2.CV_32F, 0, 1)
    return cv2.magnitude(dx, dy)


def peaking_mask(frame: np.ndarray, sensitivity: float = 0.5) -> np.ndarray:
    """Boolean mask of the pixels considered in focus.

    Args:
        frame: BGR or grayscale image.
        sensitivity: 0.0 marks only the very sharpest edges, 1.0 marks far more.

    Returns:
        A boolean array shaped like the frame's first two dimensions.
    """
    sensitivity = float(np.clip(sensitivity, 0.0, 1.0))
    strength = edge_strength(frame)
    return strength >= threshold_for(sensitivity)


def threshold_for(sensitivity: float) -> float:
    """Edge-strength threshold for a sensitivity in 0.0-1.0.

    Interpolated geometrically rather than linearly, because edge strength
    spans roughly an order of magnitude between "barely an edge" and "hard
    boundary". A linear sweep would spend most of the slider's travel in a range
    where nothing visibly changes.
    """
    sensitivity = float(np.clip(sensitivity, 0.0, 1.0))
    return _MAX_THRESHOLD * (_MIN_THRESHOLD / _MAX_THRESHOLD) ** sensitivity


def apply_peaking(
    frame: np.ndarray,
    sensitivity: float = 0.5,
    color: tuple[int, int, int] = DEFAULT_PEAK_COLOR,
    opacity: float = 1.0,
) -> np.ndarray:
    """Return a copy of ``frame`` with in-focus edges highlighted.

    Args:
        frame: BGR image.
        sensitivity: See :func:`peaking_mask`.
        color: Overlay colour as BGR.
        opacity: 1.0 replaces the pixel outright; lower values blend, which
            keeps the underlying detail visible while still flagging it.

    Returns:
        A new BGR image. The input is not modified.
    """
    if frame.ndim != 3:
        raise ValueError("apply_peaking expects a colour (BGR) image.")

    mask = peaking_mask(frame, sensitivity)
    out = frame.copy()
    opacity = float(np.clip(opacity, 0.0, 1.0))
    if opacity >= 1.0:
        out[mask] = color
    else:
        overlay = np.array(color, dtype=np.float32)
        blended = out[mask].astype(np.float32) * (1.0 - opacity) + overlay * opacity
        out[mask] = blended.astype(np.uint8)
    return out


def peaking_coverage(frame: np.ndarray, sensitivity: float = 0.5) -> float:
    """Fraction of the frame marked in focus, 0.0-1.0.

    A useful "is anything actually sharp?" signal, and only meaningful because
    the threshold is absolute: a defocused frame genuinely produces almost no
    coverage rather than the same fraction as a sharp one.
    """
    return float(peaking_mask(frame, sensitivity).mean())
