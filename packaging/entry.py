"""Entry point for the packaged app.

Thin on purpose: a bundle needs a single script to start from, and everything
it should do already lives in ``csfs.app.server.main`` -- including seeding the
user's folder and starting the bundled mock camera.
"""

from __future__ import annotations

import multiprocessing
import sys


def run() -> int:
    # Without this a frozen app on Windows re-launches itself for every
    # subprocess, which presents as the app opening over and over.
    multiprocessing.freeze_support()

    from csfs.app.server import main

    main()
    return 0


if __name__ == "__main__":
    sys.exit(run())
