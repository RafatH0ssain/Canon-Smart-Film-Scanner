# CLAUDE.md — Instructions for Claude Code

This file tells Claude Code how to build **Canon Smart Film Scan** from an empty folder. Read this fully before writing any code. It assumes the human is a capable programmer with rusty C/assembly and limited Python-tooling recency, and that the project is public on GitHub for others in the same situation.

---

## 1. What we're building (context)

A cross-platform desktop tool that remote-controls a Canon camera over **CCAPI** (Canon's official Camera Control API — a REST/HTTP API) to digitize film negatives on a copy-stand + macro-lens rig. Core value: focus and shoot from a big PC monitor with the camera untouched (no shutter-shake blur), with a **live positive preview** (real-time inversion) and **automated per-roll capture**. Supports **both black-and-white and color** negatives.

Read `ROADMAP.md` in this folder for the full feature plan and milestone order. Build in milestone order; do not jump ahead.

**This project never modifies camera firmware.** It only sends documented CCAPI commands. It cannot change the camera's own display or bake looks into the camera's files. All image processing happens on the computer. Do not attempt or suggest firmware-level changes.

## 2. Critical constraints — read before anything else

1. **Never commit Canon's CCAPI reference documentation.** Canon licenses it per-developer and forbids redistribution. The human will download their own copy from Canon and keep it locally (outside the repo, or in a git-ignored path). When you need endpoint details, **ask the human to point you at their local copy of the reference, then build against it.** Do not hardcode endpoint paths from memory or guesses — CCAPI paths and payloads must come from the human's authoritative local reference, because getting them wrong wastes time and the exact paths are not reliably known from public sources.
2. **Never commit secrets or personal data:** camera IP addresses, Canon activation files, account details, or the user's scanned images/negatives.
3. **Respect the human's hardware.** Nothing here can brick a camera (it's all external API calls), but do use the **electronic shutter** and sensible settle delays, and never issue rapid-fire commands that could destabilize a live session. When in doubt about a command's effect, explain it and ask before running against a real camera.
4. **Camera-agnostic, not R7-only.** The R7 is the reference device, but target the API and detect capabilities at runtime. Don't hardcode R7 assumptions.
5. **Both film types are first-class.** Never implement inversion in a way that assumes B&W only.

## 3. Recommended tech stack

Confirm with the human before diverging, but default to:

- **Language:** Python 3.10+
- **HTTP / CCAPI:** `requests` for control calls; `requests` with streaming (or `httpx`) for the live-view stream.
- **Image processing:** `opencv-python` (`cv2`) + `numpy`.
- **UI:** a **local web app** — `FastAPI` backend serving a processed MJPEG live-view stream plus a minimal HTML/JS/CSS frontend the human opens fullscreen in a browser on their big monitor. Rationale: cross-platform, low-friction for contributors, no native GUI toolkit pain. (A native PySide6 UI is a documented *future* alternative, not the default.)
- **Config:** a git-ignored `config.yaml` (from a committed `config.example.yaml`); allow env-var overrides. Never hardcode the camera IP.
- **Testing:** `pytest`.
- **Mock camera:** a small **mock CCAPI server** (FastAPI) that serves fake live-view frames and accepts shutter/focus calls, so contributors can develop and test with **no camera attached**. Build this early — it unblocks most development.

Keep dependencies minimal and mainstream so newcomers can install easily.

## 4. Target project structure

Create this incrementally as milestones require it — don't scaffold empty files for features not yet being built.

```
Canon Smart Film Scan/
├── README.md                 # user-facing intro + quickstart (write early, keep updated)
├── ROADMAP.md                # already present
├── CLAUDE.md                 # this file
├── LICENSE                   # MIT or Apache-2.0 (ask the human to confirm)
├── .gitignore                # see section 6
├── requirements.txt
├── config.example.yaml       # committed template (no real IPs/secrets)
├── src/
│   └── csfs/                 # "Canon Smart Film Scan"
│       ├── __init__.py
│       ├── ccapi/            # thin CCAPI client — no UI, no image processing
│       │   ├── client.py     # base HTTP session, error handling
│       │   ├── discovery.py  # find camera, list supported API versions/endpoints
│       │   ├── liveview.py   # start/stop + pull the live-view stream
│       │   ├── shutter.py    # electronic-shutter release + settle delay
│       │   ├── focus.py      # AF-drive stepping; detect manual-lens fallback
│       │   └── settings.py   # read/lock exposure & capture settings
│       ├── processing/       # pure functions over frames — trivially unit-testable
│       │   ├── invert_bw.py
│       │   ├── invert_color.py   # orange-mask sampling → normalize → invert → curves
│       │   ├── peaking.py         # focus-peaking overlay
│       │   └── sharpness.py       # acutance/contrast metric for focus confirmation
│       ├── app/              # web server + frontend that wires it together
│       │   ├── server.py
│       │   └── static/
│       └── mock/             # mock CCAPI server for camera-free development
│           └── mock_server.py
└── tests/
```

