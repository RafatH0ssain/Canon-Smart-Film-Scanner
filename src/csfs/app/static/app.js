/* Canon Smart Film Scan -- browser UI.
 *
 * Deliberately plain: no framework, no build step. Someone who has not written
 * front-end code in years should be able to read and change this.
 */

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
  capture: $("capture"),
  captureStatus: $("capture-status"),
  settle: $("settle"),
  shutterMode: $("shutter-mode"),
  outputDir: $("output-dir"),
  settings: $("settings"),
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
  shutter_speed: "Shutter speed",
  white_balance: "White balance",
  shutter_mode: "Shutter mode",
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

  renderSettings(state.settings ?? {});
}

function renderSettings(settings) {
  const names = Object.keys(settings);
  if (names.length === 0) {
    el.settings.innerHTML = `<dt class="empty">&mdash;</dt><dd></dd>`;
    return;
  }
  el.settings.innerHTML = names.map((name) => {
    const label = SETTING_LABELS[name] ?? name;
    return `<dt>${label}</dt><dd>${settings[name].value}</dd>`;
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
  el.captureStatus.textContent = "Settling, then firing…";

  try {
    const result = await post("/api/capture");
    el.captureStatus.className = "capture-status ok";
    el.captureStatus.textContent = `Saved ${result.name}`;
    refreshCaptures();
  } catch (err) {
    el.captureStatus.className = "capture-status bad";
    el.captureStatus.textContent = err.message;
  } finally {
    capturing = false;
    el.capture.disabled = !connected;
  }
}

el.capture.addEventListener("click", capture);

document.addEventListener("keydown", (event) => {
  // Ignore shortcuts while typing, and let the browser keep its own combos.
  if (event.target.matches("input, textarea") || event.metaKey || event.ctrlKey) return;

  if (event.code === "Space") {
    event.preventDefault();   // stop Space scrolling the page
    capture();
  } else if (event.key === "i") {
    el.invert.checked = !el.invert.checked;
    setPreview({ invert: el.invert.checked });
  } else if (event.key === "l") {
    el.loupe.checked = !el.loupe.checked;
    setPreview({ loupe: el.loupe.checked });
  }
});

/* ---------- start ---------- */

refresh();
refreshCaptures();
// Slow poll: keeps settings and stream health current without hammering the
// camera, which is busy serving live view.
setInterval(refresh, 5000);
