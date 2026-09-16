"use strict";

const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const assert = require("node:assert/strict");

const URL = process.env.SIM_URL || "http://127.0.0.1:8767/docs/";
const OUT = path.resolve(process.env.SIM_CHECK_OUT || path.join(__dirname, "../../build/simulator/checks"));
const REQUIRE_ENCODED = process.env.SIM_REQUIRE_ENCODED === "1";
let stage = "startup";

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    channel: process.env.SIM_BROWSER_CHANNEL || "chrome",
    args: ["--enable-gpu"],
  });
  const deadline = setTimeout(() => {
    console.error(`Simulator check exceeded 90 seconds during ${stage}`);
    process.exitCode = 1;
    browser.close().catch(() => {});
  }, 90000);
  const page = await browser.newPage({ viewport: { width: 1400, height: 1050 } });
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (message) => {
    // A missing optional clip is an expected model fallback. JS exceptions and
    // the simulator's visible error message are checked separately below.
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) errors.push(message.text());
  });
  // Record actual video-frame callback timestamps independently of app state.
  // This verifies that each displayed frame index came from both decoders.
  await page.addInitScript(() => {
    window.__videoFrames = { "received": [], "received-b": [] };
    const original = HTMLVideoElement.prototype.requestVideoFrameCallback;
    if (!original) return;
    HTMLVideoElement.prototype.requestVideoFrameCallback = function (callback) {
      return original.call(this, (now, metadata) => {
        const list = window.__videoFrames[this.id];
        if (list) list.push({ pts: metadata.mediaTime, frames: metadata.presentedFrames, now });
        callback(now, metadata);
      });
    };
  });
  const digest = (selector) => page.locator(selector).evaluate((canvas) => {
    const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    let hash = 2166136261;
    const colors = new Set();
    for (let y = 1; y < canvas.height; y += Math.max(1, Math.floor(canvas.height / 31))) {
      for (let x = 1; x < canvas.width; x += Math.max(1, Math.floor(canvas.width / 37))) {
        const i = 4 * (y * canvas.width + x);
        const rgb = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
        hash = Math.imul(hash ^ rgb, 16777619);
        colors.add(rgb);
      }
    }
    return { hash: hash >>> 0, colors: colors.size, width: canvas.width, height: canvas.height };
  });
  const choose = (selector, value) => page.selectOption(selector, String(value));
  const seek = async (time) => {
    await page.evaluate((t) => window.pigeon.seek(t), time);
    await page.waitForFunction(() => !window.pigeon.state.loading && !window.pigeon.state.playing);
  };
  const ensureClean = async () => {
    assert.equal(await page.locator("#error").isVisible(), false, await page.locator("#error").textContent());
    assert.deepEqual(errors, [], "Browser exceptions");
    assert.equal(await page.evaluate(() => window.pigeon.renderer.gl.getError()), 0, "WebGL error");
  };
  const report = { url: URL, encoded_required: REQUIRE_ENCODED, checks: [] };
  try {
    await page.goto(URL, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.simReady && window.pigeon?.state?.frameState);
    const initial = await page.evaluate(() => ({
      scenario: window.renderSequenceMetadata(),
      target: window.flightData.inputs.target_apogee_m,
      manifest: window.pigeon.state.manifest,
    }));
    stage = "selected optics";
    assert.equal(initial.target, 3048, "Default flight must be 10,000 ft AGL");
    assert.equal(initial.scenario.camera.lens, "CIL212");
    assert.equal(initial.scenario.camera.crop_px, 1552);
    assert.equal(initial.scenario.fps, 30);
    assert.equal(initial.scenario.video.per_camera_mbps, 4);
    assert.equal(await page.locator("#preset").inputValue(), "900");
    report.checks.push("CIL212 / 1552-square / 30 fps / 10,000 ft defaults");

    stage = "model camera views";
    await page.evaluate(() => window.pigeon.setSource("model"));
    await seek(15);
    assert.ok(Math.abs(await page.evaluate(() => window.pigeon.state.time) - 15) < 1e-6);
    assert.match(await page.locator("#a-spec").textContent(), /1552.*1552/);
    const a = await digest("#raw-a"), b = await digest("#raw-b");
    assert.ok(a.colors > 10 && b.colors > 10, "Source previews are blank or uniform");
    assert.notEqual(a.hash, b.hash, "A and B source images alias the same camera");
    report.model_sources = { a, b };
    for (const mode of [0, 1, 2, 3, 4, 5, 7]) {
      await choose("#view-mode", mode);
      await page.waitForFunction((m) => window.pigeon.state.mode === m, mode);
      await ensureClean();
    }
    for (const policy of [0, 1, 2, 3]) {
      await choose("#seam-policy", policy);
      await page.waitForFunction((p) => window.pigeon.state.policy === p, policy);
      await ensureClean();
    }
    await choose("#view-mode", 0);
    await choose("#seam-policy", 0);
    await page.screenshot({ path: path.join(OUT, "model-desktop.png"), fullPage: true });
    report.checks.push("distinct nonblank A/B, all seven projections, four seam policies");

    stage = "model playback and seek";
    await seek(5);
    await page.click("#play");
    await page.waitForFunction(() => window.pigeon.state.time >= 5.2);
    await page.click("#play");
    assert.equal(await page.evaluate(() => window.pigeon.state.playing), false);
    await seek(2);
    assert.ok(Math.abs(await page.evaluate(() => window.pigeon.state.time) - 2) < 1e-6, "Backward seek failed");
    const deployment = await page.evaluate(() => window.flightData.summary.deployment_time_s);
    await seek(deployment + 1);
    await page.click('[data-look="canopy"]');
    await ensureClean();
    report.checks.push("model playback, backward seek, deployment look-up");

    const encodedButton = page.locator('[data-source="received"]');
    const encodedAvailable = !!initial.manifest && !(await encodedButton.isDisabled());
    report.encoded = { tested: encodedAvailable };
    if (!encodedAvailable) {
      assert.equal(REQUIRE_ENCODED, false, "SIM_REQUIRE_ENCODED=1, but matching paired clips are unavailable");
      assert.equal(await encodedButton.isDisabled(), true, "Unavailable encoded mode must be disabled");
      report.encoded.reason = "No matching v3 clips; encoded checks skipped explicitly.";
    } else {
      stage = "paired encoded manifest";
      const m = initial.manifest;
      assert.equal(m.pipeline_version, 3);
      assert.equal(m.camera_scope, "two-fisheyes-ground-stitch");
      assert.equal(m.parameters.capture.id, initial.scenario.id);
      assert.equal(m.crop_px, 1552);
      assert.equal(m.fps, 30);
      for (const camera of [m.cameras.a, m.cameras.b]) {
        assert.equal(camera.fps, 30);
        assert.equal(camera.frames, m.frames, "Camera frame count differs from paired sequence");
        assert.equal(camera.decoded_frames, m.frames, "Generator did not verify every camera frame");
      }
      assert.ok(m.frames >= 60, "Encoded test needs at least two seconds of paired video");
      assert.notEqual(m.cameras.a.mp4, m.cameras.b.mp4, "Both eyes reference one video file");
      const offset = m.start_s || 0;
      await seek(offset);
      await page.evaluate(() => window.pigeon.setSource("received"));
      await page.waitForFunction(() => window.pigeon.state.source === "received" && !window.pigeon.state.loading);
      const dimensions = await page.evaluate(() => ["received", "received-b"].map((id) => {
        const v = document.getElementById(id);
        return [v.videoWidth, v.videoHeight];
      }));
      assert.deepEqual(dimensions, [[1552, 1552], [1552, 1552]], "Encoded camera raster mismatch");
      const durations = await page.evaluate(() => ["received", "received-b"].map((id) => document.getElementById(id).duration));
      assert.ok(durations.every((duration) => Math.abs(duration - m.frames / 30) < 0.04), "Encoded duration differs from 30 fps frame count");

      stage = "paired encoded seeks";
      for (const index of [0, Math.min(45, m.frames - 2), 7]) {
        await seek(offset + index / 30);
        const s = await page.evaluate(() => ({
          state: window.pigeon.state,
          pts: ["received", "received-b"].map((id) => document.getElementById(id).currentTime),
        }));
        assert.equal(s.state.source, "received", "Encoded seek fell back to model");
        assert.equal(s.state.pairedFrame, index, "Seek displayed wrong paired frame index");
        assert.ok(Math.abs(s.state.time - (offset + index / 30)) < 1e-6, "Frame index and flight time differ");
        assert.ok(s.pts.every((pts) => Math.abs(pts - index / 30) < 0.005), "A/B seek timestamps differ");
      }
      const ea = await digest("#raw-a"), eb = await digest("#raw-b");
      assert.notEqual(ea.hash, eb.hash, "Decoded eyes are identical");
      assert.ok(ea.colors > 10 && eb.colors > 10, "Decoded camera image is blank");

      stage = "paired encoded panning";
      const before = await page.locator("#screen").screenshot();
      const frameBefore = await page.evaluate(() => window.pigeon.state.pairedFrame);
      await page.click('[data-look="horizon"]');
      const after = await page.locator("#screen").screenshot();
      assert.equal(await page.evaluate(() => window.pigeon.state.pairedFrame), frameBefore, "Panning advanced the capture frame");
      assert.ok(!before.equals(after), "Panning did not change the viewport");

      stage = "paired encoded playback";
      await seek(offset);
      await choose("#rate", 1);
      await page.evaluate(() => {
        window.pigeon.diagnostics.decodedTimestamps.length = 0;
        window.__videoFrames.received.length = 0;
        window.__videoFrames["received-b"].length = 0;
      });
      await page.click("#play");
      await page.waitForFunction(() => window.pigeon.diagnostics.decodedTimestamps.length >= 15);
      await page.click("#play");
      const trace = await page.evaluate(() => ({
        paired: window.pigeon.diagnostics.decodedTimestamps,
        videos: window.__videoFrames,
        state: { source: window.pigeon.state.source, time: window.pigeon.state.time, playing: window.pigeon.state.playing },
      }));
      assert.equal(trace.state.source, "received");
      assert.equal(trace.state.playing, false);
      const indices = trace.paired.map(([pts, stateTime]) => {
        assert.ok(Math.abs(pts - stateTime) < 1e-6, "Decoded capture time differs from stabilization/flight time");
        const index = (pts - offset) * 30;
        assert.ok(Math.abs(index - Math.round(index)) < 1e-6, "Paired presentation time is not on the 30 fps grid");
        return Math.round(index);
      });
      const eyeIndices = Object.values(trace.videos).map((list) => new Set(list.map(({ pts }) => Math.round(pts * 30))));
      assert.ok(indices.every((index) => eyeIndices.every((eye) => eye.has(index))), "Displayed pair lacks an exact matching frame callback from both eyes");
      assert.ok(indices.every((index, i) => !i || index > indices[i - 1]), "Paired frames repeat or run backward");
      assert.ok(trace.state.time >= offset + 0.45, "Paired playback did not progress");
      report.encoded = {
        tested: true, dimensions, paired_indices: indices,
        skipped_capture_frames: indices.slice(1).reduce((n, index, i) => n + index - indices[i] - 1, 0),
        exact_dual_decoder_matches: true,
      };
      await page.screenshot({ path: path.join(OUT, "encoded-desktop.png"), fullPage: true });
      report.checks.push("v3 paired clips, exact seek indices, independent eyes, paused pan, synchronized 30 fps timestamp grid");
    }

    stage = "settings and alternative flight";
    await page.evaluate(() => window.pigeon.setSource("model"));
    await page.locator("#stand").evaluate((e) => {
      e.value = "12";
      e.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.waitForFunction(() => window.pigeon.state.params.stand === 12 && window.pigeon.state.source === "model");
    await choose("#scenario", "launch-30kft.json");
    await page.waitForFunction(() => window.flightData.inputs.target_apogee_m === 9144 && window.pigeon.state.source === "model");
    assert.equal(await encodedButton.isDisabled(), true);
    await choose("#scenario", "launch.json");
    await page.waitForFunction(() => window.flightData.inputs.target_apogee_m === 3048);
    report.checks.push("geometry control and alternate flight force model mode");

    stage = "mobile layout";
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => new Promise(requestAnimationFrame));
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "390 px viewport has horizontal overflow");
    await page.screenshot({ path: path.join(OUT, "model-mobile.png"), fullPage: true });
    await ensureClean();
    report.checks.push("390 px mobile layout and no browser/WebGL errors");
    report.screenshots = OUT;
    fs.writeFileSync(path.join(OUT, "result.json"), JSON.stringify(report, null, 2) + "\n");
    console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    console.error(`Simulator check failed during ${stage}: ${error.message}`);
    await page.screenshot({ path: path.join(OUT, "failure.png"), fullPage: true }).catch(() => {});
    fs.writeFileSync(path.join(OUT, "failure.json"), JSON.stringify({ stage, error: error.stack, browser_errors: errors }, null, 2));
    process.exitCode = 1;
  } finally {
    clearTimeout(deadline);
    await browser.close();
  }
})().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
