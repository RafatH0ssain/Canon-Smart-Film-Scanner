"""Find the camera and learn what it can do.

The first thing the app does after connecting, and the smallest slice that
proves the pipeline: if discovery succeeds, the address is right, CCAPI is
activated, and the camera is answering.

Capabilities are resolved from what the camera reports rather than assumed, so
a body exposing a different subset degrades gracefully instead of failing when
you press a button mid-roll. Response parsing is shape-tolerant for the same
reason: it walks the response collecting anything path-shaped rather than
encoding one assumed structure.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from csfs.ccapi.client import CCAPIClient
from csfs.ccapi.endpoints import ROOT_PATH_CANDIDATES, hints_for
from csfs.ccapi.errors import (
    CCAPIError,
    CCAPINotActivatedError,
    UnsupportedEndpointError,
)

_METHOD_KEYS = ("get", "post", "put", "delete", "patch")


@dataclass(frozen=True)
class Endpoint:
    """One endpoint the camera reported as supported."""

    path: str
    methods: frozenset[str] = frozenset()
    api_version: str = ""

    def supports(self, method: str) -> bool:
        """Whether the camera reported this endpoint as accepting ``method``.

        True when no method information was reported: unstated is unknown, not
        known-unsupported.
        """
        return not self.methods or method.lower() in self.methods


@dataclass
class Capabilities:
    """What this particular camera exposes over CCAPI.

    Attributes:
        api_versions: Version name -> path of that version's root.
        endpoints: Every endpoint the camera reported, keyed by path.
        resolved: Logical capability name -> the endpoint chosen for it.
        unmatched: Reported endpoints that matched no known capability.
    """

    api_versions: dict[str, str] = field(default_factory=dict)
    endpoints: dict[str, Endpoint] = field(default_factory=dict)
    resolved: dict[str, Endpoint] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)

    def has(self, capability: str) -> bool:
        """Whether this camera exposes ``capability``.

        Prefer this over catching from :meth:`path` when deciding whether to
        show a UI control.
        """
        return capability in self.resolved

    def path(self, capability: str) -> str:
        """Path for ``capability``.

        Raises:
            UnsupportedEndpointError: If this camera does not expose it. Raised
                rather than falling back to a guess, which would fail later and
                less legibly.
        """
        endpoint = self.resolved.get(capability)
        if endpoint is None:
            raise UnsupportedEndpointError(capability, list(self.endpoints))
        return endpoint.path

    def latest_version(self) -> str:
        """Name of the highest API version reported, or ``""``."""
        return max(self.api_versions, default="")


def _walk_paths(obj: Any, version: str, out: dict[str, Endpoint]) -> None:
    """Recursively collect path-shaped entries from a decoded JSON body."""
    if isinstance(obj, dict):
        raw_path = obj.get("path")
        if isinstance(raw_path, str) and raw_path.startswith("/"):
            methods = frozenset(m for m in _METHOD_KEYS if obj.get(m) is True)
            existing = out.get(raw_path)
            # A path can appear under several versions. Keep the union of
            # methods and the highest version, so a capability added later is
            # not masked by its earlier, more limited listing.
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


def _extract_versions(obj: Any) -> tuple[dict[str, str], dict[str, Any]]:
    """Pull the version listing out of the API root response.

    The root maps each version to an array of endpoint objects, so the whole
    inventory normally arrives in this one response::

        {"ver100": [{"path": "...", "get": true, ...}, ...], "ver110": [...]}

    A version given as a bare root path is also accepted, costing one extra
    request if it ever appears.

    Returns:
        ``(paths, inline)`` -- versions still needing a fetch, and versions
        whose endpoint data is already present.
    """
    paths: dict[str, str] = {}
    inline: dict[str, Any] = {}
    if not isinstance(obj, dict):
        return paths, inline

    for key, value in obj.items():
        name = str(key)
        if isinstance(value, list):
            inline[name] = value
        elif isinstance(value, str) and value.startswith("/"):
            paths[name] = value
        elif isinstance(value, dict):
            nested = value.get("path") or value.get("url")
            if isinstance(nested, str) and nested.startswith("/"):
                paths[name] = nested
            else:
                inline[name] = value
    return paths, inline


def _match_capability(
    hints: Iterable[str], endpoints: Mapping[str, Endpoint] | Iterable[str]
) -> str | None:
    """Choose the best path for one capability from the reported paths.

    A hint matches only as a whole path segment, or as a trailing run of
    segments if it contains a slash. Substring matching is deliberately not
    supported: it once resolved "drive" (drive mode) onto "drivefocus", and a
    capability bound to the wrong endpoint is far harder to diagnose than a
    missing one.

    Ties break by hint order, then shorter path, then newer API version --
    without which the choice would depend on dict ordering.
    """
    if isinstance(endpoints, Mapping):
        versions = {path: ep.api_version for path, ep in endpoints.items()}
    else:
        versions = dict.fromkeys(endpoints, "")

    best: tuple[int, int, str] | None = None
    best_path: str | None = None

    for rank, hint in enumerate(hints):
        hint_lower = hint.lower().strip("/")
        for path in versions:
            lowered = path.lower().rstrip("/")
            if "/" in hint_lower:
                matched = lowered.endswith("/" + hint_lower)
            else:
                matched = hint_lower in [s for s in lowered.split("/") if s]
            if not matched:
                continue
            key = (rank, len(path), _negated(versions[path]))
            if best is None or key < best:
                best = key
                best_path = path

    return best_path


def _negated(version: str) -> str:
    """Sort key ordering version strings newest-first.

    Inverting each digit turns an ascending string sort into a descending one,
    so ``ver140`` outranks ``ver100`` inside an otherwise ascending tuple.
    """
    return "".join(str(9 - int(c)) if c.isdigit() else c for c in version)


def _version_suffix(endpoint: Endpoint) -> str:
    """The endpoint's path with its API-version segment removed."""
    if not endpoint.api_version:
        return endpoint.path
    segments = [s for s in endpoint.path.split("/") if s]
    if endpoint.api_version in segments:
        index = segments.index(endpoint.api_version)
        return "/" + "/".join(segments[index + 1 :])
    return endpoint.path


