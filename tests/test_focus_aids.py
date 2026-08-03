"""Tests for the focus aids: peaking and the sharpness metric.

Pure functions over synthetic images, so the assertions can be about what the
aids are *for* -- distinguishing sharp from soft -- rather than about pixels.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from csfs.mock.frames import make_negative
from csfs.processing.loupe import crop_zoom
from csfs.processing.peaking import (
    DEFAULT_PEAK_COLOR,
    apply_peaking,
    peaking_coverage,
    peaking_mask,
)
from csfs.processing.sharpness import corner_sharpness, sharpness, sharpness_of_region


def _sharp() -> np.ndarray:
    return make_negative(width=400, height=300, focus_error=0.0, seed=1)


def _soft() -> np.ndarray:
    return make_negative(width=400, height=300, focus_error=8.0, seed=1)


# ---------------------------------------------------------------------------
# Peaking
# ---------------------------------------------------------------------------


def test_peaking_marks_more_of_a_sharp_frame_than_a_soft_one() -> None:
    """The entire point: peaking must discriminate focus."""
    assert peaking_coverage(_sharp()) > peaking_coverage(_soft()) * 2


def test_peaking_barely_marks_a_badly_defocused_frame() -> None:
    """A soft frame should look obviously unpeaked, not merely less peaked."""
    assert peaking_coverage(_soft(), sensitivity=0.5) < 0.02


def test_peaking_marks_nothing_on_a_flat_image() -> None:
    """No edges means no focus information; highlighting noise would mislead."""
    flat = np.full((100, 100, 3), 128, dtype=np.uint8)
    assert peaking_coverage(flat) == 0.0


def test_higher_sensitivity_marks_more() -> None:
    frame = _sharp()
    low = peaking_coverage(frame, sensitivity=0.0)
    high = peaking_coverage(frame, sensitivity=1.0)
    assert high > low


def test_peaking_finds_a_known_edge() -> None:
    """A single hard edge must be marked, and the flat areas must not be."""
    frame = np.zeros((60, 60, 3), dtype=np.uint8)
    frame[:, 30:] = 255
    mask = peaking_mask(frame, sensitivity=0.5)
    assert mask[:, 27:33].any()
    assert not mask[:, :20].any()
    assert not mask[:, 40:].any()


def test_peaking_overlay_uses_the_peak_colour() -> None:
    frame = np.zeros((60, 60, 3), dtype=np.uint8)
    frame[:, 30:] = 255
    peaked = apply_peaking(frame, sensitivity=0.5)
    mask = peaking_mask(frame, sensitivity=0.5)
    assert (peaked[mask] == np.array(DEFAULT_PEAK_COLOR)).all()


def test_peaking_does_not_modify_the_original() -> None:
    frame = _sharp()
    before = frame.copy()
    apply_peaking(frame)
    assert np.array_equal(frame, before)


def test_peaking_leaves_unmarked_pixels_untouched() -> None:
    frame = _sharp()
    peaked = apply_peaking(frame, sensitivity=0.3)
    mask = peaking_mask(frame, sensitivity=0.3)
    assert np.array_equal(peaked[~mask], frame[~mask])


def test_peaking_opacity_blends_rather_than_replaces() -> None:
    frame = np.zeros((60, 60, 3), dtype=np.uint8)
    frame[:, 30:] = 255
    blended = apply_peaking(frame, sensitivity=0.5, opacity=0.5)
    mask = peaking_mask(frame, sensitivity=0.5)
    assert not (blended[mask] == np.array(DEFAULT_PEAK_COLOR)).all()


def test_peaking_rejects_grayscale_input() -> None:
    with pytest.raises(ValueError, match="BGR"):
        apply_peaking(np.zeros((10, 10), dtype=np.uint8))


def test_peaking_must_run_before_the_loupe_not_after() -> None:
    """Peaking a magnified frame under-marks, so the pipeline peaks first.

    Nearest-neighbour enlargement leaves flat blocks with a single step at each
    boundary, so only boundaries can clear the threshold and the overlay grows
    sparser the further you zoom -- backwards for a tool you reach for when you
    want a closer look. This pins the ordering by showing what the wrong order
    costs.
    """
    sharp = _sharp()
    before_zoom = peaking_coverage(sharp, sensitivity=0.5)
    after_zoom = peaking_coverage(crop_zoom(sharp, zoom=8.0), sensitivity=0.5)
    assert before_zoom > 0
    assert after_zoom < before_zoom


def test_threshold_is_absolute_not_a_percentile() -> None:
    """The property the whole aid depends on.

    A percentile threshold marks a fixed fraction of pixels by construction, so
    it reports the same coverage on a sharp frame as on mush. Coverage must
    genuinely fall as focus degrades.
    """
    coverages = [
        peaking_coverage(
            make_negative(width=400, height=300, focus_error=e, seed=1), sensitivity=0.5
        )
        for e in (0.0, 2.0, 4.0, 8.0)
    ]
    assert coverages == sorted(coverages, reverse=True)
    assert coverages[0] > coverages[-1] * 5


def test_sensitivity_maps_to_a_falling_threshold() -> None:
    from csfs.processing.peaking import threshold_for

    assert threshold_for(0.0) > threshold_for(0.5) > threshold_for(1.0)
    assert threshold_for(-1.0) == threshold_for(0.0)   # clamped
    assert threshold_for(9.0) == threshold_for(1.0)


# ---------------------------------------------------------------------------
# Sharpness
# ---------------------------------------------------------------------------


def test_sharpness_is_higher_for_a_sharp_frame() -> None:
    assert sharpness(_sharp()) > sharpness(_soft())


def test_sharpness_decreases_monotonically_with_defocus() -> None:
    """Focusing is a hill-climb, so the metric must be monotonic to be usable."""
    values = [
        sharpness(make_negative(width=300, height=200, focus_error=e, seed=2))
        for e in (0.0, 2.0, 4.0, 8.0)
    ]
    assert values == sorted(values, reverse=True)


def test_sharpness_is_never_negative() -> None:
    assert sharpness(np.zeros((50, 50, 3), dtype=np.uint8)) >= 0.0


def test_region_sharpness_follows_the_region() -> None:
    """Half sharp, half blurred: the metric must tell the halves apart."""
    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    noise = np.random.default_rng(0).integers(0, 255, (200, 200, 3), dtype=np.uint8)
    frame[:, :200] = noise                       # busy, high-frequency
    frame[:, 200:] = cv2.GaussianBlur(noise, (21, 21), 0)  # smooth

    left = sharpness_of_region(frame, center_x=0.25, center_y=0.5, size=0.3)
    right = sharpness_of_region(frame, center_x=0.75, center_y=0.5, size=0.3)
    assert left > right


def test_region_sharpness_clamps_at_edges_without_shrinking() -> None:
    """A region near an edge must slide inward, not measure fewer pixels.

    Otherwise the value drops purely because less was measured, which looks
    exactly like going out of focus.

    Uses uniform noise rather than a synthetic negative: that fixture has a film
    rebate, whose hard border falls inside a corner region and legitimately
    scores far higher than the centre, which would mask the property under test.
    """
    frame = np.random.default_rng(4).integers(0, 255, (300, 400, 3), dtype=np.uint8)
    centre = sharpness_of_region(frame, 0.5, 0.5, size=0.3)
    corner = sharpness_of_region(frame, 0.0, 0.0, size=0.3)
    assert corner > 0
    # Statistically identical texture everywhere, so a clamped region measuring
    # the same number of pixels must score within a few percent of the centre.
    assert 0.9 < corner / centre < 1.1


def test_region_sharpness_rejects_a_non_positive_size() -> None:
    with pytest.raises(ValueError, match="size"):
        sharpness_of_region(_sharp(), size=0.0)


def test_corner_sharpness_reports_all_five_points() -> None:
    corners = corner_sharpness(_sharp())
    assert set(corners) == {
        "top_left", "top_right", "bottom_left", "bottom_right", "center"
    }
    assert all(v >= 0 for v in corners.values())


def test_corner_sharpness_detects_a_tilted_plane() -> None:
    """A stand that is not parallel shows as one side softer than the other.

    This is the measurement v0.4's alignment checker is built on.
    """
    rng = np.random.default_rng(3)
    frame = rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)
    # Blur the right-hand side only, as a tilted film plane would.
    frame[:, 200:] = cv2.GaussianBlur(frame[:, 200:], (15, 15), 0)

    corners = corner_sharpness(frame)
    assert corners["top_left"] > corners["top_right"]
    assert corners["bottom_left"] > corners["bottom_right"]
