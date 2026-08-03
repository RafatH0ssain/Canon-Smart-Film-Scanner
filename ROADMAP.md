# Canon Smart Film Scan — Roadmap

**A remote-control film-scanning tool for Canon cameras, built on Canon's official Camera Control API (CCAPI).**

Digitize developed film negatives with a camera + macro lens copy-stand setup — focusing, framing, and firing the shutter entirely from your computer, with a live positive preview and automated per-roll capture. Because everything is driven from the PC and the shutter fires without anyone touching the rig, scans come out sharper and the workflow is far less tedious.

---

## 1. Vision

Camera-based film scanning (a "copy stand": camera pointing down at a backlit negative through a macro lens) produces excellent results but has three chronic pain points:

1. **Motion blur from pressing the shutter.** Touching the camera vibrates the whole rig during the exposure.
2. **Focusing is fiddly.** You want the film *grain* tack-sharp, and the camera's small rear screen makes that hard to judge.
3. **You're staring at negatives.** Framing a photo while looking at its inverse is unintuitive, and reviewing a whole roll of negatives is worse.

This project solves all three by moving the entire capture loop to the computer: a large live-view preview with focus aids, remote shutter with the electronic (silent) shutter to eliminate mechanical shock, and real-time inversion so you frame and review a *positive* image.

## 2. Who it's for

- Home film developers who scan their own negatives with a digital camera and macro lens.
- Both **black-and-white and color** shooters. (The initial author shoots B&W, but color-negative support is a first-class goal, not an afterthought.)
- Anyone with a **CCAPI-capable Canon body** — this is not R7-specific. The R7 is the reference/test camera, but the code targets the API, not the model.

## 3. What this is *not* (honest limitations)

This tool controls the camera from outside over a documented API. It does **not** modify camera firmware. Therefore it **cannot**:

- Change what the camera's own EVF/LCD displays.
- Bake new "filters" or looks into the JPEG/RAW the camera writes to its card.
- Unlock features the firmware doesn't already expose (e.g. RAW video, removing hardware limits).

All image processing (inversion, peaking, etc.) happens **on the computer**, applied to the live-view stream and/or to captured files after download. That is exactly what film scanning needs, so it's not a real constraint here — but it's worth stating so expectations are correct. Firmware-level modification is the domain of projects like Magic Lantern, which as of this writing has no port for DIGIC X bodies like the R7.

## 4. Hardware & software assumptions

