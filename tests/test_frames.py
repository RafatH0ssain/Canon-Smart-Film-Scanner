"""Tests for the synthetic negative generator.

These assertions encode what makes a negative a negative. If the generator ever
stops producing a real orange mask or a real rebate, the inversion tests built
on top of it would still pass while proving nothing.
"""

from __future__ import annotations

import numpy as np

from csfs.mock.frames import make_negative

# Frames are BGR (OpenCV convention), so index 2 is red and 0 is blue.
B, G, R = 0, 1, 2


def test_shape_and_dtype() -> None:
    frame = make_negative(width=320, height=200, seed=1)
    assert frame.shape == (200, 320, 3)
    assert frame.dtype == np.uint8


def test_color_negative_has_an_orange_mask() -> None:
    """Red must dominate blue -- that cast is the whole problem v0.3 solves."""
    frame = make_negative(width=320, height=200, color=True, seed=1).astype(float)
    assert frame[..., R].mean() > frame[..., G].mean() > frame[..., B].mean()
    assert frame[..., R].mean() - frame[..., B].mean() > 30


def test_bw_negative_is_near_neutral() -> None:
    """B&W stock must not carry the colour mask, or B&W inversion tests lie."""
    frame = make_negative(width=320, height=200, color=False, seed=1).astype(float)
    spread = max(frame[..., c].mean() for c in (B, G, R)) - min(
        frame[..., c].mean() for c in (B, G, R)
    )
    assert spread < 15


def test_rebate_border_is_film_base_and_brighter_than_the_image() -> None:
    """The unexposed border is the reference patch the colour pipeline samples."""
    frame = make_negative(width=400, height=300, color=True, seed=2).astype(float)
    border = frame[:4, :, :].mean()
    interior = frame[140:160, 190:210, :].mean()
    assert border > interior


def test_density_range_is_compressed_like_real_film() -> None:
    """Real negatives never span the full 0-255, which is why naive inversion looks milky."""
    interior = make_negative(width=400, height=300, color=False, seed=3)[40:260, 40:360]
    assert interior.min() > 5
    assert interior.max() < 250


def test_focus_error_reduces_high_frequency_detail() -> None:
    """Blur must actually reduce acutance, or the focus aids cannot be tested."""
    sharp = make_negative(width=400, height=300, focus_error=0.0, seed=4).astype(float)
    soft = make_negative(width=400, height=300, focus_error=6.0, seed=4).astype(float)

    def detail(image: np.ndarray) -> float:
        # Variance of the horizontal gradient: a simple acutance proxy.
        return float(np.diff(image.mean(axis=2), axis=1).var())

    assert detail(soft) < detail(sharp)


def test_seed_makes_frames_reproducible() -> None:
    a = make_negative(width=160, height=120, seed=7)
    b = make_negative(width=160, height=120, seed=7)
    assert np.array_equal(a, b)


def test_phase_animates_the_frame() -> None:
    """The mock live view must visibly move, or 'is it live?' is unanswerable."""
    a = make_negative(width=320, height=240, phase=0.0, seed=8)
    b = make_negative(width=320, height=240, phase=2.0, seed=8)
    assert not np.array_equal(a, b)
