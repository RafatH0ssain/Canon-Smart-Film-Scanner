"""Tests for the capture path: shutter selection, settle delay, download."""

from __future__ import annotations

import time
from pathlib import Path

from csfs.ccapi import CCAPIClient, discover
from csfs.ccapi import contents as contents_api
from csfs.ccapi import settings as settings_api
from csfs.ccapi import shutter as shutter_api


# ---------------------------------------------------------------------------
# Shutter mode
# ---------------------------------------------------------------------------


def test_quietest_shutter_selects_fully_electronic(client: CCAPIClient) -> None:
    """The whole point: an electronic shutter has no moving parts to shake the rig."""
    caps = discover(client)
    assert shutter_api.select_quietest_shutter(client, caps) == "electronic"
    assert client.get_json(caps.path("shutter_mode"))["value"] == "electronic"


def test_shutter_selection_survives_a_camera_without_the_setting(
    client: CCAPIClient,
) -> None:
    """Some bodies do not expose shutter mode. That is not a reason to refuse to scan."""
    caps = discover(client)
    del caps.resolved["shutter_mode"]
    assert shutter_api.select_quietest_shutter(client, caps) is None


def test_preference_falls_back_when_electronic_is_unavailable() -> None:
    """Electronic first curtain still beats fully mechanical for a copy stand."""
    setting = settings_api.Setting(
        name="shutter_mode", value="mechanical",
        ability=("elec_1st_curtain", "mechanical"),
    )
    from csfs.ccapi.endpoints import SHUTTER_MODE_PREFERENCE

    assert settings_api.choose_supported(SHUTTER_MODE_PREFERENCE, setting) == "elec_1st_curtain"


def test_preference_returns_none_when_nothing_matches() -> None:
    setting = settings_api.Setting(name="x", value="a", ability=("a", "b"))
    assert settings_api.choose_supported(("c", "d"), setting) is None


# ---------------------------------------------------------------------------
# Settle delay
# ---------------------------------------------------------------------------


def test_capture_waits_the_settle_delay_before_firing(client: CCAPIClient) -> None:
    """The delay must precede the exposure, or it does nothing for vibration."""
    caps = discover(client)
    fired_at: list[float] = []

    original = shutter_api.release

    def spy(c, caps_, af=False):
        fired_at.append(time.monotonic())
        original(c, caps_, af=af)

    shutter_api.release = spy  # type: ignore[assignment]
    try:
        started = time.monotonic()
        shutter_api.capture(client, caps, settle_delay_s=0.4)
    finally:
        shutter_api.release = original  # type: ignore[assignment]

    assert fired_at, "shutter never fired"
    assert fired_at[0] - started >= 0.4


def test_capture_with_zero_delay_adds_no_wait(client: CCAPIClient) -> None:
    """Compared against a bare release, since the mock spends real time
    rendering a full-resolution capture."""
    caps = discover(client)

    started = time.monotonic()
    shutter_api.release(client, caps)
    release_only = time.monotonic() - started

    started = time.monotonic()
    shutter_api.capture(client, caps, settle_delay_s=0.0)
    with_capture = time.monotonic() - started

    assert with_capture - release_only < 0.3


def test_release_defaults_to_no_autofocus(client: CCAPIClient) -> None:
    """AF hunts on grain. The mock nudges focus when AF is requested, so
    unchanged settings prove it was not asked for."""
    caps = discover(client)
    before = client.get_json(caps.path("shooting_settings"))
    shutter_api.release(client, caps)
    assert client.get_json(caps.path("shooting_settings")) == before


# ---------------------------------------------------------------------------
# Contents
# ---------------------------------------------------------------------------


def test_listing_finds_captures_by_walking_the_tree(client: CCAPIClient) -> None:
    """The stored-file layout differs by API version, so it must be walked."""
    caps = discover(client)
    assert contents_api.list_images(client, caps) == []

    shutter_api.release(client, caps)
    shutter_api.release(client, caps)
    images = contents_api.list_images(client, caps)
    assert len(images) == 2
    assert all(path.endswith(".JPG") for path in images)


def test_latest_image_is_the_newest(client: CCAPIClient) -> None:
    caps = discover(client)
    shutter_api.release(client, caps)
    shutter_api.release(client, caps)
    assert contents_api.latest_image(client, caps).endswith("IMG_0002.JPG")


