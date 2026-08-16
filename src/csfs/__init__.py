"""Canon Smart Film Scan -- remote-control film scanning for Canon cameras.

The layering is what keeps this testable without a camera attached:

    csfs.ccapi       Thin CCAPI client. HTTP and Canon's API only.
    csfs.processing  Pure functions over numpy arrays. No network, no UI.
    csfs.app         The local web server that wires the two together.
    csfs.mock        A fake CCAPI camera, so development needs no hardware.
"""

__version__ = "1.1.0"
