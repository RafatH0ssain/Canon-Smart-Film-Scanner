"""Thin client for Canon's Camera Control API (CCAPI).

Knows about HTTP and Canon's API shape, and nothing about image processing or
the UI, so it can be tested against ``csfs.mock`` with no hardware.

Nothing here modifies camera firmware; every call is one Canon documents.
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
    "CCAPIError",
    "CCAPINotActivatedError",
    "CCAPIResponseError",
    "CameraUnreachableError",
    "Capabilities",
    "UnsupportedEndpointError",
    "discover",
]
