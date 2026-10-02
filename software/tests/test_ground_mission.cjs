const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");

(async () => {
  const source = fs.readFileSync(path.join(__dirname, "../python/pigeonvision/ground/static/mission.js"), "utf8");
  const { MissionTimeline, pressureAltitude } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
  const near = (actual, expected, tolerance = 1e-8) =>
    assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} differs from ${expected}`);
  const originUs = 2000000;
  const session = (t, id = "one") => t.accept({ type: "session", session_id: id, clock_origin_ns: originUs * 1000 }, 0);
  const pressure = (t, pts, value, extra = {}, now = 0) => t.accept({
    type: "sensor_sample", source: "bmp581", session_id: t.sessionId,
    pts_us: pts, valid: true, stale: false, values: { pressure_pa: value }, ...extra,
  }, now);
  const phase = (t, pts, value, extra = {}, now = 0) => t.accept({
    type: "flight_status", session_id: t.sessionId, pts_us: pts,
    receive_monotonic_us: originUs + pts, phase: value, valid: true, stale: false, ...extra,
  }, now);

  test("unavailable values remain unknown, never new zero measurements", () => {
    const t = new MissionTimeline();
    const s = t.sample(0, 0);
    assert.equal(s.phase, "UNKNOWN");
    for (const key of ["altitude", "speed", "elapsed", "pressure"]) assert.equal(s[key], null);
    assert.deepEqual(s.plot, []);
    assert.equal(t.reference(0, 0, false), false);
    assert.equal(t.sample(NaN, 0).pressureFresh, false);
  });

  test("sampling at video PTS excludes future pressure, phase, and events", () => {
    const t = new MissionTimeline(); session(t);
    pressure(t, 0, 101325);
    phase(t, 0, "PAD");
    pressure(t, 200000, 99000);
    phase(t, 200000, "ASCENT");
    t.accept({ type: "flight_event", session_id: "one", pts_us: 300000,
      receive_monotonic_us: originUs + 200000, valid: true, stale: false,
      inferred_epoch: 0, event_seq: 1, event: "LAUNCH" }, 0);
    const earlier = t.sample(100000, 0, true);
    assert.equal(earlier.pressure, 101325);
    assert.equal(earlier.phase, "PAD");
    assert.equal(earlier.elapsed, null);
    assert.deepEqual(earlier.events, []);
    const later = t.sample(1200000, 0, true);
    near(later.elapsed, 1); // CM5 FC receipt time, not publication or physical ignition time.
    assert.equal(later.events[0].pts, 200000);
  });

  test("batch acquisition timestamps preserve all rows across validity groups", () => {
    const t = new MissionTimeline(); session(t);
    t.accept({ type: "sensors", session_id: "one", pts_us: 900000,
      backend: "simulation", sources: [
        { source: "bmp581", valid: true, base_monotonic_us: originUs + 100000,
          rows: [[1, 0, -10, 0, 101325, 20], [3, 200000, 199990, 200000, 100000, 21]] },
        { source: "bmp581", valid: false, status: "not_ready", base_monotonic_us: originUs + 200000,
          rows: [[2, 0, -10, -100000]] },
        { source: "ina226", valid: true, base_monotonic_us: originUs, rows: [[1, 0, 0, 0, 5]] },
      ] }, 100);
    assert.deepEqual(t.pressures.map(p => p.pts), [100000, 200000, 300000]);
    assert.equal(t.sample(150000, 100).pressure, 101325);
    assert.equal(t.sample(200000, 100).pressureFresh, false);
    assert.equal(t.sample(300000, 100).pressure, 100000);
    assert.equal(t.sample(300000, 100).simulated, true);
    t.accept({ type: "sensors", session_id: "one", pts_us: 1000000,
      sources: [{ source: "bmp581", valid: true, base_monotonic_us: originUs,
        columns: ["values.temperature_c", "monotonic_us", "values.pressure_pa"],
        rows: [[22, 400000, 99900]] }] }, 100);
    assert.equal(t.sample(400000, 100).pressure, 99900);
  });

  test("producer invalidity and missing pressure block readings without retaining old freshness", () => {
    for (const extra of [{ valid: false }, { stale: true }, { values: null },
      { values: { pressure_pa: 0 } }, { values: { pressure_pa: -1 } },
      { values: { pressure_pa: NaN } }, { values: { pressure_pa: null } }]) {
      const t = new MissionTimeline(); session(t);
      pressure(t, 0, 101325);
      pressure(t, 100000, 100000, extra);
      const s = t.sample(100000, 0);
      assert.equal(s.pressureFresh, false);
      assert.equal(s.pressure, null);
      assert.equal(s.altitude, null);
    }
  });

  test("live wall-clock expiry and video-age expiry differ from paused replay", () => {
    const t = new MissionTimeline(); session(t);
    pressure(t, 1000000, 101325, {}, 100);
    phase(t, 1000000, "PAD", {}, 100);
    assert.equal(t.sample(1000000, 3101, false).pressureFresh, false);
    const paused = t.sample(1000000, 3600000, true);
    assert.equal(paused.pressureFresh, true);
    assert.equal(paused.phase, "PAD");
    assert.equal(t.sample(1500001, 101, true).pressureFresh, false);
    assert.equal(t.sample(4000001, 101, true).phase, "UNKNOWN");
  });

  test("only fresh PAD pressure establishes reference; references cannot apply to earlier video", () => {
    const t = new MissionTimeline(); session(t);
    pressure(t, 1000000, 101325);
    phase(t, 1000000, "ASCENT");
    assert.equal(t.reference(1000000, 0, true), false);
    phase(t, 1100000, "PAD");
    assert.equal(t.reference(1600000, 0, true), false); // Pressure is too old.
    pressure(t, 1100000, 101325);
    assert.equal(t.reference(1100000, 0, true), true);
    assert.equal(t.sample(1000000, 0, true).altitude, null);
    t.setReference({ pressure: 100000, pts: 0, sessionId: "other" });
    assert.equal(t.pad.pressure, 101325);
    t.setReference(null);
    assert.equal(t.pad, null);
  });

  test("standard-atmosphere pressure altitude has correct units, sign, and known value", () => {
    near(pressureAltitude(101325, 101325), 0);
    near(pressureAltitude(89874.6, 101325), 1000, 2);
    assert.ok(pressureAltitude(100000, 101325) > 0);
    assert.ok(pressureAltitude(102000, 101325) < 0);
  });

  test("one-second altitude regression estimates speed and rejects gaps or invalid readings", () => {
    function ascent(times, invalidAt = null) {
      const t = new MissionTimeline(); session(t);
      pressure(t, 0, 101325);
      phase(t, 0, "PAD");
      assert.equal(t.reference(0, 0, true), true);
      for (const pts of times) {
        const altitude = 10 * pts / 1e6;
        const p = 101325 * Math.pow(1 - altitude / 44330, 1 / .190263);
        pressure(t, pts, p, pts === invalidAt ? { valid: false } : {});
      }
      return t.sample(1000000, 0, true);
    }
    const continuous = ascent([200000, 400000, 600000, 800000, 1000000]);
    near(continuous.altitude, 10);
    near(continuous.speed, 10);
    assert.equal(ascent([200000, 400000, 800000, 1000000]).speed, null);
    const invalid = ascent([200000, 400000, 600000, 800000, 1000000], 600000);
    assert.equal(invalid.speed, null);
    assert.ok(invalid.plot.some(point => point.altitude === null));
  });

  test("new sessions clear reference, events, pressure, phase, and provenance", () => {
    const t = new MissionTimeline(); session(t);
    pressure(t, 0, 101325, { backend: "simulation" });
    phase(t, 0, "PAD");
    assert.equal(t.reference(0, 0, true), true);
    t.accept({ type: "flight_event", session_id: "one", pts_us: 100000,
      receive_monotonic_us: originUs + 100000, event: "LAUNCH", inferred_epoch: 0,
      event_seq: 1, valid: true, stale: false }, 0);
    assert.equal(t.events.length, 1);
    t.accept({ type: "session", session_id: "two", clock_origin_ns: 4000000000 }, 0);
    assert.equal(t.sessionId, "two");
    assert.equal(t.origin, 4000000);
    assert.equal(t.pad, null);
    assert.deepEqual(t.pressures, []);
    assert.deepEqual(t.phases, []);
    assert.deepEqual(t.events, []);
    assert.equal(t.sample(0, 0, true).simulated, false);
  });

  test("duplicate events preserve the initial FC receipt time and invalid events are excluded", () => {
    const t = new MissionTimeline(); session(t);
    const event = { type: "flight_event", session_id: "one", pts_us: 200000,
      receive_monotonic_us: originUs + 100000, event: "LAUNCH", inferred_epoch: 0,
      event_seq: 1, valid: true, stale: false };
    t.accept(event, 0);
    t.accept({ ...event, pts_us: 300000, receive_monotonic_us: originUs + 200000 }, 10);
    t.accept({ ...event, event_seq: 2, event: "APOGEE", valid: false }, 10);
    assert.equal(t.events.length, 1);
    assert.equal(t.events[0].pts, 100000);
    near(t.sample(600000, 100, true).elapsed, .5);
  });

  // Protect acquisition timing and session isolation independently of the transport UI.
  test("regression: delayed retired-session metadata cannot replace the active mission", () => {
    const t = new MissionTimeline(); session(t, "one"); session(t, "two");
    pressure(t, 0, 101325);
    phase(t, 0, "PAD");
    assert.equal(t.reference(0, 0, true), true);
    t.accept({ type: "sensor_sample", source: "bmp581", session_id: "one",
      pts_us: 0, valid: true, values: { pressure_pa: 90000 } }, 100);
    assert.equal(t.sessionId, "two");
    assert.equal(t.pad.pressure, 101325);
  });

  test("regression: FC phase expiry uses receipt time rather than repeated status publication", () => {
    const t = new MissionTimeline(); session(t);
    phase(t, 2900000, "PAD", { receive_monotonic_us: originUs });
    assert.equal(t.sample(3100000, 0, true).phase, "UNKNOWN");
  });

  test("regression: health phase expiry uses nested FC receipt time", () => {
    const t = new MissionTimeline(); session(t);
    t.accept({ type: "health", session_id: "one", pts_us: 2900000,
      phase: "PAD", phase_stale: false,
      flight_uart: { valid: true, stale: false, receive_monotonic_us: originUs } }, 0);
    assert.equal(t.sample(3100000, 0, true).phase, "UNKNOWN");
  });

  test("regression: a batch with unknown acquisition origin does not invent fresh publication-time samples", () => {
    const t = new MissionTimeline();
    t.accept({ type: "sensors", session_id: "one", pts_us: 900000,
      sources: [{ source: "bmp581", valid: true, base_monotonic_us: originUs,
        rows: [[1, 0, 0, 0, 101325, 20], [2, 100000, 100000, 100000, 100000, 20]] }] }, 0);
    assert.equal(t.sample(900000, 0, true).pressureFresh, false);
    assert.equal(t.pressures.length, 0);
    session(t);
    assert.deepEqual(t.pressures.map(p => p.pts), [0, 100000]);
    assert.equal(t.sample(100000, 0, true).pressure, 100000);
    assert.equal(t.sample(900000, 0, true).pressureFresh, false);
  });

  test("regression: null acquisition timestamps are not coerced into zero offsets", () => {
    const t = new MissionTimeline(); session(t);
    t.accept({ type: "sensors", session_id: "one", pts_us: 100000,
      sources: [{ source: "bmp581", valid: true, base_monotonic_us: originUs + 100000,
        rows: [[1, null, null, null, 101325, 20]] }] }, 0);
    assert.equal(t.sample(100000, 0, true).pressureFresh, false);
  });

  test("legacy phase fixtures without acquisition provenance use only their declared PTS", () => {
    const t = new MissionTimeline();
    t.accept({ type: "flight_status", session_id: "one", pts_us: 100000,
      phase: "PAD", valid: true, stale: false }, 0);
    assert.equal(t.sample(99999, 0, true).phase, "UNKNOWN");
    assert.equal(t.sample(100000, 0, true).phase, "PAD");
    assert.equal(t.sample(3100000, 0, true).phase, "UNKNOWN");
    t.accept({ type: "health", session_id: "one", pts_us: 3200000,
      phase: "ASCENT", phase_stale: false }, 0);
    assert.equal(t.sample(3200000, 0, true).phase, "ASCENT");
  });

  test("explicit unknown or future FC receipt time and invalid nested UART state remain unknown", () => {
    for (const uart of [
      { valid: true, stale: false, receive_monotonic_us: null },
      { valid: false, stale: false, receive_monotonic_us: originUs },
      { valid: true, stale: true, receive_monotonic_us: originUs },
      { valid: true, stale: false, receive_monotonic_us: originUs + 200000 },
    ]) {
      const t = new MissionTimeline(); session(t);
      t.accept({ type: "health", session_id: "one", pts_us: 100000,
        phase: "PAD", phase_stale: false, flight_uart: uart }, 0);
      assert.equal(t.sample(100000, 0, true).phase, "UNKNOWN");
    }
    const t = new MissionTimeline(); session(t);
    phase(t, 100000, "PAD", { receive_monotonic_us: null });
    assert.equal(t.sample(100000, 0, true).phase, "UNKNOWN");
  });

  test("individual acquisition records wait for origin and retain their original receipt freshness", () => {
    const t = new MissionTimeline();
    t.accept({ type: "sensor_sample", source: "bmp581", session_id: "one",
      pts_us: 900000, monotonic_us: originUs + 100000,
      valid: true, values: { pressure_pa: 101325 } }, 0);
    t.accept({ type: "flight_status", session_id: "one", pts_us: 200000,
      receive_monotonic_us: originUs + 100000, phase: "PAD", valid: true, stale: false }, 0);
    t.accept({ type: "flight_event", session_id: "one", pts_us: 900000,
      receive_monotonic_us: originUs + 150000, event: "LAUNCH", inferred_epoch: 0,
      event_seq: 1, valid: true, stale: false }, 0);
    assert.equal(t.sample(200000, 0, true).pressureFresh, false);
    session(t);
    assert.equal(t.pending.length, 0);
    assert.equal(t.sample(200000, 0, true).pressure, 101325);
    assert.equal(t.sample(200000, 0, true).phase, "PAD");
    near(t.sample(200000, 0, true).elapsed, .05);
    assert.equal(t.sample(200000, 3000, false).pressureFresh, false);
  });

  test("session retirement stays bounded and an explicit replay reset permits revisiting old sessions", () => {
    const t = new MissionTimeline();
    for (let i = 0; i < 40; i++) session(t, `session-${i}`);
    assert.equal(t.retiredSessions.size, 32);
    t.reset({ preserveSessionHistory: true });
    assert.equal(t.accept({ type: "session", session_id: "session-38", clock_origin_ns: originUs * 1000 }, 0), false);
    assert.equal(t.sessionId, "session-39");
    t.reset();
    session(t, "session-38");
    assert.equal(t.sessionId, "session-38");
    assert.equal(t.retiredSessions.size, 0);
  });
})().catch(error => { console.error(error); process.exitCode = 1; });
