# Canon Smart Film Scan

Digitize film negatives with a Canon camera on a copy stand — focusing, framing, and firing the shutter entirely from your computer, with a **live positive preview** so you never have to squint at an inverted image again.

Built on [CCAPI](https://developercommunity.usa.canon.com/), Canon's official Camera Control API. Works with black-and-white **and** colour negatives.

> **Status: early development (v0.1 in progress).**
> The camera-free mock and the CCAPI discovery layer work today. Live view, remote shutter, and inversion are being built on top. See [ROADMAP.md](ROADMAP.md).

---

## Why

Camera scanning gives excellent results but has three chronic annoyances, and this tool targets all three:

| Problem | Fix |
|---|---|
| Pressing the shutter shakes the rig and softens the scan | Remote shutter + **electronic shutter** + a configurable **settle delay** |
| Nailing focus on film *grain* is hard on a small rear screen | Big live view on your monitor, with a magnified loupe, focus peaking, and a numeric sharpness readout |
| Framing and reviewing negatives is unintuitive | **Real-time inversion** — you work in positive |

## What it deliberately does *not* do

This tool controls the camera from outside, over a documented API. **It never modifies camera firmware.** So it cannot change what the camera's own screen shows, cannot bake looks into the files the camera writes to its card, and cannot unlock features the firmware doesn't already expose.

All processing happens on the computer. For film scanning that is exactly what you want, but it's worth stating plainly so expectations are right.

---

## Try it right now, with no camera

You do not need a camera, or CCAPI access, to run this project. A **mock camera** serves synthetic film negatives — complete with an orange mask and a film rebate — over the same API a real body uses.

```bash
git clone <your-fork-url>
cd "Canon Smart Film Scan"

python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate

pip install -r requirements.txt
pip install -e .
```

Start the mock camera:

```bash
python -m csfs.mock.mock_server
```

In a second terminal, ask it what it can do:

```bash
python -m csfs.tools.dump_endpoints
```

You should see a list of API versions and resolved capabilities. That is the whole connection pipeline working.

Run the tests (they start their own mock; nothing else needs to be running):

```bash
pytest
```

> **Python version note:** use **3.10–3.13**. `opencv-python` wheels lag new Python releases, and on 3.14 pip may try to build OpenCV from source, which is a bad time on any platform.

---

## Canon-side setup (for using a real camera)

These steps happen on Canon's website and on the camera itself. They cannot be automated, and you only do them once.

1. **Update your camera's firmware** to the latest version.
2. **Register** with [Canon's Developer Community](https://developercommunity.usa.canon.com/) for your region and request CCAPI access. Approval is not instant.
3. **Download** the CCAPI **activation tool** and the **API reference** from Canon.
   ⚠️ Keep both *outside* this repository, or in `ccapi_reference/`, which is git-ignored. **Canon's licence forbids redistributing them** — never commit them, and never paste the reference into an issue or PR.
4. **Run the activation tool** against your camera. This is a one-time unlock, separate from the menu toggle in the next step.
5. **Enable CCAPI in the camera's menu** and choose a connection:
   - **Wired USB — strongly preferred for a fixed copy stand.** No dropouts, no reconnection after sleep, steadier live-view throughput.
   - **Wi-Fi** otherwise.
   The camera displays the address it's listening on.
6. **Disable auto power-off** while scanning. Bodies sleep aggressively and a sleeping camera looks exactly like a wiring fault.

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

This project never hardcodes CCAPI paths — it reads the endpoint list the camera itself publishes and matches it against hints in [`src/csfs/ccapi/endpoints.py`](src/csfs/ccapi/endpoints.py). That's the **only** file containing path assumptions.

If `dump_endpoints` reports a capability as MISSING that your reference says your camera has, add the distinguishing path segment to the hints in that file. Everything else adapts automatically.

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
│   └── errors.py        Errors that name the real-world cause
├── processing/      Pure functions over numpy arrays. No network, no UI.
├── app/             The local web server that wires it together
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
