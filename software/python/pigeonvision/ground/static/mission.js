// Mission values are sampled at the displayed video PTS, never future metadata.
// Pressure altitude assumes the standard tropospheric temperature lapse rate.
export const pressureAltitude = (p, p0) => 44330 * (1 - Math.pow(p / p0, .190263));
const finite = Number.isFinite;
const owns = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const latest = (rows, pts) => {
  let lo = 0, hi = rows.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; rows[mid].pts <= pts ? lo = mid + 1 : hi = mid; }
  return rows[lo - 1] || null;
};
function insert(rows, item, limit = 9000) {
  if (!finite(item.pts)) return;
  const last = rows.at(-1);
  if (!last || item.pts > last.pts) rows.push(item);
  else {
    const i = rows.findIndex(row => row.pts >= item.pts);
    if (rows[i]?.pts === item.pts) return;
    rows.splice(i, 0, item);
  }
  if (rows.length > limit) rows.splice(0, rows.length - limit);
}
export class MissionTimeline {
  constructor() { this.reset(); }
  reset({ preserveSessionHistory = false } = {}) {
    if (!preserveSessionHistory) {
      this.sessionId = null;
      this.retiredSessions = new Set();
    }
    this.origin = null;
    this.pressures = [];
    this.phases = [];
    this.events = [];
    this.pad = null;
    this.simulated = false;
    this.pending = [];
  }
  accept(record, now) {
    if (record.session_id && record.session_id !== this.sessionId) {
      if (this.retiredSessions.has(record.session_id)) return false;
      if (this.sessionId) {
        this.retiredSessions.add(this.sessionId);
        if (this.retiredSessions.size > 32) this.retiredSessions.delete(this.retiredSessions.values().next().value);
      }
      this.reset({ preserveSessionHistory: true });
      this.sessionId = record.session_id;
    }
    if (record.type === "session") {
      if (finite(record.clock_origin_ns)) this.origin = record.clock_origin_ns / 1000;
      this.simulated ||= record.simulated === true;
    }
    if (this.origin === null && finite(record.publication_timestamp_ns) && finite(record.pts_us))
      this.origin = record.publication_timestamp_ns / 1000 - record.pts_us;
    if (this.origin !== null && this.pending.length) {
      const pending = this.pending;
      this.pending = [];
      for (const item of pending) this.accept(item.record, item.now);
    }
    const pts = record.pts_us;
    const fc = record.type === "health" ? record.flight_uart : record;
    const needsOrigin = record.type === "sensors" ||
      (["flight_status", "flight_event", "health"].includes(record.type) && finite(fc?.receive_monotonic_us)) ||
      (record.type === "sensor_sample" && finite(record.monotonic_us));
    if (this.origin === null && needsOrigin) {
      // Wait for clock alignment instead of replacing acquisition time with publication time.
      this.pending.push({ record, now });
      if (this.pending.length > 128) this.pending.shift();
      return true;
    }
    if (["flight_status", "health"].includes(record.type)) {
      // Older records without an acquisition field can only use their declared publication PTS.
      const acquiredPts = finite(fc?.receive_monotonic_us) ? fc.receive_monotonic_us - this.origin
        : fc && owns(fc, "receive_monotonic_us") ? null : pts;
      const valid = record.type === "health"
        ? record.phase_stale === false && (!fc || fc.valid === true && !fc.stale)
        : record.valid === true && !record.stale;
      insert(this.phases, { pts, acquiredPts, receivedAt: now, phase: fc?.phase ?? record.phase, valid,
        simulated: record.backend === "simulation" }, 4096);
    }
    if (record.type === "flight_event" && record.valid === true && !record.stale) {
      const eventPts = finite(record.receive_monotonic_us) ? record.receive_monotonic_us - this.origin
        : owns(record, "receive_monotonic_us") ? null : pts;
      const id = `${record.inferred_epoch}:${record.event_seq}:${record.event}`;
      if (!this.events.some(e => e.id === id)) {
        insert(this.events, { pts: eventPts, id, event: record.event }, 128);
      }
    }
    if (record.type === "sensors") for (const source of record.sources || []) {
      if (source.source !== "bmp581") continue;
      const columns = source.columns || ["sequence", "monotonic_us", "read_start_monotonic_us",
        "last_good_monotonic_us", ...(source.valid ? ["values.pressure_pa", "values.temperature_c"] : [])];
      for (const row of source.rows || []) {
        const sample = Object.fromEntries(columns.map((column, i) => [column, row[i]]));
        if (!finite(sample.monotonic_us) ||
            (owns(source, "base_monotonic_us") && !finite(source.base_monotonic_us))) continue;
        const acquired = sample.monotonic_us + (source.base_monotonic_us ?? 0);
        const acquiredPts = acquired - this.origin;
        insert(this.pressures, {
          pts: acquiredPts, acquiredPts,
          pressure: sample["values.pressure_pa"], valid: source.valid === true,
          receivedAt: now, simulated: (source.backend || record.backend) === "simulation",
        });
      }
    }
    if (record.type === "sensor_sample" && record.source === "bmp581") {
      const acquiredPts = finite(record.monotonic_us) ? record.monotonic_us - this.origin
        : owns(record, "monotonic_us") ? null : pts;
      insert(this.pressures, { pts: acquiredPts, acquiredPts, pressure: record.values?.pressure_pa,
        valid: record.valid === true && !record.stale, receivedAt: now, simulated: record.backend === "simulation" });
    }
    return true;
  }
  reference(pts, now, replay) {
    const s = this.sample(pts, now, replay);
    if (!s.pressureFresh || s.phase !== "PAD") return false;
    this.pad = { pressure: s.pressure, pts, sessionId: this.sessionId };
    return true;
  }
  setReference(pad) {
    if (pad === null) this.pad = null;
    else if (pad.sessionId === this.sessionId && finite(pad.pressure) && pad.pressure > 0 && finite(pad.pts)) this.pad = { ...pad };
  }
  sample(pts, now, replay = false) {
    const result = { altitude: null, speed: null, elapsed: null, pressure: null,
      pressureFresh: false, phase: "UNKNOWN", events: [], plot: [], simulated: this.simulated,
      reference: this.pad, pts };
    if (!finite(pts)) return result;
    const pressure = latest(this.pressures, pts), phase = latest(this.phases, pts);
    const fresh = (row, age) => row && row.valid && finite(row.acquiredPts) &&
      pts >= row.acquiredPts && pts - row.acquiredPts < age &&
      (replay || now - row.receivedAt < 3000);
    if (fresh(phase, 3e6)) result.phase = phase.phase || "UNKNOWN";
    result.simulated ||= !!pressure?.simulated || !!phase?.simulated;
    result.events = this.events.filter(e => e.pts <= pts);
    const launch = result.events.find(e => e.event === "LAUNCH");
    if (launch) result.elapsed = (pts - launch.pts) / 1e6;
    result.pressureFresh = !!fresh(pressure, 500000) && finite(pressure.pressure) && pressure.pressure > 0;
    if (result.pressureFresh) result.pressure = pressure.pressure;
    if (!this.pad || pts < this.pad.pts) return result;
    const history = this.pressures.filter(p => p.pts >= Math.max(this.pad.pts, pts - 120e6) && p.pts <= pts);
    result.plot = history.map(p => ({ pts: p.pts,
      altitude: p.valid && finite(p.pressure) && p.pressure > 0 ? pressureAltitude(p.pressure, this.pad.pressure) : null }));
    if (!result.pressureFresh) return result;
    result.altitude = pressureAltitude(pressure.pressure, this.pad.pressure);
    // Least-squares slope over one second. Invalid readings or gaps break the fit.
    const window = result.plot.filter(p => p.pts >= pressure.pts - 1e6);
    if (window.length < 4 || window.at(-1).pts - window[0].pts < 500000 ||
        window.some((p, i) => p.altitude === null || (i && p.pts - window[i - 1].pts > 250000))) return result;
    const meanT = window.reduce((s, p) => s + (p.pts - pressure.pts) / 1e6, 0) / window.length;
    const meanH = window.reduce((s, p) => s + p.altitude, 0) / window.length;
    let covariance = 0, variance = 0;
    for (const p of window) {
      const dt = (p.pts - pressure.pts) / 1e6 - meanT;
      covariance += dt * (p.altitude - meanH); variance += dt * dt;
    }
    if (variance > 0) result.speed = covariance / variance;
    return result;
  }
}
