// Bootstrap freshness with local assets and mocked transport.
const assert = require("node:assert/strict");
const { openGroundFixture } = require("./browser/ground_fixture.cjs");

(async () => {
  const fixture = await openGroundFixture({ clock: true });
  const { browser, page, errors, emit, snapshot } = fixture;
  try {
    const { frame } = fixture;
    await page.locator("#controls-toggle").click();
    await page.locator("#operator-controls").waitFor({ state: "visible" });
    await page.locator("#advanced-controls > summary").click();
    const camera = name => ({ image_size: [64, 64], crop: [0, 0, 64, 64], output_size: [64, 64],
      K: [[32, 0, 31.5], [0, 32, 31.5], [0, 0, 1]], D: [0, 0, 0, 0], xi: 1,
      flip_x: false, flip_y: true, max_theta_deg: null,
      R_camera_from_rig: name === "A" ? [[1, 0, 0], [0, 1, 0], [0, 0, 1]] : [[-1, 0, 0], [0, 1, 0], [0, 0, -1]],
      provenance: { device_id: `synthetic-${name}` } });
    const record = (type, session_id, fields) => ({ type: "metadata", record: { type, session_id, ...fields } });
    const health = (id, pts) => record("health", id, { pts_us: pts, phase: "PAD", phase_stale: false });
    const pressure = (id, pts) => record("sensor_sample", id, { source: "bmp581", pts_us: pts,
      valid: true, values: { pressure_pa: 101325 } });
    const metadata = id => [record("session", id, { clock_origin_ns: 0,
      cameras: ["A", "B"].map(id => ({ id, device: `synthetic-${id}`, sensor_size: [64, 64], width: 64, height: 64, flip_x: false, flip_y: true })) }),
      ...["A", "B"].map(camera_id => record("frame", id, { camera_id, pts_us: 0, sensor_crop: [0, 0, 64, 64] })), health(id, 0), pressure(id, 0)];
    const status = (replay, generation) => ({ type: "status", state: "ready", source: replay ? "fixture.ts" : "udp://synthetic:5000", replay, playing: !replay, generation, reset: true });
    await emit(status(false, 1));
    await emit({ type: "calibration", calibration: { schema_version: 1, model: "mei", rig_alignment_status: "synthetic_test_only", cameras: { A: camera("A"), B: camera("B") } } });
    await emit({ type: "bootstrap_start", id: 1, metadata: metadata("live") });
    await frame("A", 0, true);
    await frame("B", 0, true);
    await frame("A", 1000);
    await emit({ type: "bootstrap_checkpoint", id: 1, sequence: 1 });
    await page.waitForFunction(() => window.testSent.some(value => value.type === "bootstrap_ack" && value.id === 1));
    assert.equal((await snapshot()).pair.cached, true);
    assert.equal((await snapshot()).presentation.frame.pts, null);
    await page.waitForFunction(() => document.getElementById("overlay").textContent.toLowerCase().includes("cached video"));
    assert.equal((await snapshot()).station.mission.pressureFresh, false);
    assert.equal((await snapshot()).station.mission.phase, "UNKNOWN");
    assert.match(await page.locator("#flight-telemetry").textContent(), /stale/);
    await page.selectOption("#view", "a");
    assert.equal((await snapshot()).presentation.frame.pts, null, "Cached raw pixels cannot advance live mission telemetry");
    await page.selectOption("#view", "perspective");

    await emit({ type: "bootstrap_snapshot_end", id: 1 });
    await frame("B", 1000);
    await page.waitForFunction(() => window.pigeonGround.snapshot().pair?.b === 1000);
    assert.equal((await snapshot()).pair.cached, true, "Pairing one fresh camera with cached pixels must remain held");
    assert.equal((await snapshot()).presentation.frame.pts, null);
    await frame("A", 2000);
    await frame("B", 2000);
    await page.waitForFunction(() => window.pigeonGround.snapshot().presentation.frame.pts === 2000);
    assert.equal((await snapshot()).station.mission.pressureFresh, false, "Fresh video cannot refresh cached pressure");
    await emit(health("live", 2000));
    await emit(pressure("live", 2000));
    await frame("A", 3000);
    await frame("B", 3000);
    await page.waitForFunction(() => window.pigeonGround.snapshot().station.mission.pressureFresh);
    assert.equal((await snapshot()).station.mission.phase, "PAD");
    assert.equal((await snapshot()).pair.cached, false, "Genuine arrivals before bootstrap_end retain live currency");
    await emit({ type: "bootstrap_end", id: 1 });

    // The source stops after queuing continuation, while three individually
    // bounded decode checkpoints take longer than telemetry's freshness window.
    await emit(status(false, 2));
    await emit({ type: "bootstrap_start", id: 2, metadata: metadata("slow") });
    const queuedAt = await page.evaluate(() => performance.now());
    for (let sequence = 1; sequence <= 3; sequence++) {
      await page.evaluate(() => { window.testHoldDecodeOutputs = true; });
      await frame("A", sequence * 1000, sequence === 1);
      await frame("B", sequence * 1000, sequence === 1);
      await emit({ type: "bootstrap_checkpoint", id: 2, sequence });
      await page.waitForFunction(() => window.testHeldDecodeOutputs.length === 2);
      await page.clock.runFor(1200);
      assert.equal(await page.evaluate(sequence => window.testSent.some(value =>
        value.type === "bootstrap_ack" && value.id === 2 && value.sequence === sequence), sequence), false);
      await page.evaluate(() => {
        window.testHoldDecodeOutputs = false;
        for (const output of window.testHeldDecodeOutputs.splice(0)) output();
      });
      await page.clock.runFor(20);
      await page.waitForFunction(sequence => window.testSent.some(value =>
        value.type === "bootstrap_ack" && value.id === 2 && value.sequence === sequence), sequence);
    }
    await emit({ type: "bootstrap_snapshot_end", id: 2 });
    const age = await page.evaluate(start => performance.now() - start, queuedAt);
    assert.ok(age > 3500);
    await emit({ ...health("slow", 4000), received_age_ms: age });
    await emit({ ...pressure("slow", 4000), received_age_ms: age });
    for (const name of ["A", "B"]) await frame(name, 4000, false, { received_age_ms: age });
    await emit({ type: "bootstrap_checkpoint", id: 2, sequence: 4 });
    await page.waitForFunction(() => window.testSent.some(value =>
      value.type === "bootstrap_ack" && value.id === 2 && value.sequence === 4));
    await emit({ type: "bootstrap_end", id: 2 });
    await page.clock.runFor(250);
    assert.ok((await snapshot()).presentation.frame.frameAgeMs > 3500, "Draining continuation cannot refresh old video");
    assert.equal((await snapshot()).station.mission.pressureFresh, false);
    assert.equal((await snapshot()).station.mission.phase, "UNKNOWN");
    await page.waitForFunction(() => /stale/i.test(document.getElementById("overlay").textContent));
    assert.match(await page.locator("#overlay").textContent(), /stale/i);
    assert.match(await page.locator("#flight-telemetry").textContent(), /stale/);

    await emit(health("slow", 5000));
    await emit(pressure("slow", 5000));
    for (const name of ["A", "B"]) await frame(name, 5000);
    await page.waitForFunction(() => window.pigeonGround.snapshot().station.mission.pressureFresh);
    assert.ok((await snapshot()).presentation.frame.frameAgeMs < 1000);
    assert.equal((await snapshot()).station.mission.phase, "PAD");

    // Decoder work also consumes freshness time, even after bootstrap ends.
    await page.evaluate(() => { window.testHoldDecodeOutputs = true; });
    for (const name of ["A", "B"]) await frame(name, 6000);
    await page.waitForFunction(() => window.testHeldDecodeOutputs.length === 2);
    await page.clock.runFor(1500);
    await page.evaluate(() => {
      window.testHoldDecodeOutputs = false;
      for (const output of window.testHeldDecodeOutputs.splice(0)) output();
    });
    await page.waitForFunction(() => window.pigeonGround.snapshot().presentation.frame.pts === 6000);
    assert.ok((await snapshot()).presentation.frame.frameAgeMs >= 1500, "Decoder output must keep original arrival time");

    await emit(status(true, 3));
    await emit({ type: "bootstrap_start", id: 3, metadata: metadata("replay") });
    await frame("A", 0, true);
    await frame("B", 0, true);
    await emit({ type: "bootstrap_checkpoint", id: 3, sequence: 1 });
    await page.waitForFunction(() => window.testSent.some(value => value.type === "bootstrap_ack" && value.id === 3));
    await page.waitForFunction(() => window.pigeonGround.snapshot().station.mission.pressureFresh);
    assert.equal((await snapshot()).presentation.frame.pts, 0);
    assert.equal((await snapshot()).pair.cached, false);
    assert.equal((await snapshot()).station.mission.phase, "PAD");
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ checked: ["stopped live snapshot is held", "cached telemetry is stale", "raw and mixed paired currency", "live bootstrap continuation", "source stops during slow bootstrap", "decoder delay preserves age", "paused replay preserved"] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
