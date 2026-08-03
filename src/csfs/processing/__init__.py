"""Image processing: pure functions over numpy arrays.

Nothing in this package touches the network or the UI, and nothing here holds
state. That is what lets the hard parts -- colour inversion above all -- be
developed and tested against synthetic fixtures with no camera attached.

Frames are OpenCV-convention ``uint8`` arrays of shape ``(H, W, 3)`` in **BGR**
order unless a function says otherwise.
"""

from csfs.processing.codec import decode_jpeg, encode_jpeg
from csfs.processing.invert import invert_linear
from csfs.processing.loupe import crop_zoom

__all__ = ["crop_zoom", "decode_jpeg", "encode_jpeg", "invert_linear"]
