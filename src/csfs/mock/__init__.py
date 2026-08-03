"""A fake CCAPI camera, so this project can be developed with no hardware.

    python -m csfs.mock.mock_server

Serves synthetic negatives rather than ordinary test images, so the inversion
pipeline is exercised against a proper film rebate and orange mask.
"""
