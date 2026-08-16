"""Build the double-clickable app.

    python packaging/build_app.py

Produces ``dist/Canon Smart Film Scanner.app`` on macOS and
``dist/Canon Smart Film Scanner/`` with an ``.exe`` inside on Windows.

CCAPI is an HTTP API, so unlike the EDSDK sibling this app needs nothing of
Canon's at runtime and is genuinely redistributable. What must never ship is
Canon's **documentation** -- the API reference and the activation tool are
licensed to you, not to whoever downloads this. The build refuses to finish if
it finds any, because that is the one mistake here that cannot be taken back
once uploaded.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from csfs import __version__  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "packaging" / "csfs.spec"
DIST = REPO / "dist"
APP_NAME = "Canon Smart Film Scanner"

#: Canon's documentation and tooling, by exact filename.
#:
#: Matched precisely rather than by substring: the app's own executable is
#: called "Canon Smart Film Scanner", and half the source tree has "ccapi" in
#: its path. A guard that flags the binary it just built is a guard everyone
#: learns to ignore.
FORBIDDEN_FILES = {
    "ccapi_activation.exe",
    "cameracontrolapi_activation.exe",
}

#: Canon's reference material, matched anywhere in the path.
FORBIDDEN_DIRS = ("ccapi_reference",)

#: The user's own settings must never be baked into a build for strangers:
#: config.yaml holds their camera's address and any digest credentials.
FORBIDDEN_EXACT = ("config.yaml",)


#: Native libraries THIRD-PARTY-NOTICES.md accounts for.
#:
#: Checked after every build. A dependency that quietly starts bundling
#: something new shows up here as a failure rather than as an undocumented
#: library in someone else's download.
NOTICED_LIBRARIES = {
    "libxau", "libavif", "libcrypto", "libjpeg", "liblcms2", "liblzma",
    "libmpdec", "libopenjp2", "libsharpyuv", "libssl", "libtiff", "libwebp",
    "libwebpdemux", "libwebpmux", "libxcb", "libz", "libpython", "python",
}


def _library_stem(name: str) -> str:
    """'libwebpmux.3.dylib' -> 'libwebpmux'."""
    return name.split(".")[0].lower()


def audit_notices(root: Path) -> list[str]:
    """Native libraries in the build that the notices do not mention."""
    seen = set()
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix not in (".dylib", ".so", ".dll"):
            continue
        # Python extension modules, not standalone libraries.
        if ".cpython-" in path.name or ".abi3." in path.name:
            continue
        stem = _library_stem(path.name)
        if stem and stem not in NOTICED_LIBRARIES and not stem.startswith("_"):
            seen.add(path.name)
    return sorted(seen)


def check_toolchain() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit(
            "PyInstaller is not installed.\n"
            "  pip install pyinstaller\n"
            "It is a build tool, so it is deliberately not in requirements.txt."
        )


def audit_output(root: Path) -> list[Path]:
    """Find anything of Canon's, or of the builder's, that got swept in."""
    offenders = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name in FORBIDDEN_FILES or name in FORBIDDEN_EXACT:
            offenders.append(path)
            continue
        if any(part.lower() in FORBIDDEN_DIRS for part in path.parts):
            offenders.append(path)
            continue
        # Canon's reference PDFs, whatever they are called.
        if path.suffix.lower() == ".pdf":
            offenders.append(path)
    return offenders


def archive(built: Path, version: str) -> Path:
    """Zip the build under a name that carries the version and the platform.

    Named from ``__version__`` rather than typed in by hand. The two drifted
    once already -- a release tagged v1.0.0 built from a tree that said 0.1.0 --
    because nothing connected them.

    ``ditto`` on macOS, not ``shutil.make_archive``: an .app is full of
    symlinks, and a zip that resolves them into copies is both much larger and
    no longer a valid bundle.
    """
    system = {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, sys.platform)
    arch = platform.machine()
    out = DIST / f"{APP_NAME.replace(' ', '-')}-{version}-{system}-{arch}.zip"
    out.unlink(missing_ok=True)

    if sys.platform == "darwin":
        subprocess.run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(built), str(out)],
            check=True,
        )
    else:
        made = shutil.make_archive(str(out.with_suffix("")), "zip", built.parent, built.name)
        out = Path(made)
    return out


def main() -> int:
    check_toolchain()

    for stale in (DIST, REPO / "build"):
        if stale.exists():
            shutil.rmtree(stale)

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)],
        cwd=REPO,
    )
    if result.returncode != 0:
        return result.returncode

    built = DIST / (f"{APP_NAME}.app" if sys.platform == "darwin" else APP_NAME)
    if not built.exists():
        sys.exit(f"Build reported success but {built} is missing.")

    offenders = audit_output(DIST)
    if offenders:
        for path in offenders[:20]:
            print(f"  {path.relative_to(DIST)}", file=sys.stderr)
        sys.exit(
            f"\nRefusing to finish: {len(offenders)} file(s) above should not be "
            "redistributed.\nRemove them from the spec's datas and rebuild."
        )

    # Skip symlinks. PyInstaller points Contents/Resources at the files in
    # Contents/Frameworks, and following those counts the same bytes twice --
    # it reported 428 MB for a bundle that occupies 169 MB on disk.
    undocumented = audit_notices(DIST)
    if undocumented:
        for name in undocumented[:20]:
            print(f"  {name}", file=sys.stderr)
        sys.exit(
            f"\nRefusing to finish: {len(undocumented)} native librar(y/ies) above are "
            "not in\nTHIRD-PARTY-NOTICES.md. Check their licences, add them, and rebuild."
        )

    size_mb = (
        sum(
            f.stat().st_size
            for f in built.rglob("*")
            if f.is_file() and not f.is_symlink()
        )
        / 1e6
    )
    print(f"\nBuilt {built}  ({size_mb:.0f} MB)")
    print("Nothing of Canon's in the output.")
    archive_path = archive(built, __version__)
    print(f"Packaged {archive_path.name}  ({archive_path.stat().st_size / 1e6:.0f} MB)")
    if sys.platform == "darwin":
        print(
            "\nUnsigned, so Gatekeeper will block it on another Mac. To share it:\n"
            "  codesign --deep --force --sign <Developer ID> "
            f'"{built}"\n'
            "  then notarise with notarytool. Locally, right-click > Open."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
