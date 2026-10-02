import { Renderer, rawDragCenter, DEFAULT_PERSPECTIVE_FOV, MIN_PERSPECTIVE_FOV,
  MAX_PERSPECTIVE_FOV, validPerspectiveFov, perspectiveZoom } from "./projection.js";
import { checkGeometry } from "./geometry.js";
import { TelemetryState } from "./telemetry.js";
import { ErrorState } from "./errors.js";
import { Station } from "./station.js";

const $ = (id) => document.getElementById(id);
const errors = new ErrorState();
const telemetry = new TelemetryState();
const perspectiveDefaultKey = "pigeonvision.perspectiveDefaultFov.v1";
let perspectiveDefaultFov = DEFAULT_PERSPECTIVE_FOV;
let perspectiveDefaultStatus = "1× is the 110° factory view. Scroll to choose.";
try {
  const saved = JSON.parse(localStorage.getItem(perspectiveDefaultKey));
  if (validPerspectiveFov(saved)) {
    perspectiveDefaultFov = saved;
    perspectiveDefaultStatus = `Browser default: ${(saved * 180 / Math.PI).toFixed(1)}°. 1× remains 110°.`;
  }
} catch { /* Unavailable storage or invalid JSON keeps the factory default. */ }
const state = {
  connected: false,
  replay: false,
  playing: false,
  status: null,
  calibration: null,
  colourCalibrationKey: null,
  pair: null,
  presentedPair: null,
  pairs: 0,
  resetCount: 0,
  metadata: {},
  descriptions: {},
  geometrySessionId: null,
  frames: {},
  geometry: { errors: [], unverified: [] },
  error: null,
  colourRequested: false,
  colourStrength: { A: 1, B: 1 },
};
const camera = () => ({
  decoder: null,
  codec: null,
  waiting: true,
  epoch: 0,
  pending: [],
  decoded: 0,
  unmatched: 0,
  resets: 0,
  lastArrival: 0,
  lastTimestamp: null,
  presentedAt: null,
  presentedTimestamp: null,
  presentationCurrent: false,
  heldForMetadata: false,
  colourHeldFrames: 0,
  size: null,
  submitted: 0,
  outputs: 0,
});
const cameras = { A: camera(), B: camera() };
const frameColourVerification = new WeakMap();
let pairLimit = Number($("max-skew").value);
let renderer, station,
  socket,
  connectionEpoch = 0;
