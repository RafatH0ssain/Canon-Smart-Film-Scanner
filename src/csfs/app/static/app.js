/* Canon Smart Film Scan -- browser UI. No framework, no build step. */

const $ = (id) => document.getElementById(id);

const el = {
  chip: $("status-chip"),
  connect: $("connect"),
  preview: $("preview"),
  placeholder: $("placeholder"),
  viewer: $("viewer"),
  loupeHint: $("loupe-hint"),
  invert: $("invert"),
  loupe: $("loupe"),
  zoom: $("zoom"),
  zoomValue: $("zoom-value"),
  cameraZoomBlock: $("camera-zoom-block"),
  cameraZoom: $("camera-zoom"),
  peaking: $("peaking"),
  peakingSensitivity: $("peaking-sensitivity"),
  peakingValue: $("peaking-value"),
  focusControls: $("focus-controls"),
  focusUnavailable: $("focus-unavailable"),
  focusReason: $("focus-reason"),
  focusRecheck: $("focus-recheck"),
  lensName: $("lens-name"),
  measure: $("measure"),
  sharpnessBlock: $("sharpness-block"),
  sharpnessBar: $("sharpness-bar"),
  sharpnessNow: $("sharpness-now"),
  sharpnessBest: $("sharpness-best"),
  sharpnessReset: $("sharpness-reset"),
  corners: $("corners"),
  settingsControls: $("settings-controls"),
  preparedNote: $("prepared-note"),
  capture: $("capture"),
  captureStatus: $("capture-status"),
  settle: $("settle"),
  shutterMode: $("shutter-mode"),
  outputDir: $("output-dir"),
  captures: $("captures"),
  toast: $("toast"),
};

let connected = false;
let capturing = false;
let toastTimer = null;

/* ---------- helpers ---------- */

function toast(message) {
  el.toast.textContent = message;
  el.toast.hidden = false;
  clearTimeout(toastTimer);
  // Long enough to read a multi-line connection error without rushing.
  toastTimer = setTimeout(() => { el.toast.hidden = true; }, 9000);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) detail = typeof body.detail === "string"
        ? body.detail
        : JSON.stringify(body.detail);
    } catch { /* response had no JSON body; the status line will do */ }
    throw new Error(detail);
  }
  return response.json();
}

const post = (path, body) =>
  api(path, { method: "POST", body: JSON.stringify(body ?? {}) });

/* ---------- rendering ---------- */

const SETTING_LABELS = {
  iso: "ISO",
  aperture: "Aperture",
  shutter_speed: "Shutter",
  white_balance: "White bal.",
  shutter_mode: "Shutter mode",
  af_operation: "AF operation",
  drive_mode: "Drive mode",
};

function render(state) {
  connected = state.connected;

  el.chip.textContent = connected
    ? (state.streaming ? "Live" : "Connected (no stream)")
    : "Disconnected";
  el.chip.dataset.state = connected ? (state.streaming ? "on" : "bad") : "off";

  el.connect.textContent = connected ? "Disconnect" : "Connect";
  el.capture.disabled = !connected || capturing;

  el.preview.hidden = !state.streaming;
  el.placeholder.hidden = state.streaming;
  el.loupeHint.hidden = !state.streaming || !state.preview.loupe;

  if (state.stream_error) toast(state.stream_error);

  el.settle.textContent = `${state.settle_delay_s.toFixed(2)} s`;
  el.shutterMode.textContent = state.shutter_mode ?? "camera default";
  el.outputDir.textContent = state.output_dir;

  el.invert.checked = state.preview.invert;
  el.loupe.checked = state.preview.loupe;
  el.zoom.value = state.preview.loupe_zoom;
  el.zoomValue.textContent = `${Number(state.preview.loupe_zoom).toFixed(1)}×`;

  el.cameraZoomBlock.hidden = !state.has_camera_zoom;

  el.peaking.checked = state.preview.peaking;
  el.peakingSensitivity.value = state.preview.peaking_sensitivity;
  el.peakingValue.textContent =
    `${Math.round(state.preview.peaking_sensitivity * 100)}%`;

  el.measure.disabled = !state.streaming;
  renderFocus(state.focus);
  renderSettings(state.settings ?? {});
  renderPrepared(state.prepared ?? {});
}

