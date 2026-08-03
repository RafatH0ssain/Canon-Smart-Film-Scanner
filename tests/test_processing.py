"""Tests for the processing layer.

Pure functions over synthetic arrays: no camera, no network, no server.
"""

from __future__ import annotations

import numpy as np
import pytest

from csfs.mock.frames import make_negative
from csfs.processing.codec import DecodeError, decode_jpeg, encode_jpeg
from csfs.processing.invert import invert_linear
from csfs.processing.loupe import crop_zoom


# ---------------------------------------------------------------------------
# Inversion
# ---------------------------------------------------------------------------


def test_inversion_flips_the_extremes() -> None:
    frame = np.array([[[0, 0, 0], [255, 255, 255]]], dtype=np.uint8)
    result = invert_linear(frame)
    assert result[0, 0].tolist() == [255, 255, 255]
    assert result[0, 1].tolist() == [0, 0, 0]


def test_inversion_is_its_own_inverse() -> None:
    frame = make_negative(width=64, height=48, seed=1)
    assert np.array_equal(invert_linear(invert_linear(frame)), frame)


def test_inversion_does_not_modify_the_original() -> None:
    """Non-destructive throughout: the original capture is always kept."""
    frame = make_negative(width=32, height=32, seed=2)
    before = frame.copy()
    invert_linear(frame)
    assert np.array_equal(frame, before)


def test_inversion_treats_all_channels_identically() -> None:
    """It must not assume black and white; colour must pass through the same way."""
    frame = np.array([[[10, 128, 240]]], dtype=np.uint8)
    assert invert_linear(frame)[0, 0].tolist() == [245, 127, 15]


def test_inverting_a_negative_brightens_the_dense_image_area() -> None:
    """A negative's exposed area is dark; inverted, it must become light."""
    negative = make_negative(width=200, height=150, color=False, seed=3)
    interior = (slice(40, 110), slice(40, 160))
    assert invert_linear(negative)[interior].mean() > negative[interior].mean()


def test_inversion_rejects_non_8bit_input() -> None:
    """Silently mis-scaling 16-bit data would be worse than failing."""
    with pytest.raises(TypeError, match="uint8"):
        invert_linear(np.zeros((4, 4, 3), dtype=np.uint16))


# ---------------------------------------------------------------------------
# Loupe
# ---------------------------------------------------------------------------


def test_loupe_preserves_output_size_by_default() -> None:
    frame = make_negative(width=320, height=240, seed=4)
    assert crop_zoom(frame, zoom=4.0).shape == frame.shape


def test_loupe_honours_an_explicit_output_size() -> None:
    frame = make_negative(width=320, height=240, seed=4)
    assert crop_zoom(frame, zoom=2.0, output_size=(160, 120)).shape == (120, 160, 3)


def test_loupe_magnifies_a_smaller_region() -> None:
    """At 4x the crop must cover a quarter of each dimension."""
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[40:60, 40:60] = 255  # a 20x20 patch in the middle

    # Zoomed to the centre, the patch fills much more of the view than it did.
    zoomed = crop_zoom(frame, 0.5, 0.5, zoom=4.0)
    assert (zoomed == 255).mean() > (frame == 255).mean() * 3


def test_loupe_clamps_at_the_edges_without_shrinking() -> None:
    """Dragging the loupe into a corner must still return a full-size view.

    Checking corner sharpness is one of the main uses, so the loupe breaking at
    the corners would be precisely the wrong failure.
    """
    frame = make_negative(width=200, height=160, seed=5)
    for x, y in [(0.0, 0.0), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0), (-5.0, 9.0)]:
        assert crop_zoom(frame, x, y, zoom=6.0).shape == frame.shape


def test_loupe_at_or_below_1x_returns_the_whole_frame() -> None:
    frame = make_negative(width=120, height=90, seed=6)
    assert np.array_equal(crop_zoom(frame, zoom=1.0), frame)


def test_loupe_rejects_a_non_positive_zoom() -> None:
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="zoom"):
        crop_zoom(frame, zoom=0.0)


def test_loupe_does_not_invent_detail_when_enlarging() -> None:
    """Nearest-neighbour only: smooth interpolation would fake sharpness.

    An enlarged 2-colour image must still contain exactly those 2 colours.
    """
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    frame[10:, :] = 255
    enlarged = crop_zoom(frame, zoom=4.0, output_size=(200, 200))
    assert set(np.unique(enlarged).tolist()) <= {0, 255}


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------


def test_jpeg_round_trip_preserves_shape() -> None:
    frame = make_negative(width=128, height=96, seed=7)
    assert decode_jpeg(encode_jpeg(frame)).shape == frame.shape


def test_decoding_rubbish_raises_decode_error() -> None:
    """Live-view streams do deliver partial frames; callers must be able to skip."""
    with pytest.raises(DecodeError):
        decode_jpeg(b"this is not a jpeg")


def test_decoding_empty_payload_raises_decode_error() -> None:
    with pytest.raises(DecodeError):
        decode_jpeg(b"")
