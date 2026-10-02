const sensorFields = {
  bmi088_accel: ["accel_m_s2"],
  bmi088_gyro: ["gyro_rad_s"],
  bmp581: ["pressure_pa", "temperature_c"],
  ina226: ["bus_voltage_v", "current_a", "power_w", "shunt_voltage_v"],
};
// Receiver freshness and producer validity remain independent.
export class TelemetryState {
  constructor() { this.sources = {}; this.health = null; this.healthAt = null; this.sessionId = null; }
  accept(record, now) {
    if (record.session_id && record.session_id !== this.sessionId) {
      this.sessionId = record.session_id;
      this.sources = {};
      this.health = null;
      this.healthAt = null;
    }
    if (record.type === "health") { this.health = record; this.healthAt = now; }
    if (record.type === "sensors") {
      for (const source of record.sources || []) {
        const row = source.rows?.at(-1);
        if (!row) continue;
        const sample = { valid: source.valid, status: source.status || (source.valid ? "fresh" : "unknown"), errors: source.errors || 0,
          backend: source.backend || record.backend || "unknown", timestamp_basis: source.timestamp_basis || record.timestamp_basis || "unknown",
          missed_polls: source.missed_polls || 0, host_clock_domain: record.acquisition_clock_domain, values: { ...(source.shared_values || {}) } };
        (source.columns || ["sequence", "monotonic_us", "read_start_monotonic_us", "last_good_monotonic_us", ...(source.valid ? (sensorFields[source.source] || []).map(key => "values." + key) : [])]).forEach((key, i) => {
          if (key.startsWith("values.")) sample.values[key.slice(7)] = row[i];
          else sample[key] = row[i];
        });
        if (!Object.keys(sample.values).length) delete sample.values;
        for (const key of ["monotonic_us", "read_start_monotonic_us", "last_good_monotonic_us"]) {
          if (typeof sample[key] === "number" && typeof source.base_monotonic_us === "number") sample[key] += source.base_monotonic_us;
        }
        const previous = this.sources[source.source];
        // Group order reflects first occurrence, not latest acquisition. UDP can also reorder batches.
        if (previous && typeof previous.sample.monotonic_us === "number" && typeof sample.monotonic_us === "number" &&
            (sample.monotonic_us < previous.sample.monotonic_us ||
             (sample.monotonic_us === previous.sample.monotonic_us && sample.sequence < previous.sample.sequence))) continue;
        this.sources[source.source] = { sample, receivedAt: now, units: source.units || {} };
      }
    } else if (record.type === "sensor_sample") {
      this.sources[record.source] = { sample: record, receivedAt: now };
    }
  }
  lines(now) {
    const lines = [];
    const healthFresh = this.healthAt !== null && now - this.healthAt <= 3000;
    const h = this.health;
    lines.push(`Phase: ${healthFresh && !h?.phase_stale ? h?.phase || "UNKNOWN" : "UNKNOWN · stale"}`);
    if (h) lines.push(`Capture: ${h.lifecycle || "unknown"}${healthFresh ? "" : " · stale"}${h.degraded ? " · degraded" : ""}`);
    for (const [source, entry] of Object.entries(this.sources)) {
      const sample = entry.sample;
      const stale = now - entry.receivedAt > 3000 || sample.stale === true;
      const valid = sample.valid === true && !stale;
      const values = sample.values || Object.fromEntries(Object.entries(sample).filter(([key]) => !["valid", "stale", "monotonic_us", "timestamp_ns", "seq", "sequence", "read_start_monotonic_us", "last_good_monotonic_us", "errors", "status", "error", "backend", "timestamp_basis", "missed_polls", "host_clock_domain"].includes(key)));
      const text = Object.entries(values).filter(([, value]) => value !== null && (typeof value !== "object" || Array.isArray(value)))
        .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.map(v => Number(v.toPrecision(5))).join(", ") : typeof value === "number" ? Number(value.toPrecision(5)) : value}${entry.units?.[key] ? " " + entry.units?.[key] : ""}`).join(" · ");
      lines.push(`${source}${sample.backend === "simulation" ? " [simulation]" : ""}: ${valid ? text || "valid" : stale ? "stale" : "invalid"}${!valid && text ? " · last " + text : ""}`);
    }
    if (!Object.keys(this.sources).length) lines.push("Sensors: no samples received");
    if (h?.components) lines.push(Object.entries(h.components).map(([key, value]) => `${key}: ${value}`).join(" · "));
    return lines;
  }
}