def test_download_writes_the_file(client: CCAPIClient, tmp_path: Path) -> None:
    caps = discover(client)
    shutter_api.release(client, caps)
    remote = contents_api.latest_image(client, caps)

    written = contents_api.download(client, caps, remote, tmp_path)
    assert written.exists()
    assert written.name == "IMG_0001.JPG"
    assert written.stat().st_size > 1000


def test_download_leaves_no_partial_file_behind(client: CCAPIClient, tmp_path: Path) -> None:
    """A truncated download must never be mistaken for a finished scan."""
    caps = discover(client)
    shutter_api.release(client, caps)
    contents_api.download(client, caps, contents_api.latest_image(client, caps), tmp_path)
    assert list(tmp_path.glob("*.part")) == []


def test_download_does_not_overwrite_an_existing_scan(
    client: CCAPIClient, tmp_path: Path
) -> None:
    """Silently destroying a scan you already have would be unforgivable."""
    caps = discover(client)
    shutter_api.release(client, caps)
    remote = contents_api.latest_image(client, caps)

    first = contents_api.download(client, caps, remote, tmp_path)
    second = contents_api.download(client, caps, remote, tmp_path)
    assert first.exists() and second.exists()
    assert first != second
    assert second.name == "IMG_0001_1.JPG"


def test_download_can_overwrite_when_asked(client: CCAPIClient, tmp_path: Path) -> None:
    caps = discover(client)
    shutter_api.release(client, caps)
    remote = contents_api.latest_image(client, caps)

    first = contents_api.download(client, caps, remote, tmp_path)
    second = contents_api.download(client, caps, remote, tmp_path, overwrite=True)
    assert first == second
    assert len(list(tmp_path.glob("*.JPG"))) == 1


def test_movies_are_not_downloaded_as_scans(client: CCAPIClient) -> None:
    """Only still images are scans; a movie on the card must be ignored."""
    caps = discover(client)
    shutter_api.release(client, caps)
    for path in contents_api.list_images(client, caps):
        assert not path.upper().endswith(".MP4")


def test_a_movie_on_the_card_does_not_stall_the_listing(client: CCAPIClient) -> None:
    """Listing must stay fast and must not fetch file contents.

    Classifying entries as "image, therefore a file; anything else, therefore a
    directory" made the walk request a movie as a listing.
    """
    caps = discover(client)
    shutter_api.release(client, caps)

    started = time.monotonic()
    images = contents_api.list_images(client, caps)
    elapsed = time.monotonic() - started

    assert elapsed < 10.0, f"listing took {elapsed:.1f}s -- it is fetching file contents"
    assert len(images) == 1
    assert images[0].endswith(".JPG")


def test_listing_walks_every_card(client: CCAPIClient) -> None:
    """Bodies with two slots put captures on either one."""
    caps = discover(client)
    root = client.get_json(caps.path("contents"))["path"]
    assert len(root) == 2


def test_unknown_file_types_are_never_walked_into(client: CCAPIClient) -> None:
    """Anything with an extension is a file, whatever the extension is."""
    assert contents_api._looks_like_a_file("/ccapi/ver130/contents/card1/X/IMG_1.CR4")
    assert contents_api._looks_like_a_file("/ccapi/ver130/contents/card1/X/CLIP.XYZ")
    assert not contents_api._looks_like_a_file("/ccapi/ver130/contents/card1")
    assert not contents_api._looks_like_a_file("/ccapi/ver130/contents/card1/100EOSR7")


def test_listing_ignores_a_response_that_is_not_a_listing(client: CCAPIClient) -> None:
    """A path that turns out to be a file is abandoned after the headers."""
    caps = discover(client)
    shutter_api.release(client, caps)
    card = client.get_json(caps.path("contents"))["path"][0]
    image = client.get_json(client.get_json(card)["path"][0])["path"][0]

    assert contents_api._fetch_listing(client, image) is None


# ---------------------------------------------------------------------------
# Session-level behaviour
# ---------------------------------------------------------------------------


def test_auto_power_off_is_disabled_for_the_session(client: CCAPIClient) -> None:
    """A sleeping camera is indistinguishable from a wiring fault."""
    caps = discover(client)
    assert settings_api.disable_auto_power_off(client, caps) is True
    assert client.get_json(caps.path("auto_power_off"))["value"] == "disable"
