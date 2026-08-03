"""A number that goes up as focus improves.

Peaking shows you *where* the sharp edges are; this says *how* sharp, which is
what you want when creeping focus back and forth to find the peak. Watching a
number rise and fall is more decisive than judging an overlay by eye,
particularly on film grain where "sharp" is a fine-textured thing.

The metric is a **gated Tenengrad**: gradient magnitudes below a noise floor are
zeroed, and the mean of the squares of what remains is reported.

The obvious alternative, variance of the Laplacian, is the textbook choice and
was tried first. It is a poor fit here. Measured on real EOS R7 live-view
frames, it separated a tack-sharp frame from a completely defocused one by only
**1.22x** -- because a compressed live-view stream has a high noise floor, and a
second-derivative operator amplifies exactly that noise. It scored well on
synthetic fixtures and badly on the camera, which is a good reminder that a
metric has to be validated on real data.

Gating at a noise floor before squaring separates the same pair of real frames
by a far wider margin, because it responds to genuine edges and ignores the
grain-sized noise that survives defocus.

**It is only meaningful as a relative measure.** The absolute value depends on
the subject, exposure and how much of the frame you measure, so comparing across
different negatives says nothing. Comparing successive frames of the *same*
subject while turning focus is exactly what it is for -- which is why the UI
reports it against the best value seen since focusing began, rather than as an
absolute score.
"""

from __future__ import annotations

import cv2
import numpy as np

from csfs.processing.peaking import edge_strength

# Gradient magnitudes below this are treated as noise, not detail. Shared in
# spirit with the peaking thresholds, and set well under the most permissive of
# them so the readout still moves in situations where peaking marks nothing.
_NOISE_FLOOR = 100.0

# Divisor purely for readability, so the UI shows tens-to-thousands rather than
# six-digit numbers. It has no effect on comparisons.
_SCALE = 1000.0


def sharpness(frame: np.ndarray) -> float:
    """Relative sharpness of a frame. Higher is sharper.

    Args:
        frame: BGR or grayscale image.

    Returns:
        Mean squared gradient magnitude above the noise floor, scaled for
        readability. Always >= 0.
    """
    strength = edge_strength(frame)
    # Gate first, then square: squaring before gating would let a large number
    # of small noise gradients accumulate into a misleadingly high score.
    gated = np.where(strength >= _NOISE_FLOOR, strength, 0.0)
    return float((gated.astype(np.float64) ** 2).mean() / _SCALE)


def sharpness_of_region(
    frame: np.ndarray,
    center_x: float = 0.5,
    center_y: float = 0.5,
    size: float = 0.25,
) -> float:
    """Sharpness of a sub-region, given in normalised coordinates.

    Measuring a region rather than the whole frame matters on a copy stand: a
    negative that is sharp in the middle and soft at the edges is a levelling
    problem, and averaging the frame hides it. This is also what lets the
    readout follow the loupe.

    Args:
        frame: BGR or grayscale image.
        center_x: Region centre, 0.0-1.0.
        center_y: Region centre, 0.0-1.0.
        size: Region side length as a fraction of the frame, 0.0-1.0.

    Returns:
        Variance of the Laplacian over the region.

    Raises:
        ValueError: If ``size`` is not positive.
    """
    if size <= 0:
        raise ValueError(f"size must be positive, got {size}")

    height, width = frame.shape[:2]
    size = float(np.clip(size, 0.0, 1.0))
    half_w = max(int(width * size / 2), 1)
    half_h = max(int(height * size / 2), 1)

    cx = int(np.clip(center_x, 0.0, 1.0) * width)
    cy = int(np.clip(center_y, 0.0, 1.0) * height)

    # Clamp so a region near an edge slides inward instead of shrinking, which
    # would otherwise make the value drop simply because fewer pixels were
    # measured -- and look exactly like going out of focus.
    left = max(0, min(cx - half_w, width - 2 * half_w))
    top = max(0, min(cy - half_h, height - 2 * half_h))
    region = frame[top : top + 2 * half_h, left : left + 2 * half_w]
    return sharpness(region)


def corner_sharpness(frame: np.ndarray, size: float = 0.18) -> dict[str, float]:
    """Sharpness at each corner and the centre.

    A copy stand whose sensor plane is not parallel to the film shows up as
    corners that differ from each other. This is the raw measurement the v0.4
    alignment checker builds on, and it is already useful by eye.

    Returns:
        Mapping of ``top_left``, ``top_right``, ``bottom_left``,
        ``bottom_right`` and ``center`` to their sharpness.
    """
    inset = size / 2 + 0.02
    points = {
        "top_left": (inset, inset),
        "top_right": (1.0 - inset, inset),
        "bottom_left": (inset, 1.0 - inset),
        "bottom_right": (1.0 - inset, 1.0 - inset),
        "center": (0.5, 0.5),
    }
    return {
        name: sharpness_of_region(frame, x, y, size) for name, (x, y) in points.items()
    }
