# Canon Smart Film Scanner

Digitize film negatives with a Canon camera on a copy stand — focusing, framing, and firing the shutter entirely from your computer, with a **live positive preview** so you never have to squint at an inverted image again.

Built on [CCAPI](https://developercommunity.usa.canon.com/), Canon's official Camera Control API. Works with black-and-white **and** colour negatives.

> **Status: v0.1 works against the mock camera.**
> Live view, magnified focus loupe, electronic shutter with settle delay, invert toggle, and auto-download are all working, calibrated against Canon's CCAPI Reference v1.4.0. **No real camera has confirmed any of it yet.** See [ROADMAP.md](ROADMAP.md).

---

## Why

Camera scanning gives excellent results but has three chronic annoyances, and this tool targets all three:

| Problem | Fix |
|---|---|
| Pressing the shutter shakes the rig and softens the scan | Remote shutter + **electronic shutter** + a configurable **settle delay** |
| Nailing focus on film *grain* is hard on a small rear screen | Big live view on your monitor, with a magnified loupe, focus peaking, and a numeric sharpness readout |
| Framing and reviewing negatives is unintuitive | **Real-time inversion** — you work in positive |

There is a sibling project, **Canon EDSDK Film Scanner**, that does the same job over EOS Digital SDK. It works, and if wireless live view feels laggy on your rig, you should use that one instead.

## What it deliberately does *not* do

This tool controls the camera from outside, over a documented API. **It never modifies camera firmware.** So it cannot change what the camera's own screen shows, cannot bake looks into the files the camera writes to its card, and cannot unlock features the firmware doesn't already expose.

All processing happens on the computer. For film scanning that is exactly what you want, but it's worth stating plainly so expectations are right.

---

## Try it right now, with no camera

You do not need a camera, or CCAPI access, to run this project. A **mock camera** serves synthetic film negatives — complete with an orange mask and a film rebate — over the same API a real body uses.

```bash
git clone <your-fork-url>
cd "Canon Smart Film Scanner"

python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate

pip install -r requirements.txt
pip install -e .
```

Start the mock camera in one terminal:

```bash
python -m csfs.mock.mock_server
```

Start the app in a second:

```bash
python -m csfs.app.server
```

Open <http://127.0.0.1:8000>, press **Connect**, and you'll get a live view of a synthetic film negative — orange mask, film rebate, grain and all. Press **Capture** and a full-resolution file downloads to `captures/`.

To see what the camera reports it can do:

```bash
python -m csfs.tools.dump_endpoints
```

Run the tests (they start their own mock; nothing else needs to be running):

```bash
pytest
```

### Using the app

| Control | Keyboard | What it does |
|---|---|---|
| Invert | <kbd>I</kbd> | Show a positive instead of a negative |
| Loupe | <kbd>L</kbd> | Magnify a region to judge grain focus. Click the image to move it |
| Focus peaking | <kbd>P</kbd> | Mark the sharpest edges in red |
| Focus near/far | <kbd>←</kbd> <kbd>→</kbd> | Drive focus. <kbd>Shift</kbd> = medium step, <kbd>Alt</kbd> = coarse |
| Measure sharpness | <kbd>M</kbd> | Numeric focus reading for the loupe region, plus per-corner values |
| Capture | <kbd>Space</kbd> | Settle, fire, download |
| Camera zoom | — | 1× / 5× / 10× **on the camera**, where the body supports it |

**Focus peaking works in autofocus mode** — unlike the camera's own peaking, which only appears in manual focus. That matters because **CCAPI cannot drive focus when focus is manual**, so on the camera alone you must choose between peaking and remote focus control. Here you get both, because peaking is computed on the computer from the live-view stream.

**The sharpness readout is relative, not absolute.** Turn focus and watch it peak; the bar is drawn against the best reading of the session. Comparing values between different negatives means nothing. The per-corner figures are useful on their own: if one corner sits consistently lower, your copy stand isn't parallel to the film.

**Camera zoom beats software zoom where it exists.** Camera-side magnification returns real sensor detail; the software loupe can only enlarge pixels the camera already discarded when it downscaled the stream. Note that **not every body offers it** — the EOS R7 does not, so the loupe is the only magnification there. The control is hidden when unsupported.

### What the app changes on your camera

On connect it sets, and tells you it has:

- **Electronic shutter** — no mechanical shock (falls back to electronic first curtain, then leaves it alone).
- **Drive mode → single** — continuous drive turns one remote release into a burst.
- **AF operation → one-shot** — servo AF keeps refocusing, so frames of a roll wouldn't share a focus distance.
- **Auto power-off → disabled** — a sleeping camera looks exactly like a network fault.

Set `capture.prepare_camera_for_scanning: false` in `config.yaml` if you'd rather it changed nothing.

> **Colour negatives look cyan in v0.1.** The invert toggle is a plain linear flip, which is genuinely useful for black & white but leaves colour film with the inverse of its orange mask. Neutralising that mask properly is [v0.3](ROADMAP.md#v03--inversion-done-properly-bw-and-color) and needs a real pipeline, not a tweak. Your captured files are unaffected — inversion is preview-only for now, and the original is always kept.

> **Python version note:** use **3.10–3.13**. `opencv-python` wheels lag new Python releases, and on 3.14 pip may try to build OpenCV from source, which is a bad time on any platform.

---

## Canon-side setup (for using a real camera)

These steps happen on Canon's website and on the camera itself. They cannot be automated, and you only do them once.

> **CCAPI connects over Wi-Fi or wired LAN — not USB.** The USB cable is used *only* for the one-time activation in step 4. Wired LAN needs an Ethernet port on the body or a WFT accessory; most enthusiast bodies (the R7 included) have neither, so Wi-Fi via your access point is the realistic setup.

1. **Update your camera's firmware** to the latest version.
2. **Register** with [Canon's Developer Community](https://developercommunity.usa.canon.com/) for your region and request CCAPI access. Approval is not instant.
3. **Download** the CCAPI **activation tool** and the **API reference** from Canon.
   ⚠️ Keep both *outside* this repository, or in `ccapi_reference/`, which is git-ignored. **Canon's licence forbids redistributing them** — never commit them, and never paste the reference into an issue or PR.
4. **Run the activation tool** with the camera connected by USB and powered on. Your computer must be online — activation contacts Canon and fails silently offline. Only one camera may be connected. This is a one-time unlock, separate from the menu steps below.
   You'll know it worked when **`Camera Control API` appears in the camera's Wi-Fi settings menu**.
   *If your camera's Advanced User Guide already documents CCAPI, activation is not needed — skip to step 5.*
5. **Connect over Wi-Fi:** `Wi-Fi settings` → `Camera Control API` → `Add connection` → `Add with wizard`, then pick your access point and let the camera take an IP automatically. Register a nickname if prompted.
6. **Read the address off the camera.** The `[Wi-Fi on]` screen shows the SSID and the CCAPI top URL. Use its host and port for `camera.base_url`.
7. **Enable `Auto connect`** in the Camera Control API menu so the camera rejoins on power-on. Worth doing for a fixed copy stand.
8. **Disable auto power-off** while scanning. Bodies sleep aggressively, and a sleeping camera looks exactly like a network fault. (The app also disables this over the API on connect.)

**Only one device can be connected to the camera at a time.** Quit Canon Camera Connect on your phone and any other tethering software, or this app cannot connect.

**Accounts are optional.** If you register one under `Camera Control API` → `Account settings`, CCAPI requires HTTP digest authentication — set `camera.username` and `camera.password` in `config.yaml` to match. Leave both empty if you registered no account.

Then point the app at it:

```bash
cp config.example.yaml config.yaml     # Windows: copy config.example.yaml config.yaml
```

Set `camera.base_url` to the address the camera showed. `config.yaml` is git-ignored because it contains your camera's IP — **never commit it**.

Verify:

```bash
python -m csfs.tools.dump_endpoints
```

### If your camera names things differently

This project never hardcodes CCAPI paths — it reads the endpoint inventory the camera itself publishes and matches it against hints in [`src/csfs/ccapi/endpoints.py`](src/csfs/ccapi/endpoints.py). That's the **only** file containing path assumptions. Canon's own reference recommends this approach, since the available URLs vary by model and firmware.

If `dump_endpoints` reports a capability as MISSING that your reference says your camera has, add the distinguishing path segment to the hints in that file. Everything else adapts automatically.

Hints match on **whole path segments**, never loose substrings — a capability bound to the wrong endpoint fails in ways that are very hard to diagnose, so an honest MISSING is preferred.

Please don't paste Canon's reference text into issues or PRs. The path *your camera reports* is fine to share; Canon's documentation of it is not.

---

## Lens support

- **Autofocus macro lens** — the app can drive focus in steps from the PC.
- **Fully manual macro lens** — the app cannot turn the focus ring. You focus by hand, but while watching a large magnified live view, and you still get the remote shutter and everything else. The app detects this and hides the focus controls rather than offering buttons that do nothing.

## Hardware

- A CCAPI-capable Canon body (R7, R6 II, R10, R5, R6, RP, R3, 90D, 850D and others — check Canon's current list). The R7 is the reference camera, but the code targets the API, not the model.
- A macro lens reaching ~1:1 for 35mm.
- A copy stand, a negative holder, and a backlight — high-CRI if you shoot colour.

---

## Project layout

```
src/csfs/
├── config.py        Config loading: defaults → config.yaml → env vars
├── ccapi/           Thin CCAPI client. No image processing, no UI.
│   ├── client.py        HTTP transport + error translation
│   ├── discovery.py     Ask the camera what it supports
│   ├── endpoints.py     The ONLY place path assumptions live
│   ├── errors.py        Errors that name the real-world cause
│   ├── liveview.py      Stream transports + frame parsers
│   ├── shutter.py       Electronic shutter + settle delay
│   ├── settings.py      Read/write settings by ability
│   └── contents.py      List and download captures
├── processing/      Pure functions over numpy arrays. No network, no UI.
│   ├── invert.py        Negative → positive (v0.1: linear)
│   ├── loupe.py         Magnified focus view
│   └── codec.py         JPEG encode/decode
├── app/             The local web server that wires it together
│   ├── server.py        HTTP API + MJPEG preview
│   ├── session.py       Camera session + live-view frame pump
│   └── static/          The browser UI (no build step)
├── mock/            A fake camera, so development needs no hardware
└── tools/           Setup and diagnostic commands
tests/               Runs entirely without a camera
```

The layering is load-bearing, not decoration: it's what lets you work on colour inversion with no camera attached, and what keeps the tests honest.

## Contributing

Forks and PRs welcome. Keep the three layers decoupled, and add tests for anything in `processing/`.

**Never commit:** Canon's CCAPI reference or activation files, camera IP addresses, `config.yaml`, or your own scans. The `.gitignore` covers all of these, but please check `git status` before committing anyway.

Got a camera model working? A short test report is a genuinely useful contribution.

## Licence

[MIT](LICENSE) — covering **this project's code only**.

Canon's CCAPI, its activation tool, and its reference documentation are Canon's property under separate terms. Nothing here grants you any rights to them; you obtain your own access from Canon.