def _known_capabilities() -> list[str]:
    """Capability names, most specific first."""
    from csfs.ccapi.endpoints import CAPABILITY_HINTS

    return sorted(CAPABILITY_HINTS, key=lambda name: (-len(name), name))


def discover(client: CCAPIClient) -> Capabilities:
    """Connect to the camera and build its capability map.

    Raises:
        CameraUnreachableError: Nothing answered at the configured address.
        CCAPINotActivatedError: Something answered, but no CCAPI root was found,
            usually because the one-time activation has not been run.
    """
    caps = Capabilities()

    root_payload: Any = None
    root_used = ""
    version_paths: dict[str, str] = {}
    inline_versions: dict[str, Any] = {}
    for candidate in ROOT_PATH_CANDIDATES:
        # A connection failure means the remaining candidates would fail the
        # same way, so let it propagate.
        response = client.get(candidate, expect_status=False)
        if not response.ok:
            continue
        try:
            payload = response.json()
        except ValueError:
            continue
        version_paths, inline_versions = _extract_versions(payload)
        if version_paths or inline_versions:
            root_payload = payload
            root_used = candidate
            break

    if root_payload is None:
        raise CCAPINotActivatedError(client.base_url)

    for version_name, payload in sorted(inline_versions.items()):
        _walk_paths(payload, version_name, caps.endpoints)

    # A version given as a bare path needs fetching. One that fails to
    # enumerate is skipped; the others may still provide what we need.
    for version_name, version_path in sorted(version_paths.items()):
        try:
            payload = client.get_json(version_path)
        except CCAPIError:
            continue
        _walk_paths(payload, version_name, caps.endpoints)

    # Derive version roots from the root that answered, never from a literal.
    caps.api_versions = dict(version_paths)
    root_prefix = root_used.rstrip("/")
    for version_name in inline_versions:
        caps.api_versions.setdefault(version_name, f"{root_prefix}/{version_name}/")

    matched_suffixes: set[str] = set()
    for capability in _known_capabilities():
        path = _match_capability(hints_for(capability), caps.endpoints)
        if path is not None:
            endpoint = caps.endpoints[path]
            caps.resolved[capability] = endpoint
            matched_suffixes.add(_version_suffix(endpoint))

    # Compare by version-stripped suffix: a body republishes each capability
    # under every version it supports, and listing the older copies as unused
    # would bury the genuinely unrecognised endpoints.
    caps.unmatched = sorted(
        path
        for path, endpoint in caps.endpoints.items()
        if _version_suffix(endpoint) not in matched_suffixes
    )
    return caps
