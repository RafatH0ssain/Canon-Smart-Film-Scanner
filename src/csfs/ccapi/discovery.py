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
from typing import Any, Iterable, Mapping

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


def _extract_versions(obj: Any) -> tuple[dict[str, str], dict[str, Any]]:
    """Pull the version listing out of the API root response.

    CCAPI's root returns each supported version mapped to an *array* of endpoint
    objects -- the whole endpoint inventory arrives in this one response, so no
    follow-up request is normally needed::

        {"ver100": [{"path": "...", "get": true, ...}, ...], "ver110": [...]}

    Two other shapes are also accepted, because a version root given as a bare
    path is a cheap thing to support and costs one extra request if it ever
    appears.

    Returns:
        ``(paths, inline)`` where ``paths`` maps a version to a root path that
        still needs fetching, and ``inline`` maps a version to endpoint data
        already present in this response.
    """
    paths: dict[str, str] = {}
    inline: dict[str, Any] = {}
    if not isinstance(obj, dict):
        return paths, inline

    for key, value in obj.items():
        name = str(key)
        if isinstance(value, list):
            # The normal CCAPI shape: the endpoint inventory, inline.
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
    """Choose the best path for one capability from the camera's reported paths.

    A hint matches only as a **whole path segment**, or -- if it contains a
    ``/`` -- as a whole trailing run of segments. Loose substring matching is
    deliberately not supported: it silently resolved ``drive`` (drive mode) onto
    ``drivefocus``, which would have sent drive-mode writes to the focus
    endpoint. A capability reported as missing is diagnosable; a capability
    quietly bound to the wrong endpoint is not.

    Among matches, ranking is:

    1. **Hint order.** Hints are listed most-specific-first, so an earlier hint
       always beats a later one.
    2. **Shorter path.** The less specialised endpoint wins -- the live-view
       endpoint itself rather than its ``/flip`` child.
    3. **Newer API version.** The same capability is republished under each
       version a body supports; the newest is the one to use.

    Steps 2 and 3 exist because a real camera reports the same capability many
    times over. Without them the choice would depend on dict ordering, which is
    a genuinely horrible bug to chase.
    """
    if isinstance(endpoints, Mapping):
        versions = {path: ep.api_version for path, ep in endpoints.items()}
    else:
        versions = {path: "" for path in endpoints}

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
            # Negated version so that "newer is better" sorts as "smaller key".
            key = (rank, len(path), _negated(versions[path]))
            if best is None or key < best:
                best = key
                best_path = path

    return best_path


def _negated(version: str) -> str:
    """Sort key that orders version strings newest-first.

    Inverting each digit turns a plain ascending string sort into a descending
    one, so ``ver140`` outranks ``ver100`` inside an otherwise ascending tuple.
    """
    return "".join(str(9 - int(c)) if c.isdigit() else c for c in version)


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
    root_used = ""
    version_paths: dict[str, str] = {}
    inline_versions: dict[str, Any] = {}
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
        version_paths, inline_versions = _extract_versions(payload)
        if version_paths or inline_versions:
            root_payload = payload
            root_used = candidate
            break

    if root_payload is None:
        # We got HTTP responses but no CCAPI root. Either CCAPI was never
        # activated on this body, or something else entirely owns this address.
        # (``reached_something`` is always true here -- a total failure to connect
        # already raised above -- but the check keeps the intent explicit.)
        if reached_something:
            raise CCAPINotActivatedError(client.base_url)
        raise CameraUnreachableError(client.base_url)

    # Endpoints listed inline in the root response: the normal case, no extra
    # requests needed.
    for version_name, payload in sorted(inline_versions.items()):
        _walk_paths(payload, version_name, caps.endpoints)

    # Any version given as a bare root path has to be fetched. A version that
    # fails to enumerate is skipped rather than fatal: the others may still
    # provide everything we need.
    for version_name, version_path in sorted(version_paths.items()):
        try:
            payload = client.get_json(version_path)
        except CCAPIError:
            continue
        _walk_paths(payload, version_name, caps.endpoints)

    # Record every version, using a root path derived from the root that
    # actually answered -- never a literal -- so callers see a consistent
    # {version: path} map whichever shape the camera used.
    caps.api_versions = dict(version_paths)
    root_prefix = root_used.rstrip("/")
    for version_name in inline_versions:
        caps.api_versions.setdefault(version_name, f"{root_prefix}/{version_name}/")

    # Resolve logical capabilities against whatever the camera actually listed.
    matched_suffixes: set[str] = set()
    for capability in _known_capabilities():
        path = _match_capability(hints_for(capability), caps.endpoints)
        if path is not None:
            endpoint = caps.endpoints[path]
            caps.resolved[capability] = endpoint
            matched_suffixes.add(_version_suffix(endpoint))

    # Compare by version-stripped suffix, not by full path. A body republishes
    # the same capability under every API version it supports, and reporting the
    # older copies as "unused" would bury the genuinely unrecognised endpoints
    # this list exists to surface.
    caps.unmatched = sorted(
        path
        for path, endpoint in caps.endpoints.items()
        if _version_suffix(endpoint) not in matched_suffixes
    )
    return caps


def _version_suffix(endpoint: Endpoint) -> str:
    """The endpoint's path with its API-version segment removed.

    Uses the version name the camera itself reported rather than assuming where
    the version sits in the path.
    """
    if not endpoint.api_version:
        return endpoint.path
    segments = [s for s in endpoint.path.split("/") if s]
    if endpoint.api_version in segments:
        index = segments.index(endpoint.api_version)
        return "/" + "/".join(segments[index + 1 :])
    return endpoint.path


def _known_capabilities() -> list[str]:
    """Capability names, most specific first.

    Ordering matters: ``shutter_mode`` must resolve before ``shutter`` so the
    broader hint cannot claim the more specific endpoint first.
    """
    from csfs.ccapi.endpoints import CAPABILITY_HINTS

    return sorted(CAPABILITY_HINTS, key=lambda name: (-len(name), name))
