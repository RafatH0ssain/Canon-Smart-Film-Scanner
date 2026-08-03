"""Negative-to-positive inversion.

**Scope note.** This module currently holds only the v0.1 inversion: a plain
linear flip. It is the simple preview toggle the MVP calls for, and it is
honestly not a good film inversion.

What it gets right: for black-and-white negatives it produces a perfectly usable
positive preview, which is enough to frame and review by.

What it gets wrong, and why v0.3 exists:

  - **Colour negatives come out cyan.** Colour film carries an orange mask in
    its base, and inverting that mask without first neutralising it leaves the
    complementary cast across the whole frame. Fixing it means sampling the
    unexposed film rebate and normalising against it -- not a tweak to this
    function, a different pipeline.
  - **Positives look flat and milky.** A negative never spans the full 0-255
    range; base fog sets a floor and highlight density a ceiling. Flipping that
    compressed range gives a compressed result. Proper inversion sets black and
    white points from the film's actual density range.

Both are deliberately left for v0.3 rather than half-fixed here, because a
half-fixed inversion is the kind of thing that quietly ships and never gets
revisited. ``invert_linear`` stays as the shared primitive those pipelines build
on.
"""

from __future__ import annotations

import numpy as np

# 8-bit images inverting around this maximum.
_MAX_8BIT = 255


def invert_linear(frame: np.ndarray) -> np.ndarray:
    """Invert an image by flipping every channel around the 8-bit midpoint.

    Applies identically to all three channels, so it is film-type agnostic --
    it makes no black-and-white assumption. It simply does not *correct* for
    anything colour negatives need.

    Args:
        frame: ``uint8`` array, any shape. Usually ``(H, W, 3)`` BGR.

    Returns:
        A new inverted array of the same shape and dtype. The input is not
        modified: inversion is non-destructive throughout this project, and the
        original capture is always kept.

    Raises:
        TypeError: If the frame is not 8-bit. Higher bit depths need a different
            maximum, and silently treating 16-bit data as 8-bit would produce
            a wildly wrong result rather than an obviously wrong one.
    """
    if frame.dtype != np.uint8:
        raise TypeError(
            f"invert_linear expects a uint8 image, got {frame.dtype}. "
            "Higher bit depths need their own maximum; see the RAW pipeline notes "
            "in ROADMAP.md."
        )
    return (_MAX_8BIT - frame.astype(np.uint8)).astype(np.uint8)
