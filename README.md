# Canon Smart Film Scanner

[![tests](https://github.com/RafatH0ssain/Canon-Smart-Film-Scanner/actions/workflows/tests.yml/badge.svg)](https://github.com/RafatH0ssain/Canon-Smart-Film-Scanner/actions/workflows/tests.yml)

Scan film negatives with a Canon camera on a copy stand: live positive preview, remote focus and shutter, over Wi-Fi. Built on [CCAPI](https://developercommunity.usa.canon.com/), Canon's Camera Control API. No native dependencies, plain `pip install`.

> **Status: works on a real camera.** Live view and capture were fixed against real hardware. Colour inversion is still the v0.1 linear flip, so **colour negatives preview cyan**: the orange mask isn't neutralised, with no scheduled fix. Black and white is fine. Captured files are never affected; inversion is preview-only.

## Why

| Problem | Fix |
|---|---|
| Shutter press shakes the rig | Remote shutter, electronic shutter, settle delay |
| Focus is hard to judge on a small rear screen | Big live view, loupe, peaking, numeric sharpness |
| Negatives are unintuitive to frame and review | Real-time inversion, so you work in positive |

CCAPI never modifies firmware, so nothing is baked into the camera's files.

**Expect roughly 4 fps at 960×640, or 11 fps at 640×424.** CCAPI has no USB transport and the link is the limit. If that's too slow, the sibling **Canon EDSDK Film Scanner** does the same job over USB at ~60 fps.

## The app

A packaged build runs by double-clicking: no Python, no terminal. On first launch it creates **Documents/Canon Smart Film Scanner/**, writes a commented `config.yaml`, opens the UI, and starts the bundled mock camera.

Nothing of Canon's is inside it: CCAPI is an HTTP API, so unlike the EDSDK sibling this app is redistributable. Pointing it at a real camera means entering the address in `config.yaml` and enabling CCAPI on the body first. Once `camera.base_url` stops pointing at the mock, the mock stops starting.

Build it yourself:

```bash
pip install pyinstaller
python packaging/build_app.py
```

Output lands in `dist/`, around **51 MB**. The build refuses to finish if Canon's reference material or your `config.yaml` reach the output. Everything in it is permissively licensed (**no GPL or LGPL components**), so the binary is yours to hand out; see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

macOS builds are unsigned, so Gatekeeper blocks them elsewhere. Locally, right-click → Open.

## Try it from source, with no camera

A mock camera serves synthetic negatives (orange mask, rebate, grain) over the same API a real body uses. Use Python **3.10–3.13**.

```bash
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt -e .
```

Then, in two terminals:

```bash
python -m csfs.mock.mock_server     # one
python -m csfs.app.server           # the other
```

<http://127.0.0.1:8000>, press **Connect**. **Capture** downloads a full-resolution file to `captures/`. `pytest` runs 256 tests with their own mock.

### Controls

| | Key | |
|---|---|---|
| Invert | <kbd>I</kbd> | Positive instead of negative |
| Loupe | <kbd>L</kbd> | Magnify to judge grain; click to move it |
| Peaking | <kbd>P</kbd> | Mark sharpest edges in red |
| Focus | <kbd>←</kbd> <kbd>→</kbd> | <kbd>Shift</kbd> medium step, <kbd>Alt</kbd> coarse |
| Sharpness | <kbd>M</kbd> | Numeric reading, plus per-corner |
| Capture | <kbd>Space</kbd> | Settle, fire, download |

Peaking works in **autofocus** mode, unlike the camera's own, because it is computed on the computer; CCAPI cannot drive focus in manual mode, so on the camera alone you must choose.

The sharpness readout is **relative**: turn focus and watch it peak. The per-corner figures do stand alone: one corner consistently lower means your copy stand isn't parallel. Camera-side zoom (1×/5×/10×) beats the loupe, but not every body has it.

### What it changes on your camera

On connect, and it tells you: **electronic shutter** (falls back to first-curtain), **drive mode → single**, **AF → one-shot**, **auto power-off → disabled** (a sleeping camera looks exactly like a network fault). Set `capture.prepare_camera_for_scanning: false` to skip it.

## Canon-side setup

Done once, on Canon's website and the camera itself. Can't be automated.

⚠️ **Canon's licence forbids redistributing the activation tool or the API reference.** Keep both in `ccapi_reference/` (git-ignored) or outside the repo. Never commit them or paste the reference into an issue.

> CCAPI connects over **Wi-Fi or LAN, not USB**. The cable is only for the one-time activation in step 3. Most bodies (R7 included) have no Ethernet, so Wi-Fi through your access point is the realistic setup.

1. Update firmware, then [register with Canon's Developer Community](https://developercommunity.usa.canon.com/) and request CCAPI access. Approval isn't instant.
2. Download the activation tool and API reference.
3. Run the tool with the camera on USB, powered, and your computer online; it contacts Canon and fails silently offline. Only one camera connected. It worked when **`Camera Control API` appears in the camera's Wi-Fi menu**.
4. `Wi-Fi settings` → `Camera Control API` → `Add connection` → `Add with wizard`, pick your access point, let it take an IP.
5. Read the CCAPI top URL off the `[Wi-Fi on]` screen; put its host and port in `camera.base_url`.
6. Enable `Auto connect` and disable auto power-off while scanning.

**Only one device at a time**: quit Canon Camera Connect on your phone and other tethering software.

```bash
cp config.example.yaml config.yaml     # Windows: copy config.example.yaml config.yaml
python -m csfs.tools.dump_endpoints    # verify
```

`config.yaml` holds your camera's IP and is git-ignored. If you registered an account under `Account settings`, CCAPI needs HTTP digest auth: set `camera.username` and `camera.password`.

### macOS

**Grant Local Network permission or nothing connects.** macOS 15+ gates LAN access per app and the camera is a LAN device. If you miss the prompt, every call fails looking like a network fault; fix it in **System Settings → Privacy & Security → Local Network**. Activation may need a Windows machine, because Canon doesn't always ship a macOS build; the unlock is written *to the camera*, so either machine works.

### If your camera names endpoints differently

Nothing here hardcodes CCAPI paths. The app reads the endpoint inventory the camera publishes and matches it against hints in [`src/csfs/ccapi/endpoints.py`](src/csfs/ccapi/endpoints.py), the only file with path assumptions.

If `dump_endpoints` reports a capability MISSING that your reference says you have, add the distinguishing path segment there. An honest MISSING is preferred: a capability bound to the wrong endpoint fails in hard-to-diagnose ways.

## Hardware

A CCAPI-capable Canon body (R7, R6 II, R10, R5, R6, RP, R3, 90D, 850D and others; check Canon's list). The R7 is the reference, but the code targets the API, not the model. Also a macro lens reaching ~1:1 (focus controls hide on a manual lens), plus copy stand, negative holder, and backlight (high-CRI for colour).

## Layout

`src/csfs/` holds config, the thin `ccapi/` client, pure `processing/`, the `app/` UI, a `mock/` camera, and `tools/`. Alongside: `packaging/` (PyInstaller) and `tests/` (256, no camera needed). The layering is what lets colour inversion be worked on with no camera attached.

## Contributing

Keep the three layers decoupled and add tests for anything in `processing/`. **Never commit** Canon's CCAPI reference or activation files, camera IP addresses, `config.yaml`, or your own scans.

## Licence

[MIT](LICENSE), covering **this project's code only**. Canon's CCAPI, activation tool and reference documentation are Canon's property under separate terms.
