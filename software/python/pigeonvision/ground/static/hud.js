// Presentation only. MissionTimeline supplies measurements at the displayed PTS.
// The HUD never interpolates telemetry, infers events, or moves with the camera.
const TAU = 2 * Math.PI;
const MAX_PLOT_VERTICES = 200;
const PLOT_GAP_US = 250000;
const finite = Number.isFinite;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const wrap = (yaw) => ((yaw + Math.PI) % TAU + TAU) % TAU - Math.PI;
const integer = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const EVENT_NAMES = ["LAUNCH", "APOGEE", "DEPLOYMENT", "LANDED"];

function clock(seconds) {
  if (!finite(seconds) || seconds < 0) return "—";
  const tenths = Math.floor(seconds * 10 + 1e-6);
  const hours = Math.floor(tenths / 36000);
  const minutes = Math.floor(tenths / 600) % 60;
  const remainder = Math.floor(tenths / 10) % 60;
  const fraction = tenths % 10;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(remainder).padStart(2, "0");
  return `${hours ? `${hours}:${mm}` : mm}:${ss}.${fraction}`;
}

// Largest-triangle sampling preserves turns without adding synthetic points.
function sampleSegment(points, limit) {
  if (points.length <= limit) return points;
  if (limit === 1) return [points.at(-1)];
  if (limit === 2) return [points[0], points.at(-1)];
  const result = [points[0]];
  const every = (points.length - 2) / (limit - 2);
  let anchor = 0;
  for (let bucket = 0; bucket < limit - 2; bucket++) {
    const nextStart = Math.floor((bucket + 1) * every) + 1;
    const nextEnd = Math.min(points.length, Math.floor((bucket + 2) * every) + 1);
    let averageT = 0, averageH = 0;
    for (let i = nextStart; i < nextEnd; i++) {
      averageT += points[i].pts;
      averageH += points[i].altitude;
    }
    const count = Math.max(1, nextEnd - nextStart);
    averageT /= count;
    averageH /= count;
    const start = Math.floor(bucket * every) + 1;
    const end = Math.min(points.length - 1, Math.floor((bucket + 1) * every) + 1);
    const a = points[anchor];
    let bestArea = -1, best = start;
    for (let i = start; i < end; i++) {
      const p = points[i];
      const area = Math.abs((a.pts - averageT) * (p.altitude - a.altitude) -
        (a.pts - p.pts) * (averageH - a.altitude));
      if (area > bestArea) { bestArea = area; best = i; }
    }
    result.push(points[best]);
    anchor = best;
  }
  result.push(points.at(-1));
  return result;
}

function plotSegments(rows, pts) {
  const segments = [];
  let segment = null, previous = null;
  for (const row of rows) {
    if (!row || !finite(row.pts) || !finite(row.altitude) || finite(pts) && row.pts > pts) {
      segment = previous = null;
      continue;
    }
    if (!previous || row.pts <= previous.pts || row.pts - previous.pts > PLOT_GAP_US) {
      segment = [];
      segments.push(segment);
    }
    segment.push(row);
    previous = row;
  }
  return segments;
}

function boundedSegments(segments) {
  // Reserve endpoints so decimation cannot reconnect across a missing sample.
  // If there are more disconnected segments than fit, retain the newest ones.
  const selected = [];
  let available = MAX_PLOT_VERTICES;
  for (let i = segments.length - 1; i >= 0; i--) {
    const minimum = Math.min(2, segments[i].length);
    if (minimum > available) break;
    selected.unshift({ points: segments[i], limit: minimum });
    available -= minimum;
  }
  const capacity = selected.reduce((sum, s) => sum + s.points.length - s.limit, 0);
  if (capacity && available) {
    const budget = available;
    for (const s of selected) {
      const extra = Math.min(s.points.length - s.limit,
        Math.floor(budget * (s.points.length - s.limit) / capacity));
      s.limit += extra;
      available -= extra;
    }
    while (available && selected.some(s => s.limit < s.points.length)) {
      for (const s of selected) {
        if (!available) break;
        if (s.limit < s.points.length) { s.limit++; available--; }
      }
    }
  }
  return selected.map(s => sampleSegment(s.points, s.limit));
}

export class MissionHUD {
  constructor() {
    this.nodes = new Map();
    for (const id of ["hud-altitude", "hud-speed", "hud-clock", "hud-phase", "hud-pressure-status",
      "hud-feed-mode", "hud-simulated", "hud-link-state", "hud-bearing", "hud-bearing-window",
      "hud-view-direction", "hud-bearing-left", "hud-bearing-center", "hud-bearing-right",
      "hud-bearing-wrap", "hud-bearing-marker", "hud-direction", "hud-fov", "hud-pitch",
      "hud-pitch-marker", "hud-altitude-plot", "hud-altitude-dot", "hud-plot-title"])
      this.nodes.set(id, document.getElementById(id));
    this.eventNodes = EVENT_NAMES.map(name => ({ name,
      node: document.querySelector(`.event-rail [data-event="${name}"]`), value: null }));
    this.plotSignature = null;
    this.plotAt = -Infinity;
    this.lastDirection = null;
  }

