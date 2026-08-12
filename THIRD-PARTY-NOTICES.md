# Third-party notices

The packaged app bundles the native libraries below. Running from a source
checkout distributes none of them — pip fetches them from PyPI — so this
matters only if you hand someone a built `.app` or `.exe`.

This list is checked by `packaging/build_app.py`, which refuses to finish if
the build contains a native library not named here. That keeps it honest when
a dependency quietly adds one.

> **Not legal advice.** Verify the terms yourself before distributing binaries.

## Everything here is permissive

**No copyleft components.** There is no GPL or LGPL code in this build, so
distributing the binary carries notice obligations and nothing heavier.

| Library | Licence | Comes from |
|---|---|---|
| libjpeg (libjpeg-turbo) | IJG / BSD-3-Clause / zlib | Pillow |
| libtiff | libtiff licence (BSD-style) | Pillow |
| libavif, libsharpyuv | BSD-2-Clause | Pillow |
| libwebp, libwebpdemux, libwebpmux | BSD-3-Clause | Pillow |
| liblcms2 | MIT | Pillow |
| libopenjp2 | BSD-2-Clause | Pillow |
| libz (zlib-ng) | zlib | Python, Pillow |
| liblzma | 0BSD / public domain | Python |
| libmpdec | BSD-2-Clause | Python |
| libcrypto, libssl (OpenSSL 3) | Apache-2.0 | Python |
| libXau, libxcb | MIT | X11 client libraries |
| CPython runtime | PSF License | the interpreter itself |

Pure-Python dependencies — numpy (BSD-3), Pillow (MIT-CMU), requests, FastAPI,
Starlette, uvicorn, pydantic, PyYAML — are permissively licensed and carry
their own notices in the bundle.

## What is deliberately absent

**OpenCV.** Its wheel bundled a GPL-licensed FFmpeg — `libx264`, `libx265`,
`libaribb24` — plus `libaom`, `SVT-AV1`, `SDL2`, `libass` and X11: roughly
49 MB of video codecs for a project that decodes JPEG, resizes, and runs a
Scharr gradient. Replacing it with Pillow and numpy took the app from 146 MB to
51 MB and removed every copyleft component in the build.

**Canon's CCAPI reference and activation tool.** Licensed to you, not to
whoever downloads this. The build fails if either is found in the output, and
so is your own `config.yaml`, which holds your camera's address.
