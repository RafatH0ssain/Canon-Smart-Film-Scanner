"""Listing and downloading files from the camera's card.

The stored-file layout differs between API versions -- one version roots the
listing at a storage name, another at a card name, a later one inserts a
directory level. This module never assumes any of that: it walks the listing the
camera gives it, following ``path`` arrays down to the leaves.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from csfs.ccapi.client import CCAPIClient
from csfs.ccapi.discovery import Capabilities
from csfs.ccapi.errors import CCAPIBusyError, CCAPIError

logger = logging.getLogger(__name__)

# File extensions that are still images worth downloading. Movies on the card
# are not scans and should not be pulled down by accident.
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".cr2", ".cr3", ".heif", ".heic", ".tif", ".tiff", ".dng"}

# How deep to follow the listing before giving up. Enough for the deepest known
# layout with room to spare, and a guard against a listing that cycles.
_MAX_DEPTH = 6

# A directory listing is a short JSON array of paths. Anything far larger is not
# a listing, and reading it would mean pulling a media file into memory.
_MAX_LISTING_BYTES = 4 * 1024 * 1024


def _paths_from(payload: Any) -> list[str]:
    """Pull the ``path`` array out of a listing response."""
    if isinstance(payload, dict):
        entries = payload.get("path")
        if isinstance(entries, list):
            return [str(e) for e in entries if isinstance(e, str)]
    return []


def _looks_like_a_file(path: str) -> bool:
    """Whether a listed path names a file rather than a directory.

    Keyed on having *any* extension, not on being a known image type. Storage
    and directory entries are bare names (``card1``, ``100EOSR7``) while files
    carry an extension, so this classifies correctly whatever the file type.

    Testing against a list of image extensions instead was a real and expensive
    bug: a movie on the card fell through as "not an image, therefore a
    directory", so the walk requested it as a listing and the camera streamed
    back the entire video to be parsed as JSON. One clip was enough to wedge a
    capture for minutes.
    """
    return bool(Path(path).suffix)


def _is_image(path: str) -> bool:
    """Whether a file is a still image worth downloading as a scan."""
    return Path(path).suffix.lower() in _IMAGE_SUFFIXES


def _fetch_listing(client: CCAPIClient, path: str) -> list[str] | None:
    """Fetch a directory listing, refusing to swallow a media file.

    Streams the response and inspects the content type before reading the body,
    so a path that turns out to name a file is abandoned after the headers
    rather than downloaded in full. Belt and braces alongside the
    classification above: misjudging one entry should cost a skipped listing,
    never a multi-megabyte download and a stalled capture.

    Returns the listed paths, or None if this was not a listing.
    """
    try:
        with client.stream(path) as response:
            if not response.ok:
                return None
            content_type = response.headers.get("Content-Type", "").lower()
            if "json" not in content_type:
                logger.debug(
                    "Skipping '%s': expected a listing, got %s", path, content_type or "unknown"
                )
                return None
            # Listings are small. Cap the read so a mislabelled response cannot
            # pull an entire file into memory.
            body = b""
            for chunk in response.iter_content(chunk_size=8192):
                body += chunk
                if len(body) > _MAX_LISTING_BYTES:
                    logger.warning("Listing at '%s' exceeded %d bytes; ignoring.",
                                   path, _MAX_LISTING_BYTES)
                    return None
        return _paths_from(json.loads(body.decode("utf-8", errors="replace")))
    except CCAPIBusyError as exc:
        # The camera refuses listings while it is still writing a frame, so this
        # is the expected answer in the moment after the shutter fires. The
        # caller polls until the file appears; warning about it every time would
        # train the user to ignore the log.
        logger.debug("Listing '%s' unavailable while the camera is busy: %s", path, exc)
        return None
    except (CCAPIError, ValueError) as exc:
        logger.warning("Could not list '%s': %s", path, exc)
        return None


def list_images(client: CCAPIClient, caps: Capabilities) -> list[str]:
    """Return the paths of every still image on the card, oldest first.

    Walks the listing tree rather than assuming its shape, so this works across
    API versions and across bodies with one or two card slots.
    """
    found: list[str] = []
    frontier = [caps.path("contents")]
    seen: set[str] = set()

    for _ in range(_MAX_DEPTH):
        if not frontier:
            break
        next_frontier: list[str] = []
        for node in frontier:
            if node in seen:
                continue
            seen.add(node)
            entries = _fetch_listing(client, node)
            if entries is None:
                continue
            for entry in entries:
                if _looks_like_a_file(entry):
                    # Movies and other non-image files live on the same card and
                    # are not scans, so they are recorded as files (never walked
                    # into) but not returned.
                    if _is_image(entry):
                        found.append(entry)
                elif entry not in seen:
                    next_frontier.append(entry)
        frontier = next_frontier

    # Camera file names increment, so sorting by name gives capture order.
    return sorted(found, key=lambda p: Path(p).name)


def latest_image(client: CCAPIClient, caps: Capabilities) -> str | None:
    """Path of the most recent still image on the card, or None if empty."""
    images = list_images(client, caps)
    return images[-1] if images else None


def download(
    client: CCAPIClient,
    caps: Capabilities,
    remote_path: str,
    destination_dir: Path,
    overwrite: bool = False,
) -> Path:
    """Download one file from the camera to ``destination_dir``.

    The file is written to a temporary name and moved into place only once it is
    complete, so an interrupted transfer can never leave a truncated image that
    later looks like a real scan.

    Args:
        remote_path: Path on the camera, as reported by the listing.
        destination_dir: Local directory; created if it does not exist.
        overwrite: Whether to replace an existing file of the same name. When
            False, a numeric suffix is added instead -- never silently
            destroying a scan you already have.

    Returns:
        The path actually written.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / Path(remote_path).name
    if target.exists() and not overwrite:
        target = _unique_name(target)

    temporary = target.with_suffix(target.suffix + ".part")
    with client.stream(remote_path) as response:
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    handle.write(chunk)
    temporary.replace(target)
    logger.info("Downloaded %s -> %s", remote_path, target)
    return target


def _unique_name(path: Path) -> Path:
    """Find an unused filename by appending _1, _2, ... to the stem."""
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def delete(client: CCAPIClient, remote_path: str) -> bool:
    """Delete a file from the camera's card. Returns True on success.

    Off by default everywhere it is called from: keeping the original on the
    card is the safer choice, and a card is cheap compared to a rescan.
    """
    try:
        client.delete(remote_path)
    except CCAPIError as exc:
        logger.warning("Could not delete '%s' from the camera: %s", remote_path, exc)
        return False
    return True
