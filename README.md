# Canon Smart Film Scanner

Scan film negatives with a Canon camera on a copy stand — live positive preview, remote focus and shutter, over Wi-Fi.

Built on [CCAPI](https://developercommunity.usa.canon.com/), Canon's Camera Control API. No native dependencies, plain `pip install`.

> **Status: works on a real camera.** Live-view streaming and capture were both fixed against real hardware. Colour inversion is still the v0.1 linear flip, so **colour negatives preview cyan** — the orange mask isn't neutralised yet. Black and white is fine. Captured files are never affected; inversion is preview-only.

## Why

| Problem | Fix |
|---|---|
| Pressing the shutter shakes the rig and softens the scan | Remote shutter, electronic shutter, configurable settle delay |
| Judging focus on grain is hard on a small rear screen | Big live view, magnified loupe, focus peaking, numeric sharpness |
| Framing and reviewing negatives is unintuitive | Real-time inversion — you work in positive |

CCAPI controls the camera from outside and never modifies firmware, so it can't change what the camera's own screen shows or bake looks into the files it writes. All processing happens on the computer, which is what film scanning wants.

**Expect roughly 4 fps at 960×640, or 11 fps at 640×424.** CCAPI has no USB transport and the link is the limit. If that's too slow to focus by, the sibling project **Canon EDSDK Film Scanner** does the same job over USB at ~60 fps — at the cost of a native SDK you must obtain from Canon, and a cable.

## The app

A packaged build runs by double-clicking — no Python, no terminal, no second
process. On first launch it creates **Documents/Canon Smart Film Scanner/**,
puts a commented `config.yaml` there, opens the UI in your browser, and starts
the bundled mock camera so there is something to look at straight away.

Nothing of Canon's is inside it — CCAPI is an HTTP API, so unlike the EDSDK
sibling this app is genuinely redistributable. Pointing it at a real camera is
still your side of the job: enter the address in that `config.yaml`, and enable
CCAPI on the body first (see [Canon-side setup](#canon-side-setup)). The moment
`camera.base_url` stops pointing at the mock, the mock stops starting.

Build it yourself with:

```bash
pip install pyinstaller
python packaging/build_app.py
```

Output lands in `dist/`, around **51 MB**. The build refuses to finish if
Canon's reference material or your own `config.yaml` reach the output, or if it
bundles a native library missing from
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

Everything in it is permissively licensed — **no GPL or LGPL components** — so
the binary is genuinely yours to hand out.

macOS builds are unsigned, so Gatekeeper blocks them on any other Mac. Sharing
one properly needs an Apple Developer ID and notarisation; locally, right-click
→ Open.

## Try it from source, with no camera

A mock camera serves synthetic negatives — orange mask, rebate, grain — over the same API a real body uses.

```bash
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt -e .
```

Use Python **3.10–3.13**; `opencv-python` wheels lag newer releases. macOS ships 3.9, which is too old — `brew install python@3.12` and build the venv from `python3.12` explicitly.

Two terminals:

```bash
python -m csfs.mock.mock_server     # one
python -m csfs.app.server           # the other
```

<http://127.0.0.1:8000>, press **Connect**. **Capture** downloads a full-resolution file to `captures/`.

`pytest` runs 256 tests, which start their own mock and need nothing else running.

### Controls

| | Key | |
|---|---|---|
| Invert | <kbd>I</kbd> | Positive instead of negative |
| Loupe | <kbd>L</kbd> | Magnify to judge grain. Click the image to move it |
| Peaking | <kbd>P</kbd> | Mark sharpest edges in red |
| Focus | <kbd>←</kbd> <kbd>→</kbd> | <kbd>Shift</kbd> medium step, <kbd>Alt</kbd> coarse |
| Sharpness | <kbd>M</kbd> | Numeric reading for the loupe region, plus per-corner |
| Capture | <kbd>Space</kbd> | Settle, fire, download |

Peaking works in **autofocus** mode, unlike the camera's own, which only appears in manual focus — and CCAPI cannot drive focus when focus is manual. On the camera alone you must choose; here you get both, because peaking is computed on the computer.

The sharpness readout is **relative** — turn focus and watch it peak. Comparing values between negatives means nothing. The per-corner figures do stand alone: one corner consistently lower means your copy stand isn't parallel to the film.

Camera-side zoom (1×/5×/10×) returns real sensor detail where the software loupe can only enlarge pixels the camera already discarded. Not every body has it — the R7 doesn't — and the control is hidden when unsupported.

### What it changes on your camera

On connect, and it tells you: **electronic shutter** (falls back to first-curtain), **drive mode → single** (continuous turns one release into a burst), **AF → one-shot** (servo would refocus between frames), **auto power-off → disabled** (a sleeping camera looks exactly like a network fault).

Set `capture.prepare_camera_for_scanning: false` to leave everything alone.

## Canon-side setup

Done once, on Canon's website and the camera itself. Can't be automated.

⚠️ **Canon's licence forbids redistributing the activation tool or the API reference.** Keep both outside this repo, or in `ccapi_reference/`, which is git-ignored. Never commit them or paste the reference into an issue.

> CCAPI connects over **Wi-Fi or wired LAN, not USB**. The cable is only for the one-time activation in step 3. Most enthusiast bodies (R7 included) have no Ethernet, so Wi-Fi through your access point is the realistic setup.

1. Update the camera's firmware, then [register with Canon's Developer Community](https://developercommunity.usa.canon.com/) and request CCAPI access. Approval isn't instant.
2. Download the activation tool and API reference.
3. Run the activation tool with the camera connected by USB, powered on, and your computer online — it contacts Canon and fails silently offline. Only one camera connected. It worked when **`Camera Control API` appears in the camera's Wi-Fi menu**. *Skip if your camera's guide already documents CCAPI.*
4. `Wi-Fi settings` → `Camera Control API` → `Add connection` → `Add with wizard`, pick your access point, let it take an IP.
5. Read the CCAPI top URL off the `[Wi-Fi on]` screen and put its host and port in `camera.base_url`.
6. Enable `Auto connect` so the camera rejoins on power-on, and disable auto power-off while scanning.

**Only one device may be connected at a time** — quit Canon Camera Connect on your phone and any other tethering software.

```bash
cp config.example.yaml config.yaml     # Windows: copy config.example.yaml config.yaml
python -m csfs.tools.dump_endpoints    # verify
```

`config.yaml` holds your camera's IP and is git-ignored. Never commit it.

If you registered an account under `Camera Control API` → `Account settings`, CCAPI requires HTTP digest auth — set `camera.username` and `camera.password` to match. Leave empty if you didn't.

### macOS

**Grant Local Network permission or nothing connects.** macOS 15+ gates LAN access per app, and the camera is a LAN device. Say yes to the prompt on first run; if you miss it, every call fails looking exactly like a network fault. Fix in **System Settings → Privacy & Security → Local Network**, ticking whatever you launch the server from. Toggle it off and on if already ticked but still failing.

**Activation may need a Windows machine** — Canon doesn't always ship a macOS build of the tool. It doesn't matter which computer does it: the unlock is written *to the camera*, which is why success shows up in the camera's own menu. Borrow one once.

### If your camera names endpoints differently

Nothing here hardcodes CCAPI paths. The app reads the endpoint inventory the camera publishes and matches it against hints in [`src/csfs/ccapi/endpoints.py`](src/csfs/ccapi/endpoints.py) — the only file with path assumptions, as Canon's own reference recommends, since URLs vary by model and firmware.

If `dump_endpoints` reports a capability MISSING that your reference says you have, add the distinguishing path segment there. Hints match whole path segments, never loose substrings: a capability bound to the wrong endpoint fails in ways that are very hard to diagnose, so an honest MISSING is preferred.

Share the path *your camera reports* if you file an issue; not Canon's documentation of it.

## Hardware

- A CCAPI-capable Canon body — R7, R6 II, R10, R5, R6, RP, R3, 90D, 850D and others; check Canon's current list. The R7 is the reference, but the code targets the API, not the model.
- A macro lens reaching ~1:1 for 35mm. Autofocus lenses can be driven from the PC; with a manual lens you focus by hand and keep everything else, and the focus controls hide themselves rather than offering dead buttons.
- Copy stand, negative holder, backlight — high-CRI for colour.

## Layout

```
src/csfs/
├── config.py        Defaults -> config.yaml -> env vars
├── ccapi/           Thin CCAPI client. No image processing, no UI.
│   ├── client.py        HTTP transport + error translation
│   ├── discovery.py     Ask the camera what it supports
│   ├── endpoints.py     The ONLY place path assumptions live
│   ├── liveview.py      Stream transports + frame parsers
│   ├── shutter.py       Electronic shutter + settle delay
│   ├── settings.py      Read/write settings by ability
│   └── contents.py      List and download captures
├── processing/      Pure numpy and Pillow. No network, no UI.
├── app/             Local web server + browser UI, no build step
├── mock/            A fake camera, so development needs no hardware
├── paths.py         Where things live, from a checkout and from a bundle
└── tools/           dump_endpoints, check_liveview
packaging/           PyInstaller build for the double-clickable app
tests/               256 tests, none needing a camera
```

The layering is load-bearing: it's what lets colour inversion be worked on with no camera attached.

## Contributing

Keep the three layers decoupled and add tests for anything in `processing/`.

**Never commit:** Canon's CCAPI reference or activation files, camera IP addresses, `config.yaml`, or your own scans. `.gitignore` covers these — check `git status` anyway.

Got a camera model working? A short test report is genuinely useful.

## Licence

[MIT](LICENSE), covering **this project's code only**. Canon's CCAPI, activation tool and reference documentation are Canon's property under separate terms.
