# Canon Smart Film Scanner

[![tests](https://github.com/RafatH0ssain/Canon-Smart-Film-Scanner/actions/workflows/tests.yml/badge.svg)](https://github.com/RafatH0ssain/Canon-Smart-Film-Scanner/actions/workflows/tests.yml)

I scan my film negatives with a Canon R7 on a copy stand. Pressing the shutter by hand shook the
whole rig, and checking focus on grain on the camera's little screen was a pain. So this controls
the camera over Wi-Fi from my laptop instead, with the live view already inverted to a positive.
It only needs a plain `pip install`, no Canon SDK.

Wi-Fi turned out to be slow on my camera (about 4 fps), so I mostly use the
[USB version](https://github.com/RafatH0ssain/Canon-EDSDK-Film-Scanner) now. After scanning, I use
[Film Scan Darkroom](https://github.com/RafatH0ssain/Film-Scan-Darkroom) to crop and invert the roll.

## What it does

- Live view inverted to a positive. Black and white looks right; colour still comes out a bit cyan
  because the orange mask isn't handled yet (the saved files aren't affected)
- Focus peaking, a loupe to zoom into the grain, and a sharpness number to focus by, including
  one per corner so you can tell if the stand isn't level
- Focus and shutter from the laptop, with a short delay before each shot so the rig settles
- Downloads each full-resolution file to `captures/` as soon as it's taken

When it connects it switches the camera to electronic shutter, single shot, one-shot AF and no
auto power-off, and tells you it did. Set `capture.prepare_camera_for_scanning: false` if you'd
rather it left the camera alone.

## What you need

A Canon camera with Canon's Camera Control API (the R7, R6 II, R10, R5 and others), a macro lens
that gets close to 1:1, a copy stand, a negative holder and a backlight. The camera and laptop
need to be on the same Wi-Fi network.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -e .
python -m csfs.mock.mock_server   # fake camera, if you don't have one connected
python -m csfs.app.server
```

Then open http://127.0.0.1:8000 and press Connect.

For a real camera you need to turn on the Camera Control API once. Request access on Canon's
[developer site](https://developercommunity.usa.canon.com/), run their activation tool with the
camera plugged in over USB, then add a Camera Control API connection in the camera's Wi-Fi menu.
Put the camera's address in `config.yaml` and check it with `python -m csfs.tools.dump_endpoints`.
Only one device can be connected at a time, so close Canon's phone app. On a Mac, allow Local
Network access when it asks, or nothing will connect.

Keys: Space to capture, arrow keys to focus, I to invert, L for the loupe, P for peaking,
M for sharpness.

`python packaging/build_app.py` builds a double-clickable app (needs `pip install pyinstaller`).

## Licence

MIT.
