"""Test helpers that used to come from OpenCV.

These measure focus and blur images in the focus tests. They only have to
behave like the originals well enough for a *sharp versus blurred* comparison
to hold -- these tests assert one frame is sharper than another, never a
specific number.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from csfs.processing.grey import to_grey


def decode_bgr(payload: bytes) -> np.ndarray:
    """Decode JPEG bytes to BGR, like ``cv2.imdecode(..., IMREAD_COLOR)``."""
    with Image.open(io.BytesIO(payload)) as image:
        return np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1])


def laplacian_variance(frame: np.ndarray) -> float:
    """Variance of the Laplacian: the classic cheap focus measure.

    Kernel ``[[0,1,0],[1,-4,1],[0,1,0]]``, matching OpenCV's default 3x3.
    """
    grey = to_grey(frame).astype(np.float64)
    padded = np.pad(grey, 1, mode="reflect")
    laplacian = (
        padded[:-2, 1:-1] + padded[2:, 1:-1] + padded[1:-1, :-2] + padded[1:-1, 2:]
        - 4.0 * padded[1:-1, 1:-1]
    )
    return float(laplacian.var())


def gaussian_blur(frame: np.ndarray, ksize: int) -> np.ndarray:
    """Separable Gaussian blur, sigma derived as OpenCV does from the kernel size."""
    if ksize % 2 == 0:
        raise ValueError("ksize must be odd")
    sigma = 0.3 * ((ksize - 1) * 0.5 - 1) + 0.8
    radius = ksize // 2
    offsets = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-(offsets**2) / (2 * sigma**2))
    kernel /= kernel.sum()

    working = frame.astype(np.float64)
    if working.ndim == 2:
        working = working[:, :, None]

    for axis in (0, 1):
        padded = np.pad(
            working,
            [(radius, radius) if a == axis else (0, 0) for a in range(3)],
            mode="reflect",
        )
        stacked = np.stack(
            [
                padded[i : i + working.shape[0]] if axis == 0
                else padded[:, i : i + working.shape[1]]
                for i in range(ksize)
            ]
        )
        working = np.tensordot(kernel, stacked, axes=(0, 0))

    result = np.clip(working, 0, 255).astype(frame.dtype)
    return result[:, :, 0] if frame.ndim == 2 else result
