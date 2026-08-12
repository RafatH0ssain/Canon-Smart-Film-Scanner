"""JPEG encode/decode helpers.

Isolated here so the rest of the processing layer deals only in arrays, and so
the live-view pipeline has exactly one place where compression settings live.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

# Quality for frames sent to the browser. High enough that the loupe is honest
# about focus -- compression artefacts at low quality look a lot like grain,
# which would defeat the point of the magnified view.
PREVIEW_JPEG_QUALITY = 88


class DecodeError(ValueError):
    """Raised when bytes could not be decoded as an image."""


def decode_jpeg(payload: bytes) -> np.ndarray:
    """Decode JPEG bytes to a BGR array.

    Raises:
        DecodeError: If the payload is not a decodable image. Live-view streams
            do occasionally deliver a partial frame; callers should skip those
            rather than tearing down the session.
    """
    if not payload:
        raise DecodeError("Empty payload.")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            rgb = np.asarray(image.convert("RGB"))
    except Exception as exc:  # Pillow raises several unrelated types
        raise DecodeError(f"Could not decode {len(payload)} bytes as an image.") from exc
    # BGR, because that is what the rest of this layer has always dealt in and
    # what every downstream index assumes. Pillow hands back RGB.
    return np.ascontiguousarray(rgb[:, :, ::-1])


def encode_jpeg(frame: np.ndarray, quality: int = PREVIEW_JPEG_QUALITY) -> bytes:
    """Encode a BGR array as JPEG bytes.

    Raises:
        ValueError: If encoding fails.
    """
    if frame.ndim == 3:
        frame = frame[:, :, ::-1]  # BGR in, RGB for the encoder
    buffer = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(frame)).save(
        buffer, format="JPEG", quality=int(quality)
    )
    return buffer.getvalue()
