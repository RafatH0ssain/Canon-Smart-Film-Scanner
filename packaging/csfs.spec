# PyInstaller spec for a double-clickable Canon Smart Film Scanner.
#
# Build with `python packaging/build_app.py`, not by invoking PyInstaller
# directly -- the script checks the toolchain first and audits the output.
#
# Unlike the EDSDK sibling, nothing of Canon's is needed at runtime: CCAPI is
# an HTTP API, so this app is genuinely redistributable. What must still never
# ship is Canon's *documentation* -- the API reference and activation tool are
# licensed to you, not to whoever downloads this. Hence explicit data files
# rather than a directory glob, and the audit in build_app.py.

import sys
from pathlib import Path

REPO = Path(SPECPATH).resolve().parent          # noqa: F821 - PyInstaller global
APP_NAME = "Canon Smart Film Scanner"

datas = [
    # The browser UI. Without this the app starts and serves nothing.
    (str(REPO / "src" / "csfs" / "app" / "static"), "csfs/app/static"),
    # Seeds the user's config on first launch, comments and all.
    (str(REPO / "config.example.yaml"), "."),
]

hiddenimports = [
    "cv2",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    # The mock camera runs in-process on a fresh install, so it has to be in
    # the bundle even though nothing imports it at module scope.
    "csfs.mock.mock_server",
]

analysis = Analysis(                             # noqa: F821
    [str(REPO / "packaging" / "entry.py")],
    pathex=[str(REPO / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "PySide6", "PyQt5"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)                         # noqa: F821

exe = EXE(                                       # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,
    # Keep a console on Windows: the app prints its folder and the camera
    # address, and a startup failure with no window is indistinguishable from
    # nothing happening at all.
    console=True,
)

collect = COLLECT(                               # noqa: F821
    exe, analysis.binaries, analysis.datas, strip=False, upx=False, name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(                                # noqa: F821
        collect,
        name=f"{APP_NAME}.app",
        icon=None,
        bundle_identifier="com.rafathossain.csfs",
        info_plist={
            "NSHighResolutionCapable": True,
            # macOS 15+ gates LAN access per app, and the camera is a LAN
            # device. Without this the prompt never appears and every call
            # fails looking exactly like a network fault.
            "NSLocalNetworkUsageDescription":
                "Connects to your Canon camera over your local network.",
            "LSMinimumSystemVersion": "11.0",
        },
    )