function renderFocus(focus) {
  if (!focus) {
    el.focusControls.hidden = true;
    el.focusUnavailable.hidden = true;
    return;
  }
  el.focusControls.hidden = !focus.available;
  el.focusUnavailable.hidden = focus.available;
  el.lensName.textContent = focus.lens;
  if (!focus.available) el.focusReason.textContent = focus.reason ?? "";
}

function renderPrepared(prepared) {
  const names = Object.keys(prepared);
  el.preparedNote.hidden = names.length === 0;
  if (names.length === 0) return;
  const changes = names
    .map((n) => `${SETTING_LABELS[n] ?? n} → ${prepared[n]}`)
    .join(", ");
  el.preparedNote.textContent = `Changed on your camera for scanning: ${changes}`;
}

function renderSettings(settings) {
  const names = Object.keys(settings);
  if (names.length === 0) {
    el.settingsControls.innerHTML = `<p class="note">Not connected.</p>`;
    return;
  }
  // Only redraw when something changed, or the poll fights the user mid-select.
  const signature = JSON.stringify(settings);
  if (el.settingsControls.dataset.signature === signature) return;
  el.settingsControls.dataset.signature = signature;

  el.settingsControls.innerHTML = names.map((name) => {
    const { value, ability } = settings[name];
    const label = SETTING_LABELS[name] ?? name;
    if (!ability || ability.length <= 1) {
      // Not selectable right now -- e.g. exposure compensation in manual mode.
      return `<div class="setting"><label>${label}</label>
              <select disabled><option>${value}</option></select></div>`;
    }
    const options = ability
      .map((v) => `<option${v === value ? " selected" : ""}>${v}</option>`)
      .join("");
    return `<div class="setting"><label>${label}</label>
            <select data-setting="${name}">${options}</select></div>`;
  }).join("");
}

async function refresh() {
  try {
    render(await api("/api/status"));
  } catch (err) {
    toast(`Could not read status: ${err.message}`);
  }
}

async function refreshCaptures() {
  try {
    const data = await api("/api/captures");
    el.captures.innerHTML = data.files.length
      ? data.files.map((name) => `<li>${name}</li>`).join("")
      : `<li class="empty">Nothing yet.</li>`;
  } catch { /* the capture list is not important enough to interrupt for */ }
}

/* ---------- actions ---------- */

el.connect.addEventListener("click", async () => {
  el.connect.disabled = true;
  try {
    const state = await post(connected ? "/api/disconnect" : "/api/connect");
    render(state);
    if (state.streaming) {
      // Cache-bust so a reconnect does not reuse the previous dead stream.
      el.preview.src = `/stream.mjpg?t=${Date.now()}`;
    } else {
      el.preview.removeAttribute("src");
    }
  } catch (err) {
    toast(err.message);
  } finally {
    el.connect.disabled = false;
  }
});

async function setPreview(patch) {
  try {
    render(await post("/api/preview", patch));
  } catch (err) {
    toast(err.message);
  }
}

el.invert.addEventListener("change", () => setPreview({ invert: el.invert.checked }));
el.loupe.addEventListener("change", () => setPreview({ loupe: el.loupe.checked }));

el.zoom.addEventListener("input", () => {
  el.zoomValue.textContent = `${Number(el.zoom.value).toFixed(1)}×`;
});
el.zoom.addEventListener("change", () =>
  setPreview({ loupe_zoom: Number(el.zoom.value), loupe: true }));

// Click the preview to move the loupe to that point.
el.preview.addEventListener("click", (event) => {
  const box = el.preview.getBoundingClientRect();
  setPreview({
    loupe: true,
    loupe_x: (event.clientX - box.left) / box.width,
    loupe_y: (event.clientY - box.top) / box.height,
  });
});

el.cameraZoom.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  try {
    await post("/api/camera-zoom", { value: button.dataset.value });
    [...el.cameraZoom.children].forEach((b) => b.classList.toggle("active", b === button));
  } catch (err) {
    toast(err.message);
  }
});