  text(id, value) {
    const node = this.nodes.get(id);
    if (node && node.textContent !== value) node.textContent = value;
  }

  hidden(id, hidden) {
    const node = this.nodes.get(id);
    if (node && node.hasAttribute("hidden") !== hidden) node.toggleAttribute("hidden", hidden);
  }

  update({ mission = {}, replay = false, connected = false, frameAgeMs = null,
    mode = "a", yaw = 0, pitch = 0, fov = null, controlling = false, sourceState = null, horizon = {} } = {}) {
    mission ||= {};
    const fresh = mission.pressureFresh === true;
    const altitude = fresh && finite(mission.altitude) ? Math.round(mission.altitude) : null;
    const speed = fresh && finite(mission.speed) ? mission.speed : null;
    this.text("hud-altitude", altitude === null ? "—" : integer.format(Object.is(altitude, -0) ? 0 : altitude));
    this.text("hud-speed", speed === null ? "—" : (Math.abs(speed) < .05 ? 0 : speed).toFixed(1));
    this.text("hud-clock", clock(mission.elapsed));
    this.text("hud-phase", String(mission.phase || "UNKNOWN").replace(/_/g, " ").toUpperCase().slice(0, 32));
    this.text("hud-pressure-status", !mission.reference ? "NO PAD REFERENCE"
      : !fresh ? "PRESSURE STALE" : altitude === null ? "ALTITUDE UNAVAILABLE" : "");
    this.text("hud-feed-mode", replay ? "REPLAY" : "LIVE");
    this.hidden("hud-simulated", mission.simulated !== true);
    const source = typeof sourceState === "string" ? sourceState : sourceState?.state;
    this.text("hud-link-state", !connected ? "DISCONNECTED"
      : source === "ended" ? "END OF RECORDING"
      : source === "error" ? "SOURCE ERROR"
      : source === "disconnected" ? finite(frameAgeMs) ? "IMAGE HELD" : "SOURCE OFFLINE"
      : source === "waiting" ? "VIDEO WAITING"
      : !finite(frameAgeMs) ? "VIDEO WAITING"
      : !replay && frameAgeMs > 1000 ? "IMAGE HELD" : "RECEIVING");
    const controlState = String(controlling === true);
    if (document.body.dataset.controlling !== controlState) document.body.dataset.controlling = controlState;
    this.direction(mode, yaw, pitch, fov, horizon);
    this.events(mission.events || [], mission.pts);
    this.plot(mission.plot || [], mission.pts);
  }

  direction(mode, yaw, pitch, fov, horizon = {}) {
    const panoramic = mode === "sphere" || mode === "mask";
    const raw = mode === "a" || mode === "b";
    const level = mode === "perspective" && horizon.requested === true;
    const locked = level && horizon.active === true;
    const known = raw || finite(yaw) && finite(pitch);
    const bearing = raw ? mode === "b" ? -Math.PI : 0 : known ? wrap(yaw) : 0;
    const elevation = raw ? 0 : known ? clamp(pitch, -Math.PI / 2, Math.PI / 2) : 0;
    const degrees = Math.round(bearing * 180 / Math.PI);
    const pitchDegrees = Math.round(elevation * 180 / Math.PI);
    const field = !raw && finite(fov) && fov > 0 ? clamp(fov / TAU * 100, 0, 100) : 0;
    const position = (bearing / TAU + .5) * 100;
    const signature = [mode, known, level, locked, position.toFixed(3), elevation.toFixed(3), field.toFixed(3)].join(":");
    if (signature === this.lastDirection) return;
    this.lastDirection = signature;
    const direction = panoramic ? "FULL SPHERE" : !known ? "—"
      : level ? `${locked ? "LEVEL" : "LEVEL UNAVAILABLE"} / ${degrees}°`
      : raw ? `${mode.toUpperCase()} / ${mode === "b" ? 180 : 0}°`
      : `${degrees === 0 ? "A / " : Math.abs(degrees) === 180 ? "B / " : ""}${degrees > 0 ? "+" : ""}${Math.abs(degrees) === 180 ? 180 : degrees}°`;
    this.text("hud-direction", direction);
    this.text("hud-bearing-left", level ? "−180°" : "B");
    this.text("hud-bearing-center", level ? "0°" : "A");
    this.text("hud-bearing-right", level ? "+180°" : "B");
    this.nodes.get("hud-view-direction")?.setAttribute("aria-label", level
      ? locked ? "View direction in the simulated world frame" : "Horizon leveling unavailable; last view basis held"
      : "View direction relative to the camera body");
    this.text("hud-fov", panoramic ? "BODY RELATIVE" : !raw && finite(fov) ? `FOV ${Math.round(fov * 180 / Math.PI)}°` : "BODY RELATIVE");
    this.text("hud-pitch", panoramic ? "ALL" : !known ? "—" : `${pitchDegrees > 0 ? "↑ +" : pitchDegrees < 0 ? "↓ " : ""}${pitchDegrees}°`);
    this.nodes.get("hud-bearing")?.setAttribute("aria-label", panoramic ? "Full sphere relative to camera body"
      : level ? `View yaw ${degrees} degrees and pitch ${pitchDegrees} degrees; ${locked ? "simulated world frame" : "leveling unavailable"}`
      : known ? `Body-relative yaw ${degrees} degrees, pitch ${pitchDegrees} degrees; A is zero degrees and B is 180 degrees`
        : "Body-relative direction unavailable");
    const window = this.nodes.get("hud-bearing-window");
    const duplicate = this.nodes.get("hud-bearing-wrap");
    const marker = this.nodes.get("hud-bearing-marker");
    if (window) { window.style.left = `${position}%`; window.style.width = `${field}%`; }
    if (marker) marker.style.left = `${position}%`;
    const wrappedPosition = position - field / 2 < 0 ? position + 100
      : position + field / 2 > 100 ? position - 100 : null;
    if (duplicate && wrappedPosition !== null) {
      duplicate.style.left = `${wrappedPosition}%`;
      duplicate.style.width = `${field}%`;
    }
    this.hidden("hud-bearing-window", panoramic || !known || raw);
    this.hidden("hud-bearing-wrap", panoramic || !known || raw || wrappedPosition === null);
    this.hidden("hud-bearing-marker", panoramic || !known);
    const pitchMarker = this.nodes.get("hud-pitch-marker");
    if (pitchMarker) pitchMarker.style.top = `${50 - elevation / Math.PI * 100}%`;
    this.hidden("hud-pitch-marker", panoramic || !known);
  }

