"""Thin client for Canon's Camera Control API (CCAPI).

This package knows about HTTP and about Canon's API shape. It deliberately
knows nothing about image processing or the user interface, so it can be tested
against the mock camera in ``csfs.mock`` with no hardware attached.

Nothing here modifies camera firmware. CCAPI is an external, documented,
read/write control API; every call this package makes is one Canon publishes.
"""

from csfs.ccapi.client import CCAPIClient
from csfs.ccapi.discovery import Capabilities, discover
from csfs.ccapi.errors import (
    CCAPIError,
    CameraUnreachableError,
    CCAPINotActivatedError,
    CCAPIResponseError,
    UnsupportedEndpointError,
)

__all__ = [
    "CCAPIClient",
    "Capabilities",
    "discover",
    "CCAPIError",
    "CameraUnreachableError",
    "CCAPINotActivatedError",
    "CCAPIResponseError",
    "UnsupportedEndpointError",
]