function renderErrors() {
  state.error = errors.message;
  $("error").textContent = state.error || "";
  $("error").hidden = state.error === null;
}
function fail(message, component = "viewer", recoverable = false) {
  errors.set(message, component, recoverable);
  renderErrors();
}
function clearError(component) {
  errors.ready(component);
  renderErrors();
}
function resetCamera(name) {
  const c = cameras[name];
  c.epoch++;
  if (c.decoder?.state !== "closed") c.decoder?.close();
  c.decoder = null;
  c.codec = null;
  c.waiting = true;
  for (const f of c.pending) f.close();
  c.pending = [];
  c.resets++;
  c.lastArrival = 0;
  c.lastTimestamp = null;
  c.presentationCurrent = false;
  c.heldForMetadata = false;
  c.size = null;
  c.submitted = c.outputs = 0;
}
function reset({ preserveGeometry = false } = {}) {
  for (const n of ["A", "B"]) resetCamera(n);
  state.pair = null;
  if (!preserveGeometry) {
    state.frames = {};
    state.descriptions = {};
    state.geometrySessionId = null;
  }
  state.resetCount++;
  validateGeometry();
  station.updateViewFrame(displayedFrame());
}
function alignmentDescription(bundle) {
  if (bundle.rig_alignment_status === "nominal_operator_geometry") {
    const baseline = bundle.nominal_geometry?.baseline_m;
    const spacing = Number.isFinite(baseline) && baseline > 0
      ? `, approximately ${Number(baseline.toPrecision(3))} m apart` : "";
    return `Nominal 180° alignment${spacing}; roll is assumed. Fine alignment is unmeasured; nearby objects may double.`;
  }
  return `Rig alignment: ${bundle.rig_alignment_status || "unverified"}.`;
}
function lensValidationDescription(bundle) {
  const validation = ["A", "B"].map((name) => bundle.validation?.cameras?.[name]);
  if (validation.every((camera) => camera?.held_out_views === 0))
    return "Lens fits are training-only; held-out accuracy is unverified.";
  if (validation.some((camera) => !Number.isInteger(camera?.held_out_views) || camera.held_out_views < 3))
    return "Lens accuracy is unverified; held-out checks are incomplete.";
  if (validation.some((camera) => camera.thresholds_met === false))
    return "Lens fits miss held-out accuracy targets.";
  return validation.every((camera) => camera.thresholds_met === true)
    ? "Lens fits meet held-out accuracy targets." : "Lens accuracy is unverified.";
}
function calibrationDescription(bundle) {
  const bounded = Object.values(bundle.cameras).every((camera) => camera.max_theta_deg != null);
  return `${alignmentDescription(bundle)} ${lensValidationDescription(bundle)} ${bounded ? "Angular limits supplied." : "Edge coverage is unvalidated."}`;
}
function configureCalibration(bundle) {
  const firstCalibration = !state.calibration;
  const hadColour = !!state.calibration?.display_colour;
  state.calibration = bundle;
  renderer.calibration = bundle;
  // Gains may change without changing the physical capture they apply to.
  // A different camera/geometry bundle must not inherit held-frame verification.
  state.colourCalibrationKey = JSON.stringify(bundle?.cameras ?? null);
  renderer.colourCalibrationKey = state.colourCalibrationKey;
  for (const name of ["A", "B"])
    if (![renderer.raw[name], renderer.pair[name]].some(t => t.colourVerification === state.colourCalibrationKey))
      cameras[name].heldForMetadata = false;
  const colour = bundle?.display_colour;
  if (!colour || !hadColour) state.colourRequested = !!colour;
  if (colour && !hadColour)
    for (const name of ["A", "B"]) {
      const strength = colour.camera_strengths?.[name];
      state.colourStrength[name] = Number.isFinite(strength) && strength >= 0 && strength <= 1 ? strength : 1;
    }
  applyColourStrengths();
  updateColourControls();
  for (const o of $("view").options)
    if (!["a", "b"].includes(o.value)) o.disabled = !bundle;
  if (!bundle) {
    $("calibration").textContent =
      "Raw cameras are available. Lens models and a declared camera alignment are needed for the panorama.";
    $("view").value = "a";
    renderer.mode = "a";
  } else {
    $("calibration").textContent = calibrationDescription(bundle);
    if (firstCalibration) {
      renderer.mode = "perspective";
      $("view").value = "perspective";
    }
  }
  validateGeometry();
  updateFocusControls();
  renderer.draw();
}

function validateGeometry() {
  if (!state.calibration) return;
  state.geometry = checkGeometry(
    state.calibration,
    state.descriptions,
    state.frames,
    { A: cameras.A.size, B: cameras.B.size },
  );
  const blocked = state.geometry.errors.length > 0;
  if (blocked)
    for (const c of Object.values(cameras)) c.heldForMetadata = false;
  updateColourControls();
  renderer.calibration = blocked ? null : state.calibration;
  for (const option of $("view").options)
    if (!["a", "b"].includes(option.value)) option.disabled = blocked;
  if (blocked) {
    renderer.mode = "a";
    $("view").value = "a";
    updateFocusControls();
  }
  const bundle = state.calibration;
  $("calibration").textContent =
    `${calibrationDescription(bundle)} ` +
    (blocked
      ? `PANORAMA DISABLED: ${state.geometry.errors.join("; ")}.`
      : state.geometry.unverified.length
        ? `Runtime geometry unverified: ${state.geometry.unverified.join("; ")}.`
        : "Capture crop, orientation, dimensions and camera identity match calibration.");
}

function colourBaseline(profile) {
  const scale = profile?.common_headroom_scale;
  return Number.isFinite(scale) && scale >= 0.5 && scale <= 1 ? scale : 1;
}

function applyColourStrengths() {
  const profile = state.calibration?.display_colour;
  const base = colourBaseline(profile);
  for (const name of ["A", "B"])
    renderer.setColourCorrection(name, profile
      ? profile.gains[name].map((gain) => base + state.colourStrength[name] * (gain - base))
      : [1, 1, 1]);
}

