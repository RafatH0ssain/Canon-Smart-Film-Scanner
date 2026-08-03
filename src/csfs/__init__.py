"""Canon Smart Film Scan -- remote-control film scanning for Canon cameras.

Layering (see CLAUDE.md section 7). Respect it; it is what keeps this project
testable without a camera attached:

    csfs.ccapi       Thin CCAPI client. Knows HTTP and Canon's API.
                     Knows nothing about images or the UI.
    csfs.processing  Pure functions over numpy arrays. No network, no UI.
    csfs.app         The local web server that wires the two together.
    csfs.mock        A fake CCAPI camera, so development needs no hardware.
"""

__version__ = "0.1.0.dev0"
