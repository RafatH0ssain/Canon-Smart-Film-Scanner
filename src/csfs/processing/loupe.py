"""The magnified focus loupe.

Judging whether film *grain* is sharp is impossible on a full-frame preview
scaled down to fit a browser window, so the loupe crops a small region and
scales it up.

An important caveat this module cannot fix on its own: magnifying a live-view
frame on the PC only enlarges pixels the camera already discarded when it
downscaled the stream. It genuinely helps -- a 1:1 view of the stream beats a
shrunk-to-fit one -- but where the body supports camera-side live-view zoom, the
app should drive that instead, because it returns real sensor detail. The two
compose: camera zoom first, then this for the final magnification step.
"""

from __future__ import annotations

import cv2
import numpy as np


def crop_zoom(
    frame: np.ndarray,
    center_x: float = 0.5,
    center_y: float = 0.5,
    zoom: float = 2.0,
    output_size: tuple[int, int] | None = None,
) -> np.ndarray:
    """Crop a region around a point and scale it up.

    Args:
        frame: Source image, ``(H, W, 3)``.
        center_x: Horizontal centre of the region, normalised 0.0-1.0.
        center_y: Vertical centre of the region, normalised 0.0-1.0.
        zoom: Magnification. 1.0 returns the whole frame; 4.0 crops a quarter of
            the width and height.
        output_size: ``(width, height)`` of the result. Defaults to the input's
            size, so the loupe fills the same space the full view did.

    Returns:
        A new array. The crop is clamped to stay inside the frame, so a centre
        near an edge slides the region inward rather than producing a smaller
        or padded result -- the alternative makes the loupe appear to break
        exactly when you drag it to a corner to check the corners, which is one
        of the things it is most useful for.

    Raises:
        ValueError: If ``zoom`` is not positive.
    """
    if zoom <= 0:
        raise ValueError(f"zoom must be positive, got {zoom}")

    height, width = frame.shape[:2]
    target_w, target_h = output_size if output_size is not None else (width, height)

    if zoom <= 1.0:
        return _resize(frame, target_w, target_h)

    crop_w = max(int(round(width / zoom)), 1)
    crop_h = max(int(round(height / zoom)), 1)

    # Clamp the centre so the crop window stays fully inside the frame.
    left = int(round(np.clip(center_x, 0.0, 1.0) * width - crop_w / 2))
    top = int(round(np.clip(center_y, 0.0, 1.0) * height - crop_h / 2))
    left = max(0, min(left, width - crop_w))
    top = max(0, min(top, height - crop_h))

    region = frame[top : top + crop_h, left : left + crop_w]
    return _resize(region, target_w, target_h)


def _resize(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    """Resize to exactly ``width`` x ``height``.

    Uses nearest-neighbour when enlarging. Smooth interpolation would invent
    detail that is not in the data and make an out-of-focus frame look
    acceptably sharp -- the precise mistake the loupe exists to prevent.
    """
    if frame.shape[1] == width and frame.shape[0] == height:
        return frame.copy()
    enlarging = width > frame.shape[1] or height > frame.shape[0]
    interpolation = cv2.INTER_NEAREST if enlarging else cv2.INTER_AREA
    return cv2.resize(frame, (width, height), interpolation=interpolation)
