import { nominalProfile, finOutline, finAngles, ORK, STATIONS } from "./rocket.js";
import { CameraRenderer, PRESETS } from "./engine.js";
import { RigView } from "./rig.js";
import { opticalState, recoveryPose, boosterToWorld, worldToBody, bodyRadius } from "./scene.js";
import { SCENARIO, linkMargin, radialPixels } from "./scenario.js";
const $ = (id) => document.getElementById(id),
  rad = (x) => (x * Math.PI) / 180,
  clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const captureMode = new URLSearchParams(location.search).has("capture");
const video = $("received");
const videoB = document.createElement("video");
videoB.id = "received-b";
videoB.muted = true;
videoB.playsInline = true;
videoB.preload = "auto";
videoB.hidden = true;
document.body.append(videoB);
const videos = [video, videoB];
let decodedPair = null;
const pendingPairs = [new Map(), new Map()];
let sourceNotice = "";
let lastPairAt = 0, lastResyncAt = 0;
const MEDIA_ROOT = new URL("assets/video/", location.href.split("#")[0]);
let renderer, work, rig, flight, manifest, mesh, outline;
let source = "received",
  preset = "900",
  mode = 0,
  earth = true,
  policy = 0,
  yaw = 25,
  pitch = -22,
  fov = 90;
let launchPreview = true;
let activeLook = null;
let time = 0,
  playing = false,
  lastTick = 0,
  uiTick = 0,
  inspectionTime = -1,
  inspectionDirty = true,
  showMask = false;
let currentClip = "",
  offset = 0,
  loadPromise = Promise.resolve(),
  seekTicket = 0,
  loading = false,
  frameToken = 0;
let modelClock = 0,
  inspectionVisible = false;
let frameState,
  staticPlot = null,
  plotSize = "",
  lastSourceFrame = -1,
  handledFrames = 0,
  frameCost = 0,
  latestMeta = null;
