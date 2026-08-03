"""Exception types for the CCAPI client.

The point of this hierarchy is that when something goes wrong at the copy stand
-- often mid-roll, with your hands covered in negatives -- the message tells you
which of the handful of real-world causes it was, and what to do about it.
"""

from __future__ import annotations


class CCAPIError(Exception):
    """Base class for every error raised by the CCAPI client."""


class CameraUnreachableError(CCAPIError):
    """No HTTP response at all: wrong address, cable pulled, camera asleep."""

    def __init__(self, base_url: str, cause: BaseException | None = None) -> None:
        super().__init__(
            f"Could not reach a camera at {base_url}.\n"
            "Check, in this order:\n"
            "  1. Is the camera powered on and awake? Bodies sleep aggressively; "
            "disable auto power-off while scanning.\n"
            "  2. Is CCAPI enabled in the camera's menu right now? It is not "
            "sticky across power cycles on all bodies.\n"
            "  3. Does the address match what the camera's CCAPI screen shows? "
            "Set it in config.yaml under camera.base_url.\n"
            "  4. If on Wi-Fi, are the PC and camera on the same network? "
            "For a fixed copy stand, wired USB is more reliable."
        )
        self.base_url = base_url
        self.__cause__ = cause


class CCAPINotActivatedError(CCAPIError):
    """The camera answered, but CCAPI is not activated on this body.

    CCAPI must be unlocked once per camera using Canon's activation tool, which
    you obtain from Canon's Developer Community. This is a one-time step and is
    separate from enabling CCAPI in the camera's menu.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__(
            f"Something answered at {base_url}, but it does not look like an "
            "activated CCAPI service.\n"
            "CCAPI needs a one-time activation with Canon's activation tool "
            "before the camera will serve the API. Register with Canon's "
            "Developer Community, download the activation tool, and run it "
            "against the camera. See README.md, 'Canon-side setup'."
        )
        self.base_url = base_url


class CCAPIAuthError(CCAPIError):
    """The camera rejected the request because it wants credentials.

    CCAPI uses HTTP digest authentication, and only when an account has been
    registered on the camera under the CCAPI account settings. Most setups have
    no account and need no credentials at all.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__(
            f"The camera at {base_url} requires authentication.\n"
            "An account is registered on the camera under its Camera Control API "
            "account settings, so CCAPI is using HTTP digest authentication.\n"
            "Either:\n"
            "  - add the matching credentials to config.yaml as camera.username "
            "and camera.password, or\n"
            "  - remove the account on the camera if you do not need it."
        )
        self.base_url = base_url


class CCAPIBusyError(CCAPIError):
    """The camera is temporarily unable to serve this request.

    Usually means the camera is showing a menu, is mid-operation, or another
    device already holds the single CCAPI connection the camera allows.
    """

    def __init__(self, url: str, message: str = "") -> None:
        detail = f"\nCamera said: {message}" if message else ""
        super().__init__(
            f"The camera is busy and refused {url}.{detail}\n"
            "Common causes: the camera is showing a menu or playback screen, or "
            "another app still holds the connection -- CCAPI allows only one "
            "connected device at a time, so quit Canon Camera Connect on your "
            "phone and any other tethering software."
        )
        self.url = url


class CCAPIResponseError(CCAPIError):
    """The camera returned an HTTP error status for a request."""

    def __init__(self, method: str, url: str, status_code: int, body: str = "") -> None:
        detail = f"\nCamera said: {body.strip()[:500]}" if body.strip() else ""
        super().__init__(f"{method} {url} failed with HTTP {status_code}.{detail}")
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body = body


class UnsupportedEndpointError(CCAPIError):
    """This camera/API version does not offer the capability we asked for.

    Raised rather than guessing a path. Different bodies expose different
    subsets of CCAPI, which is why the client resolves capabilities from what
    the camera reports instead of assuming a fixed map (see ``discovery.py``).
    """

    def __init__(self, capability: str, available: list[str] | None = None) -> None:
        msg = f"This camera does not expose the '{capability}' capability over CCAPI."
        if available:
            msg += (
                "\nIt did report these endpoints: "
                + ", ".join(sorted(available)[:40])
                + ("..." if len(available) > 40 else "")
            )
        msg += (
            "\nIf your camera *should* support this, the capability's path hints in "
            "csfs/ccapi/endpoints.py may need updating against your local copy of "
            "Canon's CCAPI reference."
        )
        super().__init__(msg)
        self.capability = capability
        self.available = available or []