  events(events, pts) {
    const received = new Map();
    for (const event of events) {
      if (!event || !EVENT_NAMES.includes(event.event) || !finite(event.pts) ||
        finite(pts) && event.pts > pts) continue;
      if (!received.has(event.event) || event.pts < received.get(event.event)) received.set(event.event, event.pts);
    }
    const launch = received.get("LAUNCH");
    for (const item of this.eventNodes) {
      if (!item.node) continue;
      const occurred = received.has(item.name);
      const stamp = occurred && finite(launch) && received.get(item.name) >= launch
        ? `T+${clock((received.get(item.name) - launch) / 1e6)}` : occurred ? "RECEIVED" : "—";
      const signature = `${occurred}:${stamp}`;
      if (item.value === signature) continue;
      item.value = signature;
      item.node.classList.toggle("is-recorded", occurred);
      const time = item.node.querySelector(".event-time");
      if (time) time.textContent = stamp;
      item.node.setAttribute("aria-label", `${item.name}: ${occurred ? `${stamp}, CM5 receipt time` : "not received"}`);
    }
  }

  plot(rows, pts) {
    const first = rows[0], last = rows.at(-1);
    const signature = `${rows.length}:${first?.pts}:${first?.altitude}:${last?.pts}:${last?.altitude}:${finite(pts) ? Math.floor(pts / 250000) : "none"}`;
    if (signature === this.plotSignature) return;
    const now = performance.now();
    if (rows.length && now - this.plotAt < 200) return;
    this.plotAt = now;
    this.plotSignature = signature;
    const segments = plotSegments(rows, pts);
    let low = Infinity, high = -Infinity, start = Infinity, end = -Infinity, count = 0;
    for (const segment of segments) for (const point of segment) {
      low = Math.min(low, point.altitude); high = Math.max(high, point.altitude);
      start = Math.min(start, point.pts); end = Math.max(end, point.pts); count++;
    }
    if (!count) {
      this.nodes.get("hud-altitude-plot")?.setAttribute("d", "");
      this.hidden("hud-altitude-dot", true);
      this.text("hud-plot-title", "Barometric altitude history; no data");
      return;
    }
    const minimum = Math.min(0, low), maximum = Math.max(0, high);
    const span = Math.max(1, maximum - minimum);
    const x = p => 2 + (p.pts - start) / Math.max(1, end - start) * 216;
    const y = p => 59 - (p.altitude - minimum) / span * 54;
    const reduced = boundedSegments(segments);
    let path = "";
    for (const segment of reduced) {
      for (let i = 0; i < segment.length; i++)
        path += `${i ? "L" : "M"}${x(segment[i]).toFixed(2)},${y(segment[i]).toFixed(2)}`;
    }
    this.nodes.get("hud-altitude-plot")?.setAttribute("d", path);
    const final = reduced.at(-1)?.at(-1);
    const dot = this.nodes.get("hud-altitude-dot");
    if (dot && final) {
      dot.setAttribute("cx", x(final).toFixed(2)); dot.setAttribute("cy", y(final).toFixed(2));
    }
    this.hidden("hud-altitude-dot", !final);
    this.text("hud-plot-title", `Barometric altitude history; ${count} samples, ${integer.format(low)} to ${integer.format(high)} metres`);
  }
}
