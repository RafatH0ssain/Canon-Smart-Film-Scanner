"""Find the camera and learn what it can do.

This is the first thing the app does after connecting, and the smallest slice
that proves the whole pipeline works: if discovery succeeds, the address is
right, CCAPI is activated, and the camera is answering.

The design point here is that we ask the camera what it supports rather than
assuming. CCAPI is self-describing -- the API root lists supported versions, and
each version root lists its endpoints -- so a body that lacks focus control or
exposes a different subset degrades gracefully instead of failing at the moment
you press a button mid-roll.

The response parsing below is deliberately shape-tolerant. Canon's exact JSON
layout is in the licensed reference, which this repository cannot contain, so
rather than encoding one assumed structure we walk the response and collect
anything path-shaped. That is a bit inelegant, and it is the right trade: it
works across API versions and cannot silently mis-parse into a wrong path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from csfs.ccapi.client import CCAPIClient
from csfs.ccapi.endpoints import ROOT_PATH_CANDIDATES, hints_for
from csfs.ccapi.errors import (
    CameraUnreachableError,
    CCAPIError,
    CCAPINotActivatedError,
    UnsupportedEndpointError,
)

# JSON keys that, when true, indicate the endpoint accepts that HTTP method.
_METHOD_KEYS = ("get", "post", "put", "delete", "patch")


@dataclass(frozen=True)
class Endpoint:
    """One endpoint the camera reported as supported."""

    path: str
    methods: frozenset[str] = frozenset()
    api_version: str = ""

    def supports(self, method: str) -> bool:
        """Whether the camera reported this endpoint as accepting ``method``.

        Returns ``True`` when the camera reported no method information at all,
        since an unstated method is unknown rather than known-unsupported.
        """
        return not self.methods or method.lower() in self.methods


@dataclass
class Capabilities:
    """What this particular camera exposes over CCAPI.

    Attributes:
        api_versions: Version name -> path of that version's root.
        endpoints: Every endpoint the camera reported, keyed by path.
        resolved: Logical capability name -> the endpoint chosen for it.
        unmatched: Reported endpoints that matched no known capability. Useful
            when calibrating ``endpoints.py`` against a real body.
    """

    api_versions: dict[str, str] = field(default_factory=dict)
    endpoints: dict[str, Endpoint] = field(default_factory=dict)
    resolved: dict[str, Endpoint] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)

    def has(self, capability: str) -> bool:
        """Whether this camera exposes ``capability``.

        Prefer this over try/except around :meth:`path` when the caller's job is
        to hide a UI control -- e.g. a manual-focus lens means no focus drive,
        and the focus buttons should simply not appear.
        """
        return capability in self.resolved

    def path(self, capability: str) -> str:
        """Path for ``capability``.

        Raises:
            UnsupportedEndpointError: If this camera does not expose it. We
                raise rather than falling back to a guessed path, because a
                guessed path fails later and less legibly.
        """
        endpoint = self.resolved.get(capability)
        if endpoint is None:
            raise UnsupportedEndpointError(capability, list(self.endpoints))
        return endpoint.path

    def latest_version(self) -> str:
        """Name of the highest API version the camera reports, or ``""``.

        Versions sort naturally by name (``ver100`` < ``ver110``), which is why
        this is a plain max rather than a parsed comparison.
        """
        return max(self.api_versions, default="")


def _walk_paths(obj: Any, version: str, out: dict[str, Endpoint]) -> None:
    """Recursively collect path-shaped entries from a decoded JSON body.

    Handles the two shapes an endpoint listing plausibly takes -- a dict with a
    ``path`` key plus per-method booleans, or a bare path string -- at any depth
    and inside any amount of surrounding structure.
    """
    if isinstance(obj, dict):
        raw_path = obj.get("path")
        if isinstance(raw_path, str) and raw_path.startswith("/"):
            methods = frozenset(m for m in _METHOD_KEYS if obj.get(m) is True)
            existing = out.get(raw_path)
            # A path can appear under several API versions. Keep the union of
            # methods and the highest version, so a capability added in a later
            # version is not masked by its earlier, more limited listing.
            if existing is not None:
                methods = methods | existing.methods
                version = max(version, existing.api_version)
            out[raw_path] = Endpoint(raw_path, methods, version)
        for value in obj.values():
            _walk_paths(value, version, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_paths(item, version, out)
    elif isinstance(obj, str) and obj.startswith("/") and obj not in out:
        out[obj] = Endpoint(obj, frozenset(), version)


def _extract_versions(obj: Any) -> dict[str, str]:
    """Pull ``{version_name: version_root_path}`` out of the API root response."""
    versions: dict[str, str] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str) and value.startswith("/"):
                versions[str(key)] = value
            elif isinstance(value, dict):
                # e.g. {"ver100": {"path": "/ccapi/ver100/"}}
                nested = value.get("path") or value.get("url")
                if isinstance(nested, str) and nested.startswith("/"):
                    versions[str(key)] = nested
    return versions


def _match_capability(hints: Iterable[str], paths: Iterable[str]) -> str | None:
    """Choose the best path for one capability from the camera's reported paths.

    Whole-segment matches beat substring matches, so a hint of ``"shutter"``
    prefers ``/.../shutterbutton`` over ``/.../shuttermode`` only when no exact
    segment matches -- and a more specific capability listed first in the hint
    tuple wins outright.
    """
    path_list = list(paths)
    for hint in hints:
        hint_lower = hint.lower()
        # Pass 1: the hint is exactly a path segment (or trailing segments).
        for path in path_list:
            segments = [s for s in path.lower().split("/") if s]
            if hint_lower in segments or path.lower().rstrip("/").endswith("/" + hint_lower):
                return path
    for hint in hints:
        # Pass 2: substring anywhere. Shorter paths win, since they are the less
        # specialised endpoint -- /liveview over /liveview/flip for "start".
        candidates = [p for p in path_list if hint.lower() in p.lower()]
        if candidates:
            return min(candidates, key=len)
    return None


def discover(client: CCAPIClient) -> Capabilities:
    """Connect to the camera and build its capability map.

    Args:
        client: A configured :class:`CCAPIClient`.

    Returns:
        The camera's :class:`Capabilities`.

    Raises:
        CameraUnreachableError: Nothing answered at the configured address.
        CCAPINotActivatedError: Something answered, but no CCAPI root was found
            at any candidate path -- the usual cause is that the one-time Canon
            activation step has not been run on this body.
    """
    caps = Capabilities()

    root_payload: Any = None
    reached_something = False
    for candidate in ROOT_PATH_CANDIDATES:
        try:
            response = client.get(candidate, expect_status=False)
        except CameraUnreachableError:
            # Nothing is listening at all. Trying the remaining candidates would
            # only repeat the same connection failure, so surface it now.
            raise
        reached_something = True
        if not response.ok:
            continue
        try:
            payload = response.json()
        except ValueError:
            continue
        versions = _extract_versions(payload)
        if versions:
            caps.api_versions = versions
            root_payload = payload
            break

    if root_payload is None:
        # We got HTTP responses but no CCAPI root. Either CCAPI was never
        # activated on this body, or something else entirely owns this address.
        # (``reached_something`` is always true here -- a total failure to connect
        # already raised above -- but the check keeps the intent explicit.)
        if reached_something:
            raise CCAPINotActivatedError(client.base_url)
        raise CameraUnreachableError(client.base_url)

    # Fetch each version root to enumerate that version's endpoints. A version
    # that fails to enumerate is skipped rather than fatal: the others may still
    # provide everything we need.
    for version_name, version_path in sorted(caps.api_versions.items()):
        try:
            payload = client.get_json(version_path)
        except CCAPIError:
            continue
        _walk_paths(payload, version_name, caps.endpoints)

    # The root response sometimes carries endpoint detail of its own.
    _walk_paths(root_payload, caps.latest_version(), caps.endpoints)

    # Resolve logical capabilities against whatever the camera actually listed.
    matched_paths: set[str] = set()
    for capability in _known_capabilities():
        path = _match_capability(hints_for(capability), caps.endpoints)
        if path is not None:
            caps.resolved[capability] = caps.endpoints[path]
            matched_paths.add(path)

    caps.unmatched = sorted(set(caps.endpoints) - matched_paths)
    return caps


def _known_capabilities() -> list[str]:
    """Capability names, most specific first.

    Ordering matters: ``shutter_mode`` must resolve before ``shutter`` so the
    broader hint cannot claim the more specific endpoint first.
    """
    from csfs.ccapi.endpoints import CAPABILITY_HINTS

    return sorted(CAPABILITY_HINTS, key=lambda name: (-len(name), name))
