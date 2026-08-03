"""Print every endpoint a camera reports, and which capability each one matched.

    python -m csfs.tools.dump_endpoints                    # against the mock
    python -m csfs.tools.dump_endpoints --base-url http://192.168.1.50:8080

Run this once against your own body with your copy of Canon's reference open:
it answers whether ``csfs/ccapi/endpoints.py`` names things the way your camera
does. MISSING means a capability the code wants but could not find; UNMATCHED
means an endpoint the camera offers that this project does not use.

Output is safe to paste into a bug report: endpoint paths only, no address,
serial number or personal data.
"""

from __future__ import annotations

import argparse
import sys

from csfs.ccapi import CCAPIClient, discover
from csfs.ccapi.endpoints import CAPABILITY_HINTS
from csfs.ccapi.errors import CCAPIError
from csfs.config import load_config


def main(argv: list[str] | None = None) -> int:
    """Print the camera's endpoint list. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base-url",
        default=None,
        help="Camera base URL. Defaults to camera.base_url from your config.",
    )
    parser.add_argument(
        "--timeout", type=float, default=None, help="Per-request timeout in seconds."
    )
    args = parser.parse_args(argv)

    config = load_config()
    base_url = args.base_url or config.camera.base_url
    timeout = args.timeout if args.timeout is not None else config.camera.timeout_s

    print(f"Querying {base_url} ...\n")
    try:
        with CCAPIClient(
            base_url,
            timeout_s=timeout,
            username=config.camera.username,
            password=config.camera.password,
        ) as client:
            caps = discover(client)
    except CCAPIError as exc:
        # Print plainly rather than as a traceback; the reader is mid-setup.
        print(f"Could not query the camera.\n\n{exc}", file=sys.stderr)
        return 1

    print(f"API VERSIONS ({len(caps.api_versions)})")
    for name, path in sorted(caps.api_versions.items()):
        print(f"  {name:<10} {path}")

    print(f"\nRESOLVED CAPABILITIES ({len(caps.resolved)}/{len(CAPABILITY_HINTS)})")
    for capability in sorted(caps.resolved):
        endpoint = caps.resolved[capability]
        methods = ",".join(sorted(endpoint.methods)) or "unknown"
        print(f"  {capability:<20} {endpoint.path}  [{methods}]")

    missing = sorted(set(CAPABILITY_HINTS) - set(caps.resolved))
    if missing:
        print(f"\nMISSING CAPABILITIES ({len(missing)})")
        print("  Not found on this camera. Either it genuinely lacks them, or the")
        print("  hints in csfs/ccapi/endpoints.py do not match its naming.")
        for capability in missing:
            hints = ", ".join(CAPABILITY_HINTS[capability])
            print(f"  {capability:<20} tried: {hints}")

    if caps.unmatched:
        print(f"\nUNMATCHED ENDPOINTS ({len(caps.unmatched)})")
        print("  Your camera offers these; this project does not use them yet.")
        for path in caps.unmatched:
            print(f"  {path}")

    print(f"\nTotal endpoints reported: {len(caps.endpoints)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