## 5. From-zero setup sequence

Do these in order. Explain each step to the human as you go, since some steps happen on Canon's side / on the camera and can't be automated.

**Step A — Canon-side prerequisites (human does these; you guide).**
1. Update the camera to the latest firmware.
2. Sign up for Canon's Developer Community for their region and request CCAPI access.
3. Download Canon's **CCAPI activation tool** and the **CCAPI reference** (kept locally, never committed).
4. Run the activation tool to enable CCAPI on the camera (one-time).
5. Enable CCAPI on the camera and choose the connection: **wired USB preferred** for a fixed copy stand; Wi-Fi otherwise. Note the camera's IP/base URL.

**Step B — Dev environment (you can do most of this).**
1. Detect the human's OS and confirm Python 3.10+ is available; if not, guide installation.
2. Create and activate a virtual environment (`python -m venv .venv`).
3. Initialize `requirements.txt` with the stack above; install.
4. Initialize the git repo, add `.gitignore` (section 6) and `config.example.yaml` **before** any first commit, so secrets can never land in history.
5. Choose and add the LICENSE (confirm MIT vs Apache-2.0 with the human).

**Step C — Prove the connection (smallest possible slice).**
1. Ask the human to open their local CCAPI reference; from it, implement `discovery.py` to hit the API root and list supported versions/endpoints.
2. Confirm you can read basic device info. This validates the whole pipeline before building anything bigger.

**Step D — Build the mock server early.**
Implement `mock/mock_server.py` to emulate the handful of endpoints v0.1 needs (discovery, live-view frames, shutter, basic settings). From here on, everything should be developable against the mock with no camera.

**Step E — Build v0.1 (MVP), then proceed milestone by milestone per ROADMAP.md.**

## 6. .gitignore essentials

At minimum, ignore:
```
# secrets & local config
config.yaml
*.local.*
.env

# Canon CCAPI reference & activation — never redistribute
ccapi_reference/
*canon*ccapi*reference*
*.CCA        # activation-related files, if applicable

# user captures / scans
captures/
scans/
*.CR3
*.tif
*.tiff
*.jpg
*.jpeg
*.png      # (allow-list any project sample images explicitly if needed)

# python
.venv/
__pycache__/
*.pyc
.pytest_cache/
dist/
build/
*.egg-info/
```
Adjust with the human — but the secrets, CCAPI reference, and captures rules are non-negotiable.

## 7. Coding conventions

- **Layer discipline:** `ccapi/` knows nothing about images or UI; `processing/` is pure functions over numpy arrays (no network, no UI); `app/` wires them. This makes everything testable and lets contributors work on one layer without a camera.
- **Capability detection over model checks.** Ask the camera/API what it supports; degrade gracefully (e.g. manual-focus lens → hide focus controls, keep the loupe).
- **Non-destructive:** always keep the original capture; write inverted results as new files.
- **Same code path for preview and output** so the live preview matches the saved result.
- **Type hints + docstrings** on public functions. Keep functions small.
- **Tests for every `processing/` function** using synthetic frames (fixtures), runnable without a camera.
- **Clear errors:** wrap CCAPI calls with helpful messages (connection lost, CCAPI not activated, wrong IP, unsupported endpoint).
- **Comments explain *why*,** especially in color inversion where the math is non-obvious.
- **Cross-platform:** no OS-specific paths or shell assumptions; use `pathlib`.

## 8. Milestone-by-milestone guidance

Follow `ROADMAP.md` for scope. Build v0.1 fully and get it working on the human's real rig before moving on. For each milestone: implement against the mock, add tests, then validate against the real camera with the human, then update the README. Do not start a later milestone until the current one works end-to-end.

Special care for **v0.3 color inversion** — this is the hardest part. Implement it as the staged pipeline described in the roadmap (sample film base → normalize orange mask → per-channel invert → per-channel curves), expose the key parameters, and provide good defaults. Test with synthetic "negative" fixtures in addition to real frames.

## 9. Working style with the human

- The human's C/assembly is rusty and reverse engineering is new, but **this project needs neither** — it's applied Python + HTTP + image processing. Keep that framing; don't over-complicate.
- Explain *why* before large changes; teach as you build so the human can maintain and extend it.
- When a step must happen on Canon's site or the physical camera, say so clearly and hand off — don't pretend to automate it.
- Before running any command against the **real** camera (as opposed to the mock), state what it will do.
- Keep the public repo newcomer-friendly: good README, clear setup, honest limitations.

## 10. Quick reference — the guardrails, condensed

- ✅ External CCAPI control only; no firmware modification.
- ✅ Camera-agnostic; detect capabilities.
- ✅ B&W **and** color.
- ✅ Electronic shutter + settle delay for sharp scans.
- ✅ Mock server so development needs no camera.
- ❌ Never commit the CCAPI reference, secrets, IPs, or captures.
- ❌ Never hardcode endpoints from memory — build from the human's local CCAPI reference.
- ❌ Never assume B&W-only.