function updateColourControls() {
  const profile = state.calibration?.display_colour;
  const verified = !state.geometry.errors.length && !state.geometry.unverified.length;
  $("colour-controls").hidden = !profile;
  renderer.colourRequested = !!profile && state.colourRequested;
  renderer.colourEnabled = renderer.colourRequested && verified;
  $("colour-toggle").disabled = !verified;
  $("colour-toggle").setAttribute("aria-pressed", String(renderer.colourEnabled));
  $("colour-toggle").textContent = renderer.colourEnabled ? "Show original colours" : "Match camera colours";
  for (const name of ["A", "B"]) {
    const input = $("colour-strength-" + name.toLowerCase());
    const percent = state.colourStrength[name] * 100;
    input.disabled = !profile || !verified;
    input.value = String(percent);
    $("colour-strength-value-" + name.toLowerCase()).textContent = `${Math.round(percent)}%`;
  }
  $("colour-strength-hint").textContent = colourBaseline(profile) < 1
    ? "0% removes the balance but keeps the preview dimming. Toggle shows originals."
    : "0% removes the balance. Toggle shows originals.";
  $("colour-status").textContent = Object.values(cameras).some(c => c.heldForMetadata)
    ? "Waiting for camera metadata · image held"
    : !verified
    ? "Waiting for camera identity and geometry."
    : renderer.colourEnabled
      ? ["colorchecker_neutrals", "measured_neutral_surfaces"].includes(profile.reference_target)
        ? "Neutral balance · preview"
        : `Matched to camera ${profile.reference_camera} · preview`
      : "Original camera colours";
}

function pairFrames() {
  const a = cameras.A.pending,
    b = cameras.B.pending;
  const maxSkew = pairLimit * 1000;
  while (a.length && b.length) {
    const delta = a[0].timestamp - b[0].timestamp;
    // Timestamp order is retained. Never manufacture a frame or rebase a camera.
    if (Math.abs(delta) <= maxSkew) {
      const fa = a.shift(),
        fb = b.shift();
      renderer.upload("A", fa, true, frameColourVerification.get(fa));
      renderer.upload("B", fb, true, frameColourVerification.get(fb));
      state.pair = {
        a: fa.timestamp,
        b: fb.timestamp,
        skew: delta,
        at: performance.now(),
      };
      state.presentedPair = state.pair;
      state.pairs++;
      fa.close();
      fb.close();
      station.updateViewFrame({ pts: Math.min(state.pair.a, state.pair.b),
        now: state.pair.at, replay: state.replay, frameAgeMs: 0,
        paired: state.pair.a === state.pair.b });
      renderer.draw();
    } else {
      const name = delta < 0 ? "A" : "B";
      cameras[name].pending.shift().close();
      cameras[name].unmatched++;
    }
  }
  for (const name of ["A", "B"])
    while (cameras[name].pending.length > 6) {
      cameras[name].pending.shift().close();
      cameras[name].unmatched++;
    }
}

function decoded(name, frame, epoch) {
  const c = cameras[name];
  if (epoch !== c.epoch) {
    frame.close();
    return;
  }
  c.decoded++;
  c.outputs++;
  c.lastArrival = performance.now();
  c.lastTimestamp = frame.timestamp;
  c.size = [frame.displayWidth, frame.displayHeight];
  validateGeometry();
  const verification = !state.geometry.errors.length && !state.geometry.unverified.length
    ? state.colourCalibrationKey : null;
  // Missing metadata must not replace a verified corrected image with a brief
  // original-colour frame. Keep decoding its reference chain, but discard this
  // output until fresh evidence verifies it. Explicit mismatches still expose
  // new content without applying correction, as does a different calibration.
  const hold = verification === null && !state.geometry.errors.length && renderer.colourRequested &&
    [renderer.raw[name], renderer.pair[name]].some(t => t.colourVerification === state.colourCalibrationKey);
  c.heldForMetadata = hold;
  if (hold) {
    c.colourHeldFrames = Math.min(Number.MAX_SAFE_INTEGER, c.colourHeldFrames + 1);
    c.presentationCurrent = false;
    state.pair = null;
    frame.close();
    updateColourControls();
    station.updateViewFrame(displayedFrame());
    return;
  }
  frameColourVerification.set(frame, verification);
  renderer.upload(name, frame, false, verification);
  c.presentedTimestamp = frame.timestamp;
  c.presentedAt = c.lastArrival;
  c.presentationCurrent = true;
  updateColourControls();
  c.pending.push(frame);
  c.pending.sort((x, y) => x.timestamp - y.timestamp);
  pairFrames();
  renderer.draw();
}

