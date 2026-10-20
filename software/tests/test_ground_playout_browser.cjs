// Local assets and mocked transport/decoder only; no live receiver or camera.
const assert = require("node:assert/strict");
const { openGroundFixture } = require("./browser/ground_fixture.cjs");

(async () => {
  const fixture = await openGroundFixture();
  const { browser, page, emit, snapshot, errors } = fixture;
  try {
    await page.evaluate(async () => {
      const { Renderer } = await import("/static/projection.js");
      const draw = Renderer.prototype.draw;
      Renderer.prototype.draw = function () {
        if (window.testTrace) window.testTrace.draws++;
        return draw.call(this);
      };
      window.testFrames = [];
      const Decoder = window.VideoDecoder;
      window.VideoDecoder = class extends Decoder {
        constructor(callbacks) {
          super({ ...callbacks, output: frame => { window.testFrames.push(frame); callbacks.output(frame); } });
        }
      };
      const clone = VideoFrame.prototype.clone;
      VideoFrame.prototype.clone = function () { const frame = clone.call(this); window.testFrames.push(frame); return frame; };
      window.testPacket = (camera_id, timestamp_us, keyframe = false) => {
        const header = new TextEncoder().encode(JSON.stringify({ type: "frame", camera_id,
          timestamp_us, keyframe, codec: "avc1.42001f" }));
        const packet = new Uint8Array(5 + header.length);
        new DataView(packet.buffer).setUint32(0, header.length);
        packet.set(header, 4); packet[packet.length - 1] = 1;
        window.testSocket.onmessage({ data: packet.buffer });
      };
      window.testPair = (pts, keyframe = false) => { window.testPacket("A", pts, keyframe); window.testPacket("B", pts, keyframe); };
    });
    const status = playing => ({ type: "status", state: "ready", source: "udp://synthetic:5000",
      replay: false, playing, generation: 1 });
    await emit({ ...status(true), reset: true });
    const camera = name => ({ image_size: [64, 64], crop: [0, 0, 64, 64], output_size: [64, 64],
      K: [[32, 0, 31.5], [0, 32, 31.5], [0, 0, 1]], D: [0, 0, 0, 0], xi: 1,
      flip_x: false, flip_y: true, max_theta_deg: null,
      R_camera_from_rig: name === "A" ? [[1, 0, 0], [0, 1, 0], [0, 0, 1]] : [[-1, 0, 0], [0, 1, 0], [0, 0, -1]],
      provenance: { device_id: `synthetic-${name}` } });
    await emit({ type: "calibration", calibration: { schema_version: 1, model: "mei",
      rig_alignment_status: "synthetic_test_only", cameras: { A: camera("A"), B: camera("B") },
      display_colour: { method: "display_rgb_gain", reference_camera: "A",
        gains: { A: [1, 1, 1], B: [1, 1, 1] } } } });
    const geometry = async session_id => {
      await emit({ type: "metadata", record: { type: "session", session_id,
        cameras: ["A", "B"].map(id => ({ id, device: `synthetic-${id}`, sensor_size: [64, 64],
          width: 64, height: 64, flip_x: false, flip_y: true })) } });
      for (const camera_id of ["A", "B"]) await emit({ type: "metadata", record: { type: "frame",
        camera_id, session_id, sensor_crop: [0, 0, 64, 64] } });
    };
    await geometry("one");
    const view = mode => emit({ type: "view", initial: true, view: { mode, yaw: 0, pitch: 0, roll: 0, fov: 1.9 } });
    const presented = pts => page.waitForFunction(pts => window.pigeonGround.snapshot().presentation.frame.pts === pts, pts);
    const pair = (pts, key = false) => page.evaluate(({ pts, key }) => window.testPair(pts, key), { pts, key });
    // B has never supplied verified pixels, so its first output is allowed raw
    // while A is held. It must not pair with A's retired pre-hold original.
    await fixture.frame("A", 1000000, true);
    await page.waitForFunction(() => window.pigeonGround.snapshot().presentation.raw.A === 1000000);
    assert.equal((await snapshot()).pending.A, 1);
    await emit({ type: "metadata", record: { type: "frame", camera_id: "A", session_id: "one", sensor_crop: null } });
    await fixture.frame("A", 1033333);
    await page.waitForFunction(() => window.pigeonGround.snapshot().presentation.heldForMetadata.A);
    await fixture.frame("B", 1000000, true);
    await page.waitForFunction(() => window.pigeonGround.snapshot().presentation.raw.B === 1000000);
    assert.equal((await snapshot()).pending.A, 0);
    assert.equal((await snapshot()).pairs, 0, "A late B cannot form a pair with A's pre-hold original");
    assert.equal((await snapshot()).presentation.frame.pts, null);
    await emit({ type: "metadata", record: { type: "frame", camera_id: "A", session_id: "one", sensor_crop: [0, 0, 64, 64] } });
    await fixture.frame("B", 1033333); await presented(1033333);
    const results = [];
    for (const mode of ["perspective", "a"]) {
      await view(mode);
      const result = await page.evaluate(async ({ mode, base }) => {
        const count = 30, start = performance.now() + 50;
        const decoded = window.pigeonGround.snapshot().decoded.A;
        window.testTrace = { draws: 0, paints: [] };
        let previous = null, recording = true;
        const sample = now => {
          const state = window.pigeonGround.snapshot(), pts = state.presentation.frame.pts;
          if (pts >= base && pts !== previous) {
            window.testTrace.paints.push({ now, pts, paired: state.pair?.a === state.pair?.b }); previous = pts;
          }
          if (recording) requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
        await Promise.all(Array.from({ length: count / 3 }, (_, group) => new Promise(resolve => setTimeout(() => {
          for (let j = 0; j < 3; j++) {
            const index = group * 3 + j;
            window.testPair(base + Math.round(index * 1000000 / 30), index === 0);
          }
          resolve();
        }, Math.max(0, start + group * 100 - performance.now())))));
        await new Promise(resolve => setTimeout(resolve, 250)); recording = false;
        const trace = window.testTrace; window.testTrace = null;
        const gaps = trace.paints.slice(1).map((p, i) => p.now - trace.paints[i].now).sort((a, b) => a - b);
        return { mode, decoded: window.pigeonGround.snapshot().decoded.A - decoded,
          displayed: trace.paints.length, draws: trace.draws, maxGapMs: Math.max(...gaps),
          medianGapMs: gaps[Math.floor(gaps.length / 2)], pairsAligned: trace.paints.every(p => p.paired) };
      }, { mode, base: mode === "a" ? 20000000 : 10000000 });
      assert.equal(result.decoded, 30);
      assert.ok(result.displayed >= 28, `${mode}: burst delivery must retain 30 fps cadence: ${JSON.stringify(result)}`);
      assert.ok(result.medianGapMs > 25 && result.medianGapMs < 42, JSON.stringify(result));
      assert.ok(result.maxGapMs < 70, JSON.stringify(result));
      assert.ok(result.draws <= 32, "A display refresh must draw the visible source once, rather than three times per pair");
      assert.ok(result.pairsAligned);
      results.push(result);
    }

    await view("perspective");
    await pair(25000000, true); await presented(25000000);
    await pair(25100000);
    await page.waitForFunction(() => window.pigeonGround.snapshot().playout.pending.pair > 0);
    await emit({ type: "metadata", record: { type: "frame", camera_id: "B", session_id: "one", sensor_crop: null } });
    await fixture.frame("B", 25133333);
    await page.waitForFunction(() => window.pigeonGround.snapshot().presentation.heldForMetadata.B);
    await page.waitForTimeout(150);
    assert.equal((await snapshot()).presentation.frame.pts, null,
      "Previously queued verified frames cannot restore currency during a newer metadata hold");
    assert.equal((await snapshot()).presentation.pair.a, 25000000);
    assert.deepEqual((await snapshot()).playout.pending, { A: 0, B: 0, pair: 0 });
    await emit({ type: "metadata", record: { type: "frame", camera_id: "B", session_id: "one", sensor_crop: [0, 0, 64, 64] } });
    await fixture.frame("A", 25133333);
    await presented(25133333);

    await view("b");
    await fixture.frame("B", 25233333);
    await page.waitForFunction(() => window.pigeonGround.snapshot().playout.pending.B > 0);
    await emit({ type: "metadata", record: { type: "frame", camera_id: "A", session_id: "one", sensor_crop: null } });
    for (let i = 0; i < 6; i++) {
      await fixture.frame("A", 25233333 + i * 1000);
      await page.waitForTimeout(20);
    }
    await page.waitForFunction(() => window.pigeonGround.snapshot().presentation.raw.B === 25233333);
    assert.equal((await snapshot()).presentation.heldForMetadata.A, true);
    assert.equal((await snapshot()).presentation.frame.pts, 25233333,
      "A metadata hold must preserve B's independently verified queued raw frame and clock");
    await emit({ type: "metadata", record: { type: "frame", camera_id: "A", session_id: "one", sensor_crop: [0, 0, 64, 64] } });
    await view("perspective"); await presented(25233333);

    // Several late decoder outputs must catch up on one refresh, retaining the
    // codec reference chain and the original receipt age, rather than adding lag.
    await pair(30000000, true); await presented(30000000);
    const decoderCount = await page.evaluate(() => window.testDecoders.length);
    const beforeDrops = (await snapshot()).playout.dropped;
    await page.evaluate(() => { window.testHoldDecodeOutputs = true;
      for (let i = 1; i <= 6; i++) window.testPair(30000000 + i * 33333); });
    await page.waitForFunction(() => window.testHeldDecodeOutputs.length === 12);
    await page.waitForTimeout(300);
    await page.evaluate(() => { window.testHoldDecodeOutputs = false;
      for (const output of window.testHeldDecodeOutputs.splice(0)) output(); });
    await presented(30199998);
    assert.equal(await page.evaluate(() => window.testDecoders.length), decoderCount);
    assert.ok((await snapshot()).playout.dropped >= beforeDrops + 15);
    assert.ok((await snapshot()).presentation.frame.frameAgeMs >= 300);
    assert.ok(Object.values((await snapshot()).playout.pending).every(count => count <= 6));

    await pair(31000000, true); await presented(31000000);
    await pair(31033333);
    await page.waitForFunction(() => window.pigeonGround.snapshot().playout.pending.A > 0);
    await emit(status(false));
    await page.waitForTimeout(150);
    assert.equal((await snapshot()).presentation.frame.pts, 31000000, "Pause must discard queued future images");
    assert.deepEqual((await snapshot()).playout.pending, { A: 0, B: 0, pair: 0 });
    assert.equal(await page.evaluate(() => window.testFrames.every(frame => frame.format === null)), true,
      "Presented, superseded and paused frames must all close");
    await emit(status(true));
    await pair(40000000, true); await presented(40000000);

    await pair(40033333);
    await page.waitForFunction(() => window.pigeonGround.snapshot().playout.pending.A > 0);
    await emit({ ...status(true), reset: true });
    await page.waitForTimeout(150);
    assert.equal((await snapshot()).presentation.frame.pts, null, "Reset must invalidate queued presentation currency");
    assert.equal((await snapshot()).presentation.raw.A, 40000000);
    assert.equal(await page.evaluate(() => window.testFrames.every(frame => frame.format === null)), true);
    await geometry("two");
    await pair(0, true); await presented(0);

    // A repeatedly resetting camera must not reanchor its healthy partner's
    // due times. This used to indefinitely postpone B's already queued frames.
    await view("b");
    await emit({ ...status(true), reset: true }); await geometry("three");
    const healthy = await page.evaluate(async () => {
      const base = 1000000, seen = new Set(); let sampling = true;
      const sample = () => { const pts = window.pigeonGround.snapshot().presentation.frame.pts;
        if (pts >= base) seen.add(pts); if (sampling) requestAnimationFrame(sample); };
      requestAnimationFrame(sample);
      await Promise.all([
        ...Array.from({ length: 9 }, (_, i) => new Promise(resolve => setTimeout(() => {
          window.testPacket("B", base + i * 33333, i === 0); resolve();
        }, i * 33.333))),
        ...Array.from({ length: 20 }, (_, i) => new Promise(resolve => setTimeout(() => {
          window.testSocket.onmessage({ data: JSON.stringify({ type: "status", state: "ready",
            source: "udp://synthetic:5000", replay: false, playing: true, generation: 1, reset_camera: "A" }) }); resolve();
        }, i * 20))),
      ]);
      sampling = false;
      return seen.size;
    });
    assert.ok(healthy >= 7, `Repeated A resets must preserve B cadence; only ${healthy} B frames appeared`);
    await emit({ ...status(true), reset: true });
    assert.equal(await page.evaluate(() => window.testFrames.every(frame => frame.format === null)), true);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ checked: ["paired and raw burst cadence", "one visible draw per refresh",
      "late decoder catch-up", "bounded queues", "receipt age", "pause/resume", "reset and backward PTS", "frame closure",
      "metadata-hold queue retirement", "healthy raw camera survives partner metadata hold",
      "unmatched pre-hold frame retirement", "healthy camera survives partner resets"], results }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
