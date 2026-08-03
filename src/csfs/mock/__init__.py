"""A fake CCAPI camera, so this project can be developed with no hardware.

Run it with::

    python -m csfs.mock.mock_server

It serves synthetic *negatives* rather than ordinary test images, because the
thing most worth testing here is the inversion pipeline, and a synthetic
negative with a proper film rebate and orange mask exercises it honestly.
"""