async function accessUnit(buffer, epoch) {
  if (epoch !== connectionEpoch) return;
  const bytes = new Uint8Array(buffer);
  if (bytes.length < 4) throw Error("Truncated frame message");
  const length = new DataView(buffer).getUint32(0);
  if (length > 16384 || length + 4 >= bytes.length)
    throw Error("Invalid frame header");
  const header = JSON.parse(
    new TextDecoder().decode(bytes.subarray(4, 4 + length)),
  );
  const name = header.camera_id,
    c = cameras[name];
  if (
    !c ||
    header.type !== "frame" ||
    !Number.isSafeInteger(header.timestamp_us)
  )
    throw Error("Invalid camera frame header");
  if (c.decoder?.decodeQueueSize > 8) {
    resetCamera(name);
    state.pair = null;
  }
  if (!c.decoder || c.codec !== header.codec) {
    if (!header.keyframe) return;
    resetCamera(name);
    const current = c.epoch;
    const config = { codec: header.codec, optimizeForLatency: true };
    const support = await VideoDecoder.isConfigSupported(config);
    if (epoch !== connectionEpoch || current !== c.epoch) return;
    if (!support.supported)
      throw Error(
        `Browser cannot decode ${header.codec}. Use a desktop Chrome build with H.264 support.`,
      );
    c.decoder = new VideoDecoder({
      output: (f) => decoded(name, f, current),
      error: (e) => {
        fail(`Camera ${name} decoder: ${e.message}`, `decoder:${name}`);
        resetCamera(name);
        state.pair = null;
      },
    });
    c.decoder.configure(config);
    c.codec = header.codec;
    c.waiting = true;
  }
  if (c.waiting && !header.keyframe) return;
  c.waiting = false;
  c.decoder.decode(
    new EncodedVideoChunk({
      type: header.keyframe ? "key" : "delta",
      timestamp: header.timestamp_us,
      data: bytes.subarray(4 + length),
    }),
  );
  c.submitted++;
}

async function message(value, epoch = connectionEpoch) {
  if (value.type === "control_owner") {
    station.owner(value.can_control);
    return;
  }
  if (value.type === "view") { station.receiveView(value.view, value.initial === true); return; }
  if (value.type === "reference") { station.reference(value.reference); return; }
  if (value.type === "bootstrap_start") {
    for (const item of value.metadata || []) await message(item, epoch);
    return;
  }
  if (value.type === "bootstrap_checkpoint") {
    // flush() would require another keyframe. Wait for actual output callbacks
    // before acknowledging the next bounded batch in this same GOP instead.
    const deadline = performance.now() + 2000;
    const currentSocket = socket;
    const generations = Object.values(cameras).map(c => c.epoch);
    while (Object.values(cameras).some(c => c.outputs < c.submitted)) {
      if (epoch !== connectionEpoch || currentSocket.readyState !== WebSocket.OPEN) return;
      if (performance.now() > deadline) {
        fail("Could not load the held frame; reconnecting.", "connection", true);
        currentSocket.close();
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 5));
    }
    if (epoch !== connectionEpoch || currentSocket.readyState !== WebSocket.OPEN) return;
    if (Object.values(cameras).some((c, i) => c.epoch !== generations[i])) {
      currentSocket.close();
      return;
    }
    currentSocket.send(JSON.stringify({ type: "bootstrap_ack", id: value.id, sequence: value.sequence }));
    return;
  }
  if (value.type === "status") {
    const preserveGeometry = value.preserve_geometry === true && Number.isSafeInteger(value.generation) &&
      value.source === state.status?.source && value.generation === state.status?.generation;
    // Replay restart rewinds acquisition timestamps. Decoder recovery within a
    // receiver generation must retain telemetry ordering and retired sessions.
    const replayRestart = value.replay &&
      (value.source !== state.status?.source || value.generation !== state.status?.generation);
    if (replayRestart) { telemetry.reset(); station.resetMission(); }
    state.status = value;
    if (value.state === "ready") clearError("source");
    state.replay = value.replay;
    state.playing = value.playing;
    $("playback").hidden = !value.replay;
    $("source-type").textContent = value.replay
      ? `Recorded · ${value.source.split("/").pop()}`
      : "Live camera transport";
    $("receiver").textContent = JSON.stringify(value, null, 2);
    if (value.reset) reset({ preserveGeometry });
    if (value.reset_camera) resetCamera(value.reset_camera);
  } else if (value.type === "calibration")
    configureCalibration(value.calibration);
  else if (value.type === "error")
    fail(value.message, value.component || "viewer", value.recoverable === true);
  else if (value.type === "metadata") {
    const record = value.record;
    if (telemetry.accept(record, performance.now()) === false) return;
    state.metadata[record.camera_id || record.type || "latest"] = record;
    if (record.type === "session") {
      const sessionId = record.session_id ?? null;
      if (sessionId !== state.geometrySessionId) {
        state.frames = {};
        state.pair = null;
        // Retire queued frames and asynchronous outputs from the old capture.
        // resetCamera preserves the held textures and their presentation age.
        for (const name of ["A", "B"]) resetCamera(name);
        station.updateViewFrame(displayedFrame());
      }
      state.geometrySessionId = sessionId;
      state.descriptions = {};
      for (const camera of record.cameras || record.hardware?.cameras || [])
        state.descriptions[camera.id] = camera;
    }
    if (
      record.camera_id &&
      ("sensor_crop" in record || "scaler_crop" in record)
    )
      state.frames[record.camera_id] = record;
    station.metadata(record, performance.now());
    validateGeometry();
    $("metadata").textContent = JSON.stringify(state.metadata, null, 2);
  }
}

