# Canon Smart Film Scanner

[![tests](https://github.com/RafatH0ssain/Canon-Smart-Film-Scanner/actions/workflows/tests.yml/badge.svg)](https://github.com/RafatH0ssain/Canon-Smart-Film-Scanner/actions/workflows/tests.yml)

I scan my film negatives with a Canon camera on a copy stand, and pressing the shutter by hand
shook the rig and judging focus on grain on the back screen was miserable. This drives the camera
over Wi-Fi through Canon's Camera Control API: a live view that's already inverted to a positive,
focus peaking, a loupe and a sharpness reading, and remote focus and shutter. It has no native
dependencies, just a plain `pip install`.

Wi-Fi is also its limit. On my R7 it gets about 4 fps at 960×640, which is why the USB sibling
[Canon EDSDK Film Scanner](https://github.com/RafatH0ssain/Canon-EDSDK-Film-Scanner) exists at
about 60 fps. Colour negatives preview a bit cyan here because the orange mask isn't neutralized
yet; black and white is fine, and the captured files are never touched either way. Once a roll is
scanned, [Film Scan Darkroom](https://github.com/RafatH0ssain/Film-Scan-Darkroom) crops, inverts
and exports it.

## Running it

With Python 3.10 to 3.13:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -e .
python -m csfs.mock.mock_server     # a fake camera, in one terminal
python -m csfs.app.server           # the app, in another
```

Open <http://127.0.0.1:8000> and press Connect. The mock serves synthetic negatives over the same
API a real body uses, so everything works without a camera.

For a real camera you have to enable CCAPI once. Register with
[Canon's Developer Community](https://developercommunity.usa.canon.com/) and request access, run
Canon's activation tool with the camera on USB and the computer online, then add a Camera Control
API connection in the camera's Wi-Fi menu. Canon doesn't allow redistributing the tool or its
reference, so keep both out of the repo. Copy `config.example.yaml` to `config.yaml`, put the
camera's address in `camera.base_url`, and check it with `python -m csfs.tools.dump_endpoints`.
Only one device can be connected at a time, so quit Canon Camera Connect on your phone. On macOS
15 and later, allow Local Network access when asked, or every call fails like a network fault.

On connect it switches the camera to electronic shutter, single drive, one-shot AF and no auto
power-off, and tells you it did. Set `capture.prepare_camera_for_scanning: false` to leave the
camera alone.

Keys: <kbd>Space</kbd> capture, <kbd>←</kbd><kbd>→</kbd> focus, <kbd>I</kbd> invert,
<kbd>L</kbd> loupe, <kbd>P</kbd> peaking, <kbd>M</kbd> sharpness.

`python packaging/build_app.py` (after `pip install pyinstaller`) builds a double-clickable app.
Nothing of Canon's is inside it and every component is permissively licensed, so it's yours to
hand out.

## How it works

Nothing hardcodes CCAPI paths. The app reads the endpoint list the camera publishes and matches it
against hints in `src/csfs/ccapi/endpoints.py`, since URLs change between models and firmware. If
your body names something differently, that's the one file to edit.

| Folder | What's in it |
|---|---|
| `src/csfs/ccapi/` | the CCAPI client: discovery, live view, shutter, settings, downloads |
| `src/csfs/processing/` | inversion, peaking and sharpness |
| `src/csfs/app/` | the local web server and browser UI |
| `src/csfs/mock/` | a fake camera so none of this needs hardware |

`pytest` runs the tests; they start their own mock camera.

## Licence

[MIT](LICENSE), for this project's code. Canon's CCAPI, activation tool and documentation are
Canon's, under their own terms.