const params = {
  diameter: SCENARIO.geometry.diameter_mm,
  stand: SCENARIO.geometry.pupil_standoff_mm,
  height: SCENARIO.geometry.airbrake_offset_mm,
  exposure: SCENARIO.capture.exposure_ms,
  skew: SCENARIO.capture.skew_ms,
  readout: SCENARIO.capture.readout_ms,
};
const diagnostics = {
  decodedTimestamps: [],
  callbackGaps: 0,
  lastPresented: 0,
  resyncs: 0,
};
async function json(path) {
  const r = await fetch(path);
  if (!r.ok) throw Error(`${path}: ${r.status}`);
  return r.json();
}
function error(e) {
  $("error").hidden = false;
  $("error").textContent = e.message || String(e);
  console.error(e);
}
function sample(t) {
  const pos = clamp(t * 30, 0, flight.trajectory.length - 1),
    i = Math.floor(pos),
    a = flight.trajectory[i],
    b = flight.trajectory[Math.min(i + 1, flight.trajectory.length - 1)],
    f = pos - i;
  const row = {};
  for (const k in a)
    row[k] = typeof a[k] === "number" ? a[k] + (b[k] - a[k]) * f : a[k];
  return row;
}
function stateAt(t) {
  return {
    ...opticalState(t, sample(t), flight.summary, params, preset, PRESETS[preset].field, mesh, outline),
    yaw, pitch, fov, earth, policy,
  };
}
function drawMain() {
  if (!frameState) return;
  followLook();
  const s = { ...frameState, yaw, pitch, fov, earth, policy };
  const view = mode;
  renderer.draw(s, {
    mode: view,
    feed: source === "received" ? 2 : 0,
    width: 1280,
  });
  $("screen").style.aspectRatio =
    view === 1 || view === 7
      ? "2"
      : view === 2 || view === 3
        ? String(PRESETS[preset].w / PRESETS[preset].h)
        : "16 / 9";
}
function sourceFrame(t) {
  time = clamp(t, 0, flight.summary.duration_s);
  frameState = stateAt(time);
  renderer.capture(frameState, { native: !playing });
  drawMain();
  lastSourceFrame = Math.floor(time * 30);
  inspectionDirty = true;
}
function bodyDirection() {
  let d = [
    Math.cos(rad(pitch)) * Math.cos(rad(yaw)),
    Math.cos(rad(pitch)) * Math.sin(rad(yaw)),
    Math.sin(rad(pitch)),
  ];
  if (mode === 1 || mode === 7 || mode === 2) d = [1, 0, 0];
  if (mode === 3) d = [-1, 0, 0];
  if (earth && mode !== 2 && mode !== 3) d = worldToBody(d, frameState);
  return d;
}
function markers() {
  const d = bodyDirection(),
    p = PRESETS[preset],
    R = p.circle / p.pitch / 2;
  for (const [id, sign] of [
    ["a", 1],
    ["b", -1],
  ]) {
    const angle = Math.acos(clamp(sign * d[0], -1, 1)),
      len = Math.hypot(d[1], d[2]),
      q = len > 1e-9 ? [(sign * d[1]) / len, d[2] / len] : [0, 0],
      x = 0.5 + q[0] * radialPixels(angle, p) / p.w,
      y = 0.5 - q[1] * radialPixels(angle, p) / p.h;
    const el = $("marker-" + id);
    el.toggleAttribute(
      "hidden",
      playing || angle > rad(p.field / 2) || x < 0 || x > 1 || y < 0 || y > 1,
    );
    const circle = el.querySelector("circle");
    circle.setAttribute("cx", x * 100);
    circle.setAttribute("cy", y * 100);
  }
}
function inspect() {
  if (captureMode || !frameState) return;
  const state = { ...stateAt(time), earth: false };
  const encoded = source === "received" && decodedPair;
  if (encoded) work.uploadPair(decodedPair.a, decodedPair.b, decodedPair.token);
  else work.capture(state, { native: !playing });
  const feed = encoded ? 2 : 0;
  work.copyTo($("raw-a"), state, { mode: 2, feed, width: 512, earth: false });
  work.copyTo($("raw-b"), state, { mode: 3, feed, width: 512, earth: false });
  work.copyTo($("stitched"), state, { mode: showMask ? 7 : 1, feed, width: 1024, earth: false, policy });
  inspectionTime = time;
  inspectionDirty = false;
  const p = PRESETS[preset];
  $("capture-detail").textContent = `${encoded ? "Decoded cameras" : "Ideal camera model"} · T+${time.toFixed(2)} s · ${p.w} × ${p.h} crop`;
  for (const id of ["a-spec", "b-spec"]) $(id).textContent = `${p.w} × ${p.h}`;
  $("raw-a").style.aspectRatio = String(p.w / p.h);
  $("raw-b").style.aspectRatio = String(p.w / p.h);
  markers();
}
function syncUi(force = false) {
  if (!frameState) return;
  const r = sample(time),
    p = PRESETS[preset];
  $("time-label").textContent = `T + ${time.toFixed(2)} s`;
  $("timeline").value = time;
  const phaseButtons = [...$("phases").children];
  phaseButtons.forEach((b,i) => b.classList.toggle("active", time >= Number(b.dataset.time) && (!phaseButtons[i+1] || time < Number(phaseButtons[i+1].dataset.time))));
  $("phase-label").textContent =
    time < (flight.summary.ignition_time_s ?? 3)
      ? "ON THE PAD"
      : time < (flight.summary.burnout_time_s ?? 9)
        ? "POWERED ASCENT"
        : time < flight.summary.apogee_time_s
          ? "COAST"
          : time < flight.summary.deployment_time_s
            ? "APOGEE"
            : "RECOVERY";
  $("flight-alt").innerHTML =
    Math.round(r.altitude_m).toLocaleString() + " <small>m</small>";
  $("flight-speed").innerHTML =
    r.velocity_mps.toFixed(0) + " <small>m/s</small>";
  $("flight-range").innerHTML =
    (r.slant_range_m / 1000).toFixed(2) + " <small>km</small>";
  $("flight-margin").innerHTML =
    linkMargin(r.slant_range_m).toFixed(1) + " <small>dB</small>";
  $("play").textContent = playing ? "Ⅱ Pause" : launchPreview ? "▶ Launch" : "▶ Play";
  $("play").setAttribute(
    "aria-label",
    playing ? "Pause launch" : "Play launch",
  );
  const names = {
    0: "Ground view",
    1: "Full direction map",
    2: "Camera A · fisheye",
    3: "Camera B · fisheye",
    4: "Camera A · perspective",
    5: "Camera B · perspective",
    7: "Source coverage map",
  };
  $("view-title").textContent = source === "received" && mode === 0 ? "Ground view" : names[mode];
  $("source-badge").textContent = source === "received" ? "Encoded simulation" : "Ideal model";
  $("stage-label").textContent = source === "received" ? "2 × 1552² / 30 fps / 4 Mb/s each" : "Synthetic scene / no codec";
  $("view-reference").textContent = mode === 2 || mode === 3 ? "CAMERA CROP" : earth ? "EARTH FIXED" : "BODY FIXED";
  $("view-angle").textContent = mode === 2 || mode === 3 ? (preset === "900" ? "197° MIN. MODELED FOV" : p.field + "° FISHEYE") : mode === 1 || mode === 7 ? "360° × 180°" : Math.round(fov) + "° VIEW";
  $("view-note").textContent = source === "received" ? "Two decoded camera streams, stitched here. Drag to look around." : sourceNotice || "Synthetic optics and geometry. Pause for full crop resolution.";
  for (const b of document.querySelectorAll("[data-source]")) {
    b.classList.toggle("active", b.dataset.source === source);
    b.disabled = b.dataset.source === "received" && (!manifest || $("scenario").value !== "launch.json" || preset !== "900");
  }
  $("seam-policy").value = String(policy);
  $("earth").disabled = mode === 2 || mode === 3;
  $("earth").checked = earth;
  $("view-mode").value = String(mode);
  for (const b of document.querySelectorAll("[data-look]")) b.disabled = false;
  $("inspection-status").textContent = playing && !$("live-inspection").checked ? `Held at T+${inspectionTime.toFixed(2)} s` : source === "received" ? "Same instant, opposite sides." : "Camera crops before encoding.";
  $("rig-description").textContent = `Ø ${params.diameter} mm · lens pupils ${params.stand} mm outside the skin`;
  $("seam-description").textContent = ["Selects each camera's outward half.", "Blends the overlap. Nearby surfaces can ghost.", "Uses A wherever it has coverage.", "Uses B wherever it has coverage."][policy];
  const detail = Math.round(2 * radialPixels(Math.PI / 4, p));
  $("detail-head").textContent = `${detail} samples across a centred 90° view`;
  $("detail-copy").textContent = "Geometric sampling estimate. Edge sharpness, noise and calibration need real footage.";
  const rconf = SCENARIO.radio;
  const allowance = rconf.symbols_per_second * rconf.user_bits_per_frame / rconf.symbols_per_frame * rconf.capacity_fraction / 1e6;
  $("radio-facts").innerHTML = [
    ["Camera crops", "2 × 1552 × 1552"], ["Capture / video", "30 fps / 4 Mb/s each"],
    ["Transport target", "9.00 Mb/s"], ["RF allowance", `${allowance.toFixed(2)} Mb/s`],
    ["PA average / frequency", "0.5 W / 1.28 GHz"], ["Antenna gain TX / RX", "0 / 15 dBi assumed"],
    ["Loss / NF / reserve", "7 / 3 / 10 dB assumed"], ["Receiver C/N threshold", "8 dB assumed"],
  ].map(([a,b]) => `<div><dt>${a}</dt><dd>${b}</dd></div>`).join("");
  $("codec-status").textContent = manifest ? `${manifest.frames} frames per camera · ${manifest.transport.measured_ts_mbps.toFixed(3)} Mb/s measured transport. ${manifest.transport.mux_delay_s.toFixed(2)} s mux buffer. RF packet loss is not simulated.` : "No matching encoded clip. Showing the ideal model.";
  const qa = video.getVideoPlaybackQuality?.(), qb = videoB.getVideoPlaybackQuality?.();
  $("playback-stats").textContent = source === "received" ? `Browser drops A/B: ${qa?.droppedVideoFrames ?? 0}/${qb?.droppedVideoFrames ?? 0} · resyncs: ${diagnostics.resyncs} · frame ${decodedPair?.index ?? 0}` : "Model: 30 fps source cadence; pointer panning runs independently.";

  for (const k of ["stand", "height", "exposure", "skew", "readout"]) {
    $(k).value = params[k];
    $(k + "-out").textContent =
      params[k] + (["stand", "height"].includes(k) ? " mm" : " ms");
  }
  $("readout").disabled = p.global;
  const d = bodyDirection();
  rig.draw({
    ...frameState,
    field: p.field,
    bodyYaw: Math.atan2(d[1], d[0]),
    bodyPitch: Math.asin(clamp(d[2], -1, 1)),
  });
  markers();
  plot();
}
function plot() {
  const c = $("flightplot"),
    w = Math.round(c.clientWidth || 220),
    h = 60,
    key = w + "/" + flight.summary.duration_s;
  if (plotSize !== key) {
    plotSize = key;
    staticPlot = document.createElement("canvas");
    staticPlot.width = w * 2;
    staticPlot.height = h * 2;
    const ctx = staticPlot.getContext("2d");
    ctx.scale(2, 2);
    ctx.strokeStyle = "#aabc9f";
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    flight.trajectory.forEach((r, i) => {
      if (i % 3) return;
      const x = 3 + ((w - 6) * r.t_s) / flight.summary.duration_s,
        y =
          h - 5 - (r.altitude_m / flight.summary.apogee_altitude_m) * (h - 12);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  }
  if (c.width !== w * 2) c.width = w * 2;
  if (c.height !== h * 2) c.height = h * 2;
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  ctx.drawImage(staticPlot, 0, 0);
  ctx.fillStyle = "#41674e";
  ctx.beginPath();
  ctx.arc(
    (3 + ((w - 6) * time) / flight.summary.duration_s) * 2,
    (h -
      5 -
      (sample(time).altitude_m / flight.summary.apogee_altitude_m) * (h - 12)) *
      2,
    4,
    0,
    Math.PI * 2,
  );
  ctx.fill();
}
function resetParams() {
  Object.assign(params, {
    diameter: SCENARIO.geometry.diameter_mm,
    stand: 8,
    height: 150,
    exposure: 0.5,
    skew: 0,
    readout: preset === "900" ? 0 : 10,
  });
}
function pause() {
  ++seekTicket;
  loading = false;
  clearPairs();
  playing = false;
  videos.forEach((v) => v.pause());
  lastTick = 0;
  inspectionDirty = true;
}
function clearPairs() {
  for (const map of pendingPairs) { for (const bmp of map.values()) bmp.close(); map.clear(); }
}
function showPair(a, b, index, token) {
  if (decodedPair) { decodedPair.a.close(); decodedPair.b.close(); }
  decodedPair = { a, b, index, token };
  lastPairAt = performance.now();
  time = clamp(index / SCENARIO.fps + offset, 0, flight.summary.duration_s);
  frameState = stateAt(time);
  renderer.uploadPair(a, b, token);
  drawMain();
  inspectionDirty = true;
  window.presentedFrameTime = time;
  window.flightTime = time;
}
async function loadVideo(t, resume = false) {
  const ticket = ++seekTicket;
  clearPairs();
  if (!manifest || preset !== "900" || $("scenario").value !== "launch.json") {
    source = "model"; sourceFrame(t); return;
  }
  loading = true;
  offset = manifest.start_s ?? 0;
  const revision = manifest.fingerprint.scene_sha256.slice(0, 12) + "-" + manifest.fingerprint.config_sha256.slice(0, 12);
  const paths = [manifest.cameras.a.mp4, manifest.cameras.b.mp4].map(p => {
    const url = new URL(p, MEDIA_ROOT);
    url.searchParams.set("v", revision);
    return url.href;
  });
  const key = paths.join("|");
  if (currentClip !== key) {
    currentClip = key;
    loadPromise = Promise.all(videos.map((v,i) => new Promise((resolve,reject) => {
      const done = () => { v.removeEventListener("error", bad); resolve(); };
      const bad = () => { v.removeEventListener("loadedmetadata", done); reject(Error("Camera video could not be loaded")); };
      v.addEventListener("loadedmetadata", done, { once:true });
      v.addEventListener("error", bad, { once:true });
      v.src = paths[i]; v.load();
    })));
  }
  try {
    await loadPromise;
    if (ticket !== seekTicket) return;
    const index = clamp(Math.floor((t-offset) * SCENARIO.fps + 1e-5), 0, manifest.frames-1);
    const target = index / SCENARIO.fps + .001;
    await Promise.all(videos.map(v => new Promise(resolve => {
      if (Math.abs(v.currentTime-target) < .0001 && v.readyState >= 2) return resolve();
      v.addEventListener("seeked", resolve, { once:true }); v.currentTime=target;
    })));
    if (ticket !== seekTicket) return;
    const [a,b] = await Promise.all(videos.map(v => createImageBitmap(v)));
    if (ticket !== seekTicket) { a.close(); b.close(); return; }
    loading = false;
    showPair(a,b,index,"seek" + ++frameToken);
    if (resume) { playing=true; videos.forEach(v => v.playbackRate=Number($("rate").value)); await Promise.all(videos.map(v => v.play())); }
  } catch (e) {
    if (ticket !== seekTicket) return;
    loading=false; pause(); source="model"; sourceNotice="Encoded clips unavailable. Showing the ideal model."; sourceFrame(t); syncUi();
    console.warn(e.message);
  }
}
async function seek(t) {
  launchPreview = false;
  pause();
  t = clamp(t, 0, flight.summary.duration_s);
  if (source === "received") await loadVideo(t);
  else sourceFrame(t);
  if (!playing) inspect();
  syncUi(true);
}
async function setSource(s) {
  launchPreview = false;
  pause();
  source = s;
  mode = 0;
  if (s === "received") {
    resetParams();
    policy = 0;
    await loadVideo(time);
  } else sourceFrame(time);
  inspect();
  syncUi(true);
}
async function setMode(value) {
  activeLook = null;
  pause();
  mode = Number(value);

  if (mode === 2 || mode === 3 || mode === 4 || mode === 5) earth = false;
  if (mode === 4 || mode === 5) {
    yaw = mode === 4 ? 180 : 0;
    pitch = -85;
    fov = 65;
  }
  if (source === "model") sourceFrame(time);
  else drawMain();
  inspect();
  syncUi(true);
}
async function loadFlight(file) {
  flight = await json("assets/" + file);
  window.flightData = flight;
  time = 0;
  $("timeline").max = flight.summary.duration_s;
  plotSize = "";
  $("phases").replaceChildren();
  for (const [name, t] of [
    ["Pad", 0],
    ["Ignition", flight.summary.ignition_time_s ?? 3],
    ["Burnout", flight.summary.burnout_time_s ?? 9],
    ["Airbrakes", 12],
    ["Apogee", flight.summary.apogee_time_s],
    ["Separation", flight.summary.deployment_time_s],
  ]) {
    const b = document.createElement("button");
    b.textContent = name;
    b.title = `T + ${t.toFixed(1)} s`;
    b.dataset.time = t;
    b.onclick = () => seek(t).catch(error);
    $("phases").append(b);
  }
  frameState = stateAt(0);
}
function decoded(which, now, meta) {
  videos[which].requestVideoFrameCallback((n,m) => decoded(which,n,m));
  if (source !== "received" || loading || !playing || videos[which].seeking) return;
  const ticket=seekTicket, index=Math.round(meta.mediaTime * SCENARIO.fps);
  if (decodedPair && index <= decodedPair.index) return;
  createImageBitmap(videos[which]).then(bmp => {
    if (ticket!==seekTicket || source!=="received" || loading || !playing || (decodedPair && index<=decodedPair.index)) { bmp.close(); return; }
    const map=pendingPairs[which]; map.get(index)?.close(); map.set(index,bmp);
    if (pendingPairs[0].has(index) && pendingPairs[1].has(index)) {
      const start=performance.now(), a=pendingPairs[0].get(index), b=pendingPairs[1].get(index);
      pendingPairs[0].delete(index); pendingPairs[1].delete(index);
      showPair(a,b,index,"pair" + ++frameToken);
      handledFrames++; frameCost=performance.now()-start;
      diagnostics.decodedTimestamps.push([index / SCENARIO.fps + offset,frameState.time]);
      if (diagnostics.decodedTimestamps.length>120) diagnostics.decodedTimestamps.shift();
    }
    for (const pending of pendingPairs) for (const [idx,img] of pending) {
      if (idx < index-6 || (decodedPair && idx<=decodedPair.index)) { img.close(); pending.delete(idx); }
    }
  }).catch(e => { pause(); error(e); });
}
function tick(now) {
  requestAnimationFrame(tick);
  if (captureMode || !flight) return;
  if (playing && source === "received" && !loading && !document.hidden && now-lastResyncAt > 1500 &&
      (Math.abs(video.currentTime-videoB.currentTime) > .20 || now-lastPairAt > 900)) {
    lastResyncAt = now;
    diagnostics.resyncs++;
    pause();
    loadVideo(time, true).catch(error);
  }
  if (playing && source === "model") {
    if (lastTick) {
      modelClock = clamp(
        modelClock + (now - lastTick) * 0.001 * Number($("rate").value),
        0,
        flight.summary.duration_s,
      );
      if (Math.floor(modelClock * 30) !== lastSourceFrame)
        sourceFrame(Math.floor(modelClock * 30) / 30);
    }
    if (time >= flight.summary.duration_s - 0.034) pause();
  }
  lastTick = now;
  if (now - uiTick > 200) {
    uiTick = now;
    if (
      !loading &&
      ((!playing && inspectionDirty) ||
        (playing && inspectionVisible && $("live-inspection").checked))
    )
      inspect();
    syncUi();
  }
}
function followLook() {
  if (!frameState || !["airbrakes", "canopy"].includes(activeLook)) return;
  const pose = recoveryPose(frameState);
  if (!pose.separated) {
    [yaw, pitch, earth] = activeLook === "airbrakes" ? [0,-72,false] : [0,85,true];
    return;
  }
  const p = activeLook === "canopy" ? pose.canopy
    : boosterToWorld([bodyRadius(frameState),0,-frameState.height/1000], pose.booster);
  yaw = Math.atan2(p[1],p[0])*180/Math.PI;
  pitch = Math.atan2(p[2],Math.hypot(p[0],p[1]))*180/Math.PI;
  earth = true;
}
function look(name) {
  const views = {
    horizon: [0, 0, true],
    airbrakes: [0, -72, false],
    nadir: [0, -89.9, true],
    canopy: [0, 85, true],
    seam: [90, -65, false],
  };
  activeLook = name;
  [yaw, pitch, earth] = views[name];
  mode = 0;
  drawMain();
  syncUi(true);
}
function bind() {
  $("restart").onclick = () => seek(0).catch(error);
  for (const b of document.querySelectorAll("[data-source]"))
    b.onclick = () => setSource(b.dataset.source).catch(error);
  for (const b of document.querySelectorAll("[data-look]"))
    b.onclick = () => look(b.dataset.look);
  $("view-mode").onchange = () => setMode($("view-mode").value).catch(error);
  for (const b of document.querySelectorAll("[data-camera]"))
    b.onclick = () =>
      setMode(
        b.dataset.action === "raw"
          ? b.dataset.camera === "A"
            ? 2
            : 3
          : b.dataset.camera === "A"
            ? 4
            : 5,
      ).catch(error);
  $("earth").onchange = () => {
    activeLook = null;
    earth = $("earth").checked;
    drawMain();
    syncUi(true);
  };
  $("play").onclick = async () => {
    if (loading) return;
    try {
      if (playing) {
        pause();
        if (source === "model") sourceFrame(time);
        inspect();
      } else {
        if (launchPreview) await seek(0);
        const clipEnded = source === "received" && (videos.some(v => v.ended) || time >= offset + (manifest.frames - 1) / SCENARIO.fps - 1e-6);
        if (clipEnded || time >= flight.summary.duration_s - 0.05) await seek(source === "received" ? offset : 0);
        playing = true;
        lastPairAt = performance.now();
        lastTick = 0;
        modelClock = time;
        if (source === "received") {
          videos.forEach(v => v.playbackRate = Number($("rate").value));
          await Promise.all(videos.map(v => v.play()));
        }
      }
      syncUi(true);
    } catch (e) {
      pause();
      error(e);
    }
  };
  $("rate").onchange = () => videos.forEach(v => v.playbackRate = Number($("rate").value));
  $("timeline").oninput = () => seek(Number($("timeline").value)).catch(error);
  $("scenario").onchange = async () => {
    pause();
    source = "model";
    await loadFlight($("scenario").value);
    sourceFrame(0);
    inspect();
    syncUi(true);
  };
  $("preset").onchange = async () => {
    pause();
    preset = $("preset").value;
    resetParams();
    if (preset !== "900") source = "model";
    if (source === "received") await loadVideo(time);
    else sourceFrame(time);
    inspectionDirty = true;
    inspect();
    syncUi(true);
  };
  $("seam-policy").onchange = () => {
    policy = Number($("seam-policy").value);
    frameState = stateAt(time);
    drawMain();
    inspect();
    syncUi(true);
  };
  $("toggle-mask").onclick = () => {
    showMask = !showMask;
    $("toggle-mask").textContent = showMask ? "Show image" : "Show source map";
    inspect();
  };
  $("ring-view").onclick = () => {
    rig.full = false;
    $("ring-view").classList.add("active");
    $("full-rig").classList.remove("active");
    syncUi(true);
  };
  $("full-rig").onclick = () => {
    rig.full = true;
    $("full-rig").classList.add("active");
    $("ring-view").classList.remove("active");
    syncUi(true);
  };
  $("bubbles").onchange = () => {
    rig.bubbles = $("bubbles").checked;
    syncUi(true);
  };
  for (const k of ["stand", "height", "exposure", "skew", "readout"])
    $(k).oninput = () => {
      pause();
      source = "model";
      params[k] = Number($(k).value);
      sourceFrame(time);
      inspect();
      syncUi(true);
    };
  $("reset-geometry").onclick = () => {
    pause();
    source = "model";
    resetParams();
    sourceFrame(time);
    inspect();
    syncUi(true);
  };
  $("snapshot").onclick = () => {
    drawMain();
    const a = document.createElement("a");
    a.href = $("screen").toDataURL();
    a.download = `pigeon-vision-${source}-${time.toFixed(2)}s.png`;
    a.click();
  };
  $("fullscreen").onclick = () => {
    $("screen").parentElement.requestFullscreen?.().catch(error);
  };
  let drag = null,
    pending = false;
  const redraw = () => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      drawMain();
      markers();
    });
  };
  $("screen").onpointerdown = (e) => {
    if (![0, 4, 5].includes(mode)) return;
    activeLook = null;
    drag = [e.clientX, e.clientY, yaw, pitch];
    $("screen").setPointerCapture(e.pointerId);
  };
  $("screen").onpointermove = (e) => {
    if (!drag) return;
    yaw = ((drag[2] - (e.clientX - drag[0]) * 0.17 + 540) % 360) - 180;
    pitch = clamp(drag[3] + (e.clientY - drag[1]) * 0.17, -89.9, 89.9);
    redraw();
  };
  for (const event of ["pointerup", "pointercancel"])
    $("screen").addEventListener(event, () => {
      drag = null;
      syncUi(true);
    });
  $("screen").addEventListener(
    "wheel",
    (e) => {
      if (![0, 4, 5].includes(mode)) return;
      e.preventDefault();
      fov = clamp(fov + e.deltaY * 0.04, 35, 115);
      redraw();
    },
    { passive: false },
  );
  $("screen").onkeydown = (e) => {
    if (e.key === "," || e.key === ".") { e.preventDefault(); seek(time + (e.key === "." ? 1 : -1) / SCENARIO.fps).catch(error); return; }
    if (e.code === "Space") { e.preventDefault(); $("play").click(); return; }
    const d = {
      ArrowLeft: [-3, 0],
      ArrowRight: [3, 0],
      ArrowUp: [0, 3],
      ArrowDown: [0, -3],
    }[e.key];
    if (!d) return;
    e.preventDefault();
    activeLook = null;
    yaw += d[0];
    pitch = clamp(pitch + d[1], -89.9, 89.9);
    redraw();
  };
  videos.forEach(v => v.onended = () => {
    if (source === "received" && manifest && !loading)
      seek(offset + (manifest.frames - 1) / SCENARIO.fps).catch(error);
  });
  new IntersectionObserver(
    (entries) => {
      inspectionVisible = entries[0].isIntersecting;
    },
    { threshold: 0.1 },
  ).observe(document.querySelector(".pipeline"));
  videos.forEach((v,i) => v.requestVideoFrameCallback?.((n,m) => decoded(i,n,m)));
}
function drawRocketKey() {
  const scale = 130 / (STATIONS.tip - STATIONS.tail);
  const point = (r,z) => `${(44+r*scale).toFixed(3)} ${(7+(STATIONS.tip-z)*scale).toFixed(3)}`;
  const profile = nominalProfile();
  const body = [...profile.map(([z,r])=>point(r,z)), ...profile.toReversed().map(([z,r])=>point(-r,z))];
  $("rocket-profile").setAttribute("d", "M"+body.join(" L")+" Z");
  $("rocket-fins").setAttribute("d", finAngles().map(a=>"M"+finOutline(ORK.radius).map(([r,z])=>point(r*Math.cos(a),z)).join(" L")+" Z").join(" "));
}
try {
  drawRocketKey();
  renderer = new CameraRenderer($("screen"));
  work = new CameraRenderer($("work-canvas"));
  rig = new RigView($("rig"));
  const cad = await json("assets/airbrakes.json");
  outline = cad.leaf.outline_xy_mm.map((p) => p.map((v) => v / 1000));
  mesh = new Float32Array(outline.flat());
  await loadFlight("launch.json");
  try {
    manifest = await json(new URL("manifest-v3.json", MEDIA_ROOT));
    if (manifest.pipeline_version !== 3 || manifest.camera_scope !== "two-fisheyes-ground-stitch" || manifest.parameters?.capture?.id !== SCENARIO.id || manifest.crop_px !== SCENARIO.camera.crop_px) throw Error("Clip settings differ from the current scenario");
    for (const [path,expected] of Object.entries(manifest.fingerprint.inputs)) {
      // Generator keys are relative to the visual site, never the workstation.
      if (path.startsWith("/") || path.includes("..")) throw Error("Invalid fingerprint path");
      const response=await fetch(new URL(path, import.meta.url));
      if (!response.ok) throw Error("Missing clip source: "+path);
      const hash=await crypto.subtle.digest("SHA-256",await response.arrayBuffer());
      const actual=Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,"0")).join("");
      if (actual!==expected) throw Error("Scene changed; regenerate camera clips");
    }
    if (!video.requestVideoFrameCallback) throw Error("Synchronized video requires video frame callbacks");
  } catch (e) {
    manifest=null; source="model"; sourceNotice="Encoded clips are not available for these settings. Showing the ideal model.";
    console.info(e.message);
  }
  bind();
  let lastCaptureKey="";
  window.renderSequenceMetadata = () => SCENARIO;
  window.renderSequenceFrame = ({t,preset:key="900",width=1552,kind="camera-a"}) => {
    preset=key; resetParams(); source="model"; earth=false;
    mode=kind === "camera-a" ? 2 : kind === "camera-b" ? 3 : 1;
    time=t; frameState=stateAt(t);
    const captureKey=key+"/"+t;
    if (captureKey!==lastCaptureKey) { renderer.capture(frameState,{native:true}); lastCaptureKey=captureKey; }
    renderer.draw(frameState,{mode,feed:0,width,earth:false,policy:0});
    return $("screen").toDataURL("image/png").split(",")[1];
  };

  window.pigeon = {
    diagnostics,
    get state() {
      return {
        source,
        preset,
        time,
        mode,
        earth,
        yaw, pitch, activeLook,
        playing,
        policy,
        params: { ...params },
        inspectionTime,
        frameState,
        pairedFrame: decodedPair?.index,
        loading,
        manifest,
        metrics: renderer.metrics,
      };
    },
    seek,
    setSource,
    setMode,
    drawMain,
    inspect,
    get renderer() {
      return renderer;
    },
  };
  if (captureMode) {
    source = "model";
    sourceFrame(0);
  } else {
    if (source === "received") await loadVideo(15);
    else sourceFrame(15);
    inspect();
    syncUi(true);
    requestAnimationFrame(tick);
  }
  window.simReady = true;
} catch (e) {
  error(e);
}