function connect() {
  const epoch = ++connectionEpoch;
  let chain = Promise.resolve(),
    pending = 0,
    accepting = true;
  socket = new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?role=${station.audience ? "audience" : "operator"}`,
  );
  socket.binaryType = "arraybuffer";
  socket.onopen = () => {
    state.connected = true;
    clearError("connection");
  };
  socket.onmessage = (e) => {
    if (!accepting) return;
    if (++pending > 48) {
      accepting = false;
      fail("Browser receive queue full; reconnecting for fresh keyframes.", "connection", true);
      socket.close();
      return;
    }
    chain = chain
      .then(async () => {
        if (!accepting || epoch !== connectionEpoch) return;
        if (typeof e.data === "string") await message(JSON.parse(e.data), epoch);
        else await accessUnit(e.data, epoch);
      })
      .catch((e) => fail(e.message))
      .finally(() => pending--);
  };
  socket.onclose = () => {
    if (epoch !== connectionEpoch) return;
    accepting = false;
    state.connected = false;
    station.owner(false);
    telemetry.reset({ preserveSessionHistory: true });
    reset();
    setTimeout(connect, 1500);
  };
  socket.onerror = () => fail("Ground connection failed; reconnecting.", "connection", true);
}

function updateFocusControls() {
  const titles = { a: "Camera A", b: "Camera B", sphere: "360° panorama", perspective: "Look around", mask: "Source coverage" };
  $("view-heading").textContent = titles[renderer.mode];
  $("view-description").textContent = renderer.mode === "perspective"
    ? "Drag to turn. Scroll to change the field of view."
    : renderer.mode === "sphere" || renderer.mode === "mask"
      ? "Both cameras projected across the full sphere."
      : "Scroll to magnify. Drag to inspect the lens.";
  const raw = ["a", "b"].includes(renderer.mode);
  $("focus-control").hidden = !raw;
  $("raw-orientation").hidden = !raw;
  updatePerspectiveControls();
  if (raw) {
    const cameraId = renderer.mode.toUpperCase();
    const focus = renderer.focus[cameraId];
    const rotation = renderer.viewerRotation[cameraId];
    $("orientation-value").textContent = `${rotation}°`;
    $("rotate-view").setAttribute("aria-pressed", String(rotation === 180));
    $("rotate-view").setAttribute("aria-label", `Rotate camera ${cameraId} display 180 degrees; currently ${rotation} degrees`);
    $("focus-zoom").value = focus.zoom;
    $("focus-value").textContent = `${focus.zoom.toFixed(1).replace(/\.0$/, "")}×`;
  }
  $("image").classList.toggle("can-pan", renderer.mode === "perspective" || raw && renderer.rawLayout().zoom > 1);
}

function updatePerspectiveControls() {
  $("perspective-controls").hidden = renderer.mode !== "perspective";
  $("perspective-zoom-value").textContent = `${perspectiveZoom(renderer.fov).toFixed(2)}×`;
  $("perspective-fov-value").textContent = `${(renderer.fov * 180 / Math.PI).toFixed(1)}° horizontal FOV`;
  $("perspective-default-status").textContent = perspectiveDefaultStatus;
}

function displayedFrame(now = performance.now()) {
  const raw = ["a", "b"].includes(renderer.mode);
  const camera = raw ? cameras[renderer.mode.toUpperCase()] : null;
  // Arrival describes decoder activity; presentation describes the pixels the
  // operator can see. Held pixels retain their age, but cannot advance telemetry
  // after a decoder/session reset or a withheld unknown frame.
  const pts = raw ? camera.presentationCurrent ? camera.presentedTimestamp : null
    : state.pair ? Math.min(state.pair.a, state.pair.b) : null;
  const at = raw ? camera.presentedAt : state.presentedPair?.at;
  return { pts, now, replay: state.replay, frameAgeMs: at == null ? null : now - at,
    paired: !!state.pair && state.pair.a === state.pair.b };
}

function diagnostics() {
  const now = performance.now();
  $("flight-telemetry").textContent = telemetry.lines(now).join("\n");
  $("connection").textContent = state.connected
    ? `${state.status?.state || "Connected"}${state.replay ? (state.playing ? " · playing" : " · paused") : ""}`
    : "Disconnected";
  const missing = [];
  for (const name of ["A", "B"]) {
    const c = cameras[name],
      stale = !state.replay && now - c.lastArrival > 1000;
    const status = c.heldForMetadata ? "Waiting for camera metadata · image held" : c.lastArrival
      ? stale
        ? "Stale"
        : "Receiving"
      : "Waiting for keyframe";
    if (!c.lastArrival || stale)
      missing.push(`${name}: ${status.toLowerCase()}`);
    $("camera-" + name.toLowerCase()).textContent =
      `${status}${c.size ? " · " + c.size.join(" × ") : ""}\nDisplayed PTS: ${c.presentedTimestamp ?? "—"} µs · decoded: ${c.lastTimestamp ?? "—"} µs\nDecoded: ${c.decoded} · held for colour: ${c.colourHeldFrames} · unmatched: ${c.unmatched}\nDecode queue: ${c.decoder?.decodeQueueSize ?? 0} · waiting pairs: ${c.pending.length}`;
  }
  $("pair").textContent = state.pair
    ? `Presented pairs: ${state.pairs}\nA−B timestamp gap: ${(state.pair.skew / 1000).toFixed(3)} ms\nExposure sync: unverified\nDecoder resets A/B: ${cameras.A.resets}/${cameras.B.resets}`
    : "No matched frame pair\nExposure sync: unverified";
  $("position").textContent = state.pair
    ? `A ${(state.pair.a / 1e6).toFixed(6)} s · B ${(state.pair.b / 1e6).toFixed(6)} s`
    : "No pair yet";
  const panorama = !["a", "b"].includes(renderer.mode);
  let overlay = "";
  if (!state.connected) overlay = "Disconnected — image held";
  else if (Object.values(cameras).some(c => c.heldForMetadata)) overlay = "Waiting for camera metadata · image held";
  else if (missing.length) overlay = missing.join(" · ");
  else if (panorama && !state.pair)
    overlay = "Waiting for a matched frame pair";
  else if (panorama && !state.replay && now - state.pair.at > 1000)
    overlay = "Paired view is stale — image held";
  else if (state.status?.state === "ended") overlay = "End of recording";
  $("overlay").textContent = overlay;
  $("overlay").hidden = !overlay;
  station.update({ ...displayedFrame(now), connected: state.connected, sourceState: state.status?.state });
  const alignment = state.calibration?.rig_alignment_status === "nominal_operator_geometry" ? "Nominal alignment" : "Spherical projection";
  $("view-caption").textContent = panorama
    ? `${renderer.mode === "sphere" ? "360° panorama" : renderer.mode === "mask" ? "Source coverage" : `${perspectiveZoom(renderer.fov).toFixed(2)}× · ${Math.round((renderer.fov * 180) / Math.PI)}° look-around`} · ${alignment} · shutter sync unverified`
    : `Camera ${renderer.mode.toUpperCase()} · display ${renderer.viewerRotation[renderer.mode.toUpperCase()]}° · ${renderer.rawLayout().zoom.toFixed(1).replace(/\.0$/, "")}× focus zoom · ${renderer.rawLayout().zoom > 1 ? "Drag to inspect" : "Scroll to zoom"}`;
}

try {
  if (!("VideoDecoder" in window))
    throw Error(
      "WebCodecs VideoDecoder is unavailable. Open this localhost viewer in a supported desktop Chrome browser.",
    );
  renderer = new Renderer($("image"));
  renderer.fov = perspectiveDefaultFov;
  station = new Station(renderer, {
    frame: displayedFrame,
    send: value => { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value)); },
    changed: updateFocusControls,
    display: () => ({
      focus: renderer.focus,
      rotation: renderer.viewerRotation,
      seam: renderer.seam,
      colour: { requested: state.colourRequested, strength: state.colourStrength },
      maxSkew: pairLimit,
    }),
    applyDisplay: display => {
      renderer.focus = structuredClone(display.focus);
      renderer.viewerRotation = { ...display.rotation };
      renderer.seam = display.seam;
      $("seam").value = display.seam;
      $("max-skew").value = display.maxSkew;
      pairLimit = display.maxSkew;
      state.colourRequested = display.colour.requested;
      state.colourStrength = { ...display.colour.strength };
      applyColourStrengths();
      updateColourControls();
    },
  });
  $("perspective-save-default").onclick = () => {
    if (renderer.mode !== "perspective" || !validPerspectiveFov(renderer.fov)) return;
    perspectiveDefaultFov = renderer.fov;
    try {
      localStorage.setItem(perspectiveDefaultKey, JSON.stringify(perspectiveDefaultFov));
      perspectiveDefaultStatus = "Saved for this browser. Reset view uses this zoom.";
    } catch {
      perspectiveDefaultStatus = "Default set for this page only; browser storage is unavailable.";
    }
    updatePerspectiveControls();
  };
  $("colour-toggle").onclick = () => {
    if (!station.controlling) return;
    state.colourRequested = !state.colourRequested;
    updateColourControls();
    station.viewChanged();
  };
  for (const name of ["A", "B"]) {
    const input = $("colour-strength-" + name.toLowerCase());
    input.oninput = () => {
      const percent = input.valueAsNumber;
      if (station.controlling && !input.disabled && Number.isFinite(percent)) {
        state.colourStrength[name] = Math.max(0, Math.min(1, percent / 100));
        applyColourStrengths();
        station.viewChanged();
      }
      updateColourControls();
    };
  }
  $("view").onchange = () => {
    if (!station.controlling) { $("view").value = renderer.mode; return; }
    station.navigation.stop();
    renderer.mode = $("view").value;
    drag = null;
    updateFocusControls();
    station.viewChanged();
  };
  $("seam").onchange = () => {
    if (!station.controlling) { $("seam").value = renderer.seam; return; }
    renderer.seam = Number($("seam").value);
    station.viewChanged();
  };
  $("max-skew").onchange = () => {
    const value = Number($("max-skew").value);
    if (station.controlling && Number.isFinite(value) && value >= .05 && value <= 100) {
      pairLimit = value;
      station.viewChanged();
    } else $("max-skew").value = pairLimit;
  };
  $("home").onclick = () => {
    if (!station.controlling) return;
    station.navigation.stop();
    renderer.yaw = renderer.pitch = renderer.roll = 0;
    renderer.fov = perspectiveDefaultFov;
    renderer.resetRawView();
    updateFocusControls();
    station.viewChanged();
  };
  $("rotate-view").onclick = () => {
    if (!station.controlling) return;
    drag = null;
    $("image").classList.remove("dragging");
    renderer.rotateRawView();
    updateFocusControls();
    station.viewChanged();
  };
  $("focus-zoom").oninput = () => {
    if (!station.controlling) { updateFocusControls(); return; }
    renderer.setRawZoom(Number($("focus-zoom").value));
    updateFocusControls();
    station.viewChanged();
  };
  updateFocusControls();
  for (const button of document.querySelectorAll("[data-action]"))
    button.onclick = () => {
      if (station.controlling && socket.readyState === WebSocket.OPEN)
        socket.send(
          JSON.stringify({ type: "control", action: button.dataset.action }),
        );
    };
  let drag = null;
  const canvas = $("image");
  canvas.onpointerdown = (e) => {
    if (!station.controlling || e.button !== 0) return;
    station.navigation.stop();
    canvas.focus({ preventScroll: true });
    drag = { x: e.clientX, y: e.clientY, yaw: renderer.yaw, pitch: renderer.pitch, layout: renderer.rawLayout(), rotation: renderer.viewerRotation[renderer.mode.toUpperCase()] || 0 };
    canvas.classList.add("dragging");
    canvas.setPointerCapture(e.pointerId);
  };
  canvas.onpointermove = (e) => {
    if (!station.controlling) { endDrag(); return; }
    if (!drag) return;
    if (["a", "b"].includes(renderer.mode)) {
      renderer.panRaw(rawDragCenter(drag.layout,
        [e.clientX - drag.x, e.clientY - drag.y],
        [canvas.clientWidth, canvas.clientHeight], drag.rotation));
    } else if (renderer.mode === "perspective") {
      renderer.yaw = drag.yaw - (e.clientX - drag.x) * 0.004;
      renderer.pitch = Math.max(-1.56, Math.min(1.56,
        drag.pitch + (e.clientY - drag.y) * 0.004));
    } else return;
    station.viewChanged();
  };
  const endDrag = () => { drag = null; canvas.classList.remove("dragging"); };
  canvas.onpointerup = endDrag;
  canvas.onpointercancel = endDrag;
  canvas.onlostpointercapture = endDrag;
  canvas.addEventListener(
    "wheel",
    (e) => {
      if (!station.controlling) return;
      station.navigation.stop();
      e.preventDefault();
      if (["a", "b"].includes(renderer.mode)) {
        renderer.setRawZoom(renderer.rawLayout().zoom * Math.exp(-e.deltaY * 0.001));
        updateFocusControls();
      } else {
        renderer.fov = Math.max(MIN_PERSPECTIVE_FOV, Math.min(MAX_PERSPECTIVE_FOV, renderer.fov * Math.exp(e.deltaY * 0.001)));
        updatePerspectiveControls();
      }
      station.viewChanged();
    },
    { passive: false },
  );
  canvas.onkeydown = (e) => {
    if (!station.controlling || socket.readyState !== WebSocket.OPEN) return;
    if (e.code === "Space" && state.replay) {
      e.preventDefault();
      socket.send(
        JSON.stringify({
          type: "control",
          action: state.playing ? "pause" : "play",
        }),
      );
    }
    if (e.code === "Period" && state.replay)
      socket.send(JSON.stringify({ type: "control", action: "step" }));
  };
  new ResizeObserver(() => renderer.draw()).observe(canvas);
  setInterval(diagnostics, 200);
  connect();
  // Read-only, compact diagnostics for browser acceptance tests.
  window.pigeonGround = {
    snapshot: () => ({
      connected: state.connected,
      pairs: state.pairs,
      pair: state.pair,
      calibrated: !!renderer.calibration,
      geometry: state.geometry,
      errors: state.error,
      focus: renderer.focus,
      perspective: { fov: renderer.fov, defaultFov: perspectiveDefaultFov, zoom: perspectiveZoom(renderer.fov) },
      navigation: { yaw: renderer.yaw, pitch: renderer.pitch, roll: renderer.roll, mode: renderer.mode },
      pairingGapMs: pairLimit,
      station: { audience: station.audience, controlling: station.controlling,
        horizon: station.horizonStatus,
        mission: station.mission.sample(station.lastUpdate.pts, performance.now(), state.replay) },
      viewerRotation: renderer.viewerRotation,
      colourEnabled: renderer.colourEnabled,
      colourStrength: { ...state.colourStrength },
      colourGains: { A: [...renderer.colour.A.gain], B: [...renderer.colour.B.gain] },
      decoded: { A: cameras.A.decoded, B: cameras.B.decoded },
      pending: { A: cameras.A.pending.length, B: cameras.B.pending.length },
      colourHeldFrames: { A: cameras.A.colourHeldFrames, B: cameras.B.colourHeldFrames },
      presentation: {
        raw: { A: cameras.A.presentedTimestamp, B: cameras.B.presentedTimestamp },
        pair: state.presentedPair,
        frame: displayedFrame(),
        heldForMetadata: { A: cameras.A.heldForMetadata, B: cameras.B.heldForMetadata },
      },
    }),
  };
} catch (error) {
  fail(error.message);
  $("overlay").textContent = "Viewer unavailable";
}