**Hardware**
- A Canon camera that supports CCAPI (R7, R6 II, R10, R5, R6, RP, R3, 90D, 850D, and others — check Canon's current CCAPI compatibility list).
- A macro lens capable of ~1:1 for 35mm, or appropriate magnification for your format.
- A copy stand or repro rig, a backlight (ideally high-CRI for color), and a negative holder.
- **Connection:** CCAPI works over **Wi-Fi** or a **wired LAN** (an Ethernet port on the body, or a WFT accessory). **USB is used only for the one-time activation, never for control** — there is no USB tethering path for CCAPI.
  - Wired LAN is the more stable option where the body has it, but most enthusiast bodies (the R7 included) have no Ethernet port, so **Wi-Fi via your access point is the realistic setup**. Enable the camera's *Auto connect* so it rejoins on power-on, and disable auto power-off while scanning.
  - Only **one device may be connected to the camera at a time**. Quit Canon Camera Connect and any other tethering software first.

**Lens focus caveat**
- With an **autofocus-capable** macro lens, the app can drive focus in steps from the PC.
- With a **fully manual** macro lens, the app cannot turn the focus ring — you focus by hand, but while watching the large magnified live view (still a big sharpness win), and you still get the remote shutter and everything else.
- The app must detect and adapt to which case applies.

**Software**
- Python 3.10+.
- Cross-platform: Windows, macOS, Linux (including Raspberry Pi as a stretch target).

## 5. The CCAPI licensing constraint (important for an open-source repo)

CCAPI access is granted by Canon per developer, after signing up to Canon's Developer Community. The activation tool and — critically — **the API reference documentation are covered by Canon's license terms and may not be redistributed.**

Consequences for this project:
- **The Canon CCAPI reference PDF/docs must never be committed to this repository.** Each contributor obtains their own copy from Canon.
- The repo may contain original code that *calls* the API, plus our own notes describing observed behavior, but not Canon's copyrighted reference text.
- Setup docs must walk users through getting their own CCAPI access.

This is baked into `.gitignore` and the contributing guidelines.

## 6. Architecture overview

```
┌──────────────┐   CCAPI (HTTP/REST over USB or Wi-Fi)   ┌──────────────┐
│  Canon body  │ <-------------------------------------> │  CCAPI client │
│  (R7 etc.)   │   liveview stream / shutter / focus /   │  (thin layer) │
└──────────────┘   settings / file download              └──────┬───────┘
                                                                 │
                                                      ┌──────────▼──────────┐
                                                      │  Processing engine   │
                                                      │  invert (bw/color),  │
                                                      │  peaking, sharpness, │
                                                      │  alignment           │
                                                      └──────────┬──────────┘
                                                                 │
                                                      ┌──────────▼──────────┐
                                                      │   Local web UI       │
                                                      │  (browser on your    │
                                                      │   big monitor)       │
                                                      └─────────────────────┘
```

**Why a local web UI:** serving the processed live-view stream to a browser tab means the "big screen" is just a fullscreen browser window, it's identical across operating systems, and contributors don't have to wrestle with native GUI toolkits. Latency over localhost is low. A native UI (e.g. PySide6) is a possible future alternative but is not the default.

**Layering:** the CCAPI client is a thin, well-tested wrapper with no UI or image-processing knowledge. The processing engine is pure functions over frames (easy to unit-test). The web app wires them together. This separation keeps each part independently testable and lets contributors work on, say, color inversion without needing a camera attached.

## 7. Feature roadmap

Milestones are ordered so that each one is independently useful and testable. Version numbers are indicative, not promises.

### v0.1 — MVP: sharp, shake-free remote capture
The smallest thing that fixes the core problem.
- CCAPI connection + device discovery (find the camera, confirm supported API versions).
- Live-view stream pulled to the PC and shown in the browser at a usable size.
- **Magnified focus loupe:** zoom a region of the live view to grain level to confirm focus.
- **Remote shutter** via CCAPI.
- **Electronic (silent) shutter** selected to eliminate mechanical shutter shock.
- **Settle delay:** configurable pause between "trigger" and actual exposure so residual vibration damps out.
- Simple **invert toggle** (basic linear inversion) so you can preview a positive.
- Captured file auto-downloads to a chosen folder.

*Outcome: you focus and shoot from your monitor, touching nothing on the rig.*

### v0.2 — Focus control & focus aids
- **Remote focus stepping** for AF-capable lenses (near/far, coarse/fine increments) with keyboard shortcuts.
- Graceful detection + fallback for manual-focus lenses (hide focus controls, keep the magnified loupe).
- **Focus peaking** overlay on the live view (highlight highest-contrast edges) so nailing grain focus is obvious rather than a guess.
- On-demand **sharpness readout** (e.g. a numeric contrast/acutance metric on the magnified region) for objective focus confirmation.

### v0.3 — Inversion done properly (B&W and color)
Real film inversion, not just a linear flip.
- **B&W inversion:** tone-curve-aware inversion with adjustable black/white points; handle the film base density.
- **Color-negative inversion** (the hard, high-value part):
  1. Sample the **film base / rebate** (the unexposed orange mask) as a reference.
  2. Normalize/white-balance against that mask to neutralize the orange cast.
  3. Per-channel inversion.
  4. Per-channel curves/gamma correction to restore natural color.
  5. Expose the key parameters so users can taste-tune, with sensible auto defaults.
- Apply inversion **live** (preview) and to **downloaded captures** (output), using the same code path so preview matches result.
- Save both the raw capture and the inverted result, so the user keeps the original.

### v0.4 — Alignment / parallelism checker
Attacks the *other* main cause of soft scans: sensor plane not parallel to the film.
- Shoot a flat target (or the negative itself) and compute **corner-by-corner sharpness**.
- Report which corners are soft and by how much, so the user can level the copy stand once and verify it's square.
- Optional live "tilt" indicator during setup.

### v0.5 — Roll automation & organization
Removes the tedium of a 36-frame roll.
- One-key **capture → auto-download → auto-invert → save** cycle.
- Structured **naming and foldering**: e.g. `Roll014/Roll014_Frame07.tif`, configurable templates.
- Per-roll **session metadata** (film stock, developer, date, notes) written alongside as a sidecar file.
- Batch **re-invert / re-export** of a whole roll after tweaking parameters, without rescanning.

### v0.6 — Consistent exposure & capture settings
- **Lock exposure** (and other relevant settings) via the API at the start of a roll so every frame is captured identically — critical for predictable batch inversion.
- Preset **profiles** per film stock / format, saved and reloadable.
- Optional light-source consistency checks / warnings.

### v1.0 — Polish, packaging, docs
- Clean install path (pip/pipx, maybe a one-file launcher).
- Robust error handling and reconnection logic.
- Thorough README, setup guide, and troubleshooting.
- **Verified support across multiple CCAPI camera models** (community-contributed test reports).
- Sample outputs and a short demo.

### Post-1.0 — stretch ideas
- **Dust/scratch heuristics** (software only — note that true infrared ICE-style removal is impossible without an IR channel, so this is limited to CV heuristics; set expectations honestly).
- **Auto-crop and auto-straighten** to the film frame.
- **Multi-frame stitching** for medium/large format scanned in tiles.
- **Tethered contact-sheet view**: whole-roll thumbnail wall as you scan.
- **RAW-aware pipeline**: pull/download RAW and do inversion on the fuller-bit-depth data where the workflow allows.
- Optional **native desktop UI** (PySide6) as an alternative to the browser UI.
- **Raspberry Pi** headless capture appliance.
- Integration hooks / export presets for common editors.

## 8. Design principles

- **API-targeted, not model-targeted.** Prefer capability detection over hardcoding one camera's quirks.
- **Both film types are first-class.** Never assume B&W; color must work.
- **Separation of concerns.** CCAPI client / processing / UI stay decoupled and independently testable.
- **Developable without a camera.** A mock CCAPI server (see CLAUDE.md) lets contributors build and test most things with no hardware attached.
- **Respect Canon's license.** No redistribution of the CCAPI reference; each user brings their own access.
- **Keep the original.** Always retain the un-inverted capture; inversion is non-destructive.
- **Cross-platform from day one.**

## 9. Contributing (summary)

- Fork, branch, PR. Keep the three layers decoupled.
- Do **not** commit: the Canon CCAPI reference, camera IPs, activation files, or any personal captures.
- New camera model works for you? Add a short test report.
- See `CLAUDE.md` for the from-zero development setup and conventions.

## 10. License

Choose a permissive open-source license (MIT or Apache-2.0 recommended) so others in the same situation can freely use and extend it. Note clearly that the license covers *this project's code only* — Canon's CCAPI and its documentation have their own separate terms.
