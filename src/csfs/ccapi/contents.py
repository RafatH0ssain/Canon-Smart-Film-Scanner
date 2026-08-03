"""Listing and downloading files from the camera's card.

The stored-file layout differs between API versions and bodies, so this walks
whatever listing the camera returns rather than assuming a shape.
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

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".cr2", ".cr3", ".heif", ".heic", ".tif", ".tiff", ".dng"}

_MAX_DEPTH = 6

# A listing is a short JSON array of paths. Anything far larger is not a
# listing, and reading it would pull a media file into memory.
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

    Keyed on having any extension, not on being a known image type. Storage and
    directory entries are bare names; files carry an extension. Testing against
    a list of image types instead made a movie on the card look like a directory,
    so the walk requested it as a listing and the camera returned the video.
    """
    return bool(Path(path).suffix)


def _is_image(path: str) -> bool:
    """Whether a file is a still image worth downloading as a scan."""
    return Path(path).suffix.lower() in _IMAGE_SUFFIXES


def _fetch_listing(client: CCAPIClient, path: str) -> list[str] | None:
    """Fetch a directory listing, or None if the path was not one.

    Streams and checks the content type before reading, so a path that turns out
    to name a file is abandoned after the headers rather than downloaded.
    """
    try:
        with client.stream(path) as response:
            if not response.ok:
                return None
            content_type = response.headers.get("Content-Type", "").lower()
            if "json" not in content_type:
                logger.debug("Skipping '%s': got %s", path, content_type or "unknown")
                return None
            body = b""
            for chunk in response.iter_content(chunk_size=8192):
                body += chunk
                if len(body) > _MAX_LISTING_BYTES:
                    logger.warning("Listing at '%s' too large; ignoring.", path)
                    return None
        return _paths_from(json.loads(body.decode("utf-8", errors="replace")))
    except CCAPIBusyError as exc:
        # Expected while the camera is still writing a frame; the caller polls.
        logger.debug("Listing '%s' unavailable, camera busy: %s", path, exc)
        return None
    except (CCAPIError, ValueError) as exc:
        logger.warning("Could not list '%s': %s", path, exc)
        return None


def list_images(client: CCAPIClient, caps: Capabilities) -> list[str]:
    """Return the paths of every still image on the card(s), oldest first."""
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
                    if _is_image(entry):
                        found.append(entry)
                elif entry not in seen:
                    next_frontier.append(entry)
        frontier = next_frontier

    # Camera file names increment, so sorting by name gives capture order.
    return sorted(found, key=lambda p: Path(p).name)


def latest_image(client: CCAPIClient, caps: Capabilities) -> str | None:
    """Path of the most recent still image, or None if there are none."""
    images = list_images(client, caps)
    return images[-1] if images else None


def download(
    client: CCAPIClient,
    caps: Capabilities,
    remote_path: str,
    destination_dir: Path,
    overwrite: bool = False,
) -> Path:
    """Download one file to ``destination_dir`` and return the path written.

    Written to a temporary name and moved into place once complete, so an
    interrupted transfer cannot leave a truncated image that looks like a scan.
    Without ``overwrite`` an existing file gets a numeric suffix rather than
    being replaced.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / Path(remote_path).name
    if target.exists() and not overwrite:
        target = _unique_name(target)

    temporary = target.with_suffix(target.suffix + ".part")
    with client.stream(remote_path) as response, temporary.open("wb") as handle:
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
    """Delete a file from the card. Returns True on success."""
    try:
        client.delete(remote_path)
    except CCAPIError as exc:
        logger.warning("Could not delete '%s': %s", remote_path, exc)
        return False
    return True