async function capture() {
  if (!connected || capturing) return;
  capturing = true;
  el.capture.disabled = true;
  el.captureStatus.hidden = false;
  el.captureStatus.className = "capture-status busy";

  // A capture can take 30s, mostly downloading tens of megabytes over Wi-Fi.
  // Show elapsed time so slow-but-healthy does not look like a hang.
  const started = Date.now();
  const tick = setInterval(() => {
    const secs = Math.round((Date.now() - started) / 1000);
    const stage = secs < 3 ? "Settling, then firing…"
      : secs < 8 ? "Fired — waiting for the card…"
      : "Downloading from the camera…";
    el.captureStatus.textContent = `${stage} (${secs}s)`;
  }, 250);

  try {
    const result = await post("/api/capture");
    el.captureStatus.className = "capture-status ok";
    el.captureStatus.textContent =
      `Saved ${result.name} in ${Math.round((Date.now() - started) / 1000)}s`;
    refreshCaptures();
  } catch (err) {
    el.captureStatus.className = "capture-status bad";
    el.captureStatus.textContent = err.message;
  } finally {
    clearInterval(tick);
    capturing = false;
    el.capture.disabled = !connected;
  }
}

el.capture.addEventListener("click", capture);

/* ---------- peaking ---------- */

el.peaking.addEventListener("change", () => setPreview({ peaking: el.peaking.checked }));

el.peakingSensitivity.addEventListener("input", () => {
  el.peakingValue.textContent = `${Math.round(el.peakingSensitivity.value * 100)}%`;
});
el.peakingSensitivity.addEventListener("change", () =>
  setPreview({ peaking_sensitivity: Number(el.peakingSensitivity.value), peaking: true }));

/* ---------- focus drive ---------- */

async function driveFocus(direction, size) {
  try {
    await post("/api/focus", { direction, size });
  } catch (err) {
    toast(err.message);
    refresh();   // the controller may now know focus drive is unavailable
  }
}

el.focusControls.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  driveFocus(button.dataset.dir, button.dataset.size);
});

el.focusRecheck.addEventListener("click", async () => {
  try {
    render(await post("/api/focus/recheck"));
  } catch (err) {
    toast(err.message);
  }
});

/* ---------- sharpness ---------- */

// The metric has no natural maximum, so the bar is drawn relative to the best
// reading of this session.
function renderSharpness(data) {
  el.sharpnessBlock.hidden = false;
  el.sharpnessNow.textContent = data.sharpness.toFixed(1);
  el.sharpnessBest.textContent = data.best.toFixed(1);
  const ratio = data.best > 0 ? Math.min(data.sharpness / data.best, 1) : 0;
  el.sharpnessBar.style.width = `${ratio * 100}%`;

  el.corners.innerHTML = Object.entries(data.corners)
    .map(([k, v]) => `<dt>${k.replace(/_/g, " ")}</dt><dd>${v.toFixed(0)}</dd>`)
    .join("");
}

async function measureSharpness() {
  try {
    renderSharpness(await api("/api/sharpness"));
  } catch (err) {
    toast(err.message);
  }
}

el.measure.addEventListener("click", measureSharpness);
el.sharpnessReset.addEventListener("click", async () => {
  await post("/api/sharpness/reset");
  measureSharpness();
});

/* ---------- camera settings ---------- */

el.settingsControls.addEventListener("change", async (event) => {
  const select = event.target.closest("select[data-setting]");
  if (!select) return;
  try {
    render(await post("/api/setting", {
      name: select.dataset.setting,
      value: select.value,
    }));
  } catch (err) {
    toast(err.message);
    refresh();   // put the control back to what the camera actually has
  }
});

/* ---------- keyboard ---------- */

document.addEventListener("keydown", (event) => {
  // Ignore shortcuts while typing, and let the browser keep its own combos.
  if (event.target.matches("input, textarea, select") || event.metaKey || event.ctrlKey) return;

  // Modifiers pick the step size, so one pair of keys covers all three.
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    const size = event.altKey ? "coarse" : event.shiftKey ? "medium" : "fine";
    driveFocus(event.key === "ArrowLeft" ? "near" : "far", size);
    return;
  }

  if (event.code === "Space") {
    event.preventDefault();   // stop Space scrolling the page
    capture();
  } else if (event.key === "i") {
    el.invert.checked = !el.invert.checked;
    setPreview({ invert: el.invert.checked });
  } else if (event.key === "l") {
    el.loupe.checked = !el.loupe.checked;
    setPreview({ loupe: el.loupe.checked });
  } else if (event.key === "p") {
    el.peaking.checked = !el.peaking.checked;
    setPreview({ peaking: el.peaking.checked });
  } else if (event.key === "m") {
    measureSharpness();
  }
});

/* ---------- start ---------- */

refresh();
refreshCaptures();
// Slow poll: the camera is busy serving live view.
setInterval(refresh, 5000);
