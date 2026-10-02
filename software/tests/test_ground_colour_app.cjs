// Synthetic calibration and mocked WebSocket only. Never connects to the
// operator's ground receiver or sends camera controls.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const assets = path.resolve(__dirname, "../python/pigeonvision/ground/static");
(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--enable-gpu"] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const failures = [];
    page.on("pageerror", error => failures.push(error.message));
    await page.route("http://127.0.0.1:8773/**", route => {
      const resource = new URL(route.request().url()).pathname;
      const file = resource === "/" ? "index.html" : resource.replace(/^\/static\//, "");
      if (file.includes("..") || !fs.existsSync(path.join(assets, file))) return route.abort();
      const contentType = file.endsWith(".js") ? "application/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".ttf") ? "font/ttf" : file.endsWith(".png") ? "image/png" : "text/html";
      return route.fulfill({ body: fs.readFileSync(path.join(assets, file)), contentType });
    });
    await page.addInitScript(() => {
      // Produce real solid-color VideoFrames without a camera or H.264 source.
      window.testHeldDecodeOutputs = [];
      window.VideoDecoder = class {
        static async isConfigSupported(config) { return { supported: true, config }; }
        constructor({ output }) { this.output = output; this.state = "unconfigured"; this.decodeQueueSize = 0; }
        configure() { this.state = "configured"; }
        close() { this.state = "closed"; }
        decode(chunk) {
          const pixels = new Uint8Array(chunk.byteLength); chunk.copyTo(pixels);
          const source = new OffscreenCanvas(256, 256), context = source.getContext("2d");
          context.fillStyle = `rgb(${[...pixels].join(",")})`; context.fillRect(0, 0, 256, 256);
          const frame = new VideoFrame(source, { timestamp: chunk.timestamp });
          window.testDecodedFrame = frame;
          if (window.testHoldDecodeOutputs) window.testHeldDecodeOutputs.push(() => this.output(frame));
          else queueMicrotask(() => { if (this.state === "closed") frame.close(); else this.output(frame); });
        }
      };
      const ResizeObserver = window.ResizeObserver;
      window.ResizeObserver = class extends ResizeObserver {
        constructor(callback) { super(callback); window.testRedraw = callback; }
      };
      window.socketCount = 0;
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; window.testSocket = this; window.socketCount++; queueMicrotask(() => { this.onopen?.(); this.onmessage?.({ data: JSON.stringify({ type: "control_owner", can_control: true }) }); }); }
        send(message) { if (JSON.parse(message).type !== "view") throw Error("Only ground view publications are expected in this fixture"); }
        close() { this.readyState = 3; this.onclose?.(); }
      };
    });
    await page.goto("http://127.0.0.1:8773/");
    await page.waitForFunction(() => window.pigeonGround?.snapshot().connected && window.pigeonGround.snapshot().station.controlling);
    await page.locator("#controls-toggle").click();
    await page.locator("#operator-controls").waitFor({ state: "visible" });
    await page.locator("#advanced-controls > summary").click();
    const emit = value => page.evaluate(value => window.testSocket.onmessage({ data: JSON.stringify(value) }), value);
    const snapshot = () => page.evaluate(() => window.pigeonGround.snapshot());
    const strength = async (name, percent) => page.locator("#colour-strength-" + name.toLowerCase()).evaluate((input, value) => {
      input.value = String(value); input.dispatchEvent(new Event("input", { bubbles: true }));
    }, percent);
    const gainsNear = (state, name, expected) => expected.forEach((value, index) =>
      assert.ok(Math.abs(state.colourGains[name][index] - value) < 1e-12,
        `${name}: ${state.colourGains[name]} expected ${expected}`));
    const camera = name => ({ image_size: [256,256], crop: [0,0,256,256], output_size: [256,256],
      K: [[128,0,127.5],[0,128,127.5],[0,0,1]], D: [0,0,0,0], xi: 1,
      flip_x: false, flip_y: true, max_theta_deg: null,
      R_camera_from_rig: name === "A" ? [[1,0,0],[0,1,0],[0,0,1]] : [[-1,0,0],[0,1,0],[0,0,-1]],
      provenance: { device_id: `synthetic-${name}` } });
    const profile = { schema_version: 1, model: "mei", rig_alignment_status: "synthetic_test_only",
      cameras: { A: camera("A"), B: camera("B") },
      display_colour: { schema_version: 1, method: "display_rgb_gain", reference_camera: "A",
        gains: { A: [1,1,1], B: [1.05,.98,1.08] }, devices: { A: "synthetic-A", B: "synthetic-B" } } };
    const descriptions = () => ["A", "B"].map(id => ({ id, device: `synthetic-${id}`,
      sensor_size: [256,256], width: 256, height: 256, flip_x: false, flip_y: true }));
    const geometry = async (alter = value => value, session_id = undefined) => {
      await emit({ type: "metadata", record: { type: "session", session_id, cameras: alter(descriptions()) } });
      for (const camera_id of ["A", "B"])
        await emit({ type: "metadata", record: { type: "frame", camera_id, sensor_crop: [0,0,256,256] } });
    };
    await emit({ type: "calibration", calibration: profile });
    assert.equal((await snapshot()).colourEnabled, false);
    assert.equal(await page.locator("#colour-controls").isVisible(), true);
    assert.equal(await page.locator("#colour-toggle").isDisabled(), true);
    for (const name of ["a", "b"])
      assert.equal(await page.locator("#colour-strength-" + name).isDisabled(), true);
    await strength("B", 0);
    assert.equal((await snapshot()).colourStrength.B, 1, "Unverified geometry must gate strength changes too");
    await geometry();
    assert.equal((await snapshot()).colourEnabled, true);
    assert.equal(await page.locator("#colour-toggle").getAttribute("aria-pressed"), "true");
    assert.match(await page.locator("#colour-status").textContent(), /Matched to camera A/);
    assert.deepEqual((await snapshot()).colourStrength, { A: 1, B: 1 });
    for (const name of ["a", "b"])
      assert.equal(await page.locator("#colour-strength-" + name).isDisabled(), false);
    await strength("B", 50);
    gainsNear(await snapshot(), "B", [1.025, .99, 1.04]);
    gainsNear(await snapshot(), "A", [1, 1, 1]);
    assert.equal(await page.locator("#colour-strength-value-b").textContent(), "50%");
    await strength("B", 0);
    gainsNear(await snapshot(), "B", [1, 1, 1]);
    await strength("B", 35);
    const chosen = await snapshot();
    gainsNear(chosen, "B", profile.display_colour.gains.B.map(gain => 1 + .35 * (gain - 1)));
    await page.locator("#colour-toggle").click();
    assert.equal((await snapshot()).colourEnabled, false);
    assert.match(await page.locator("#colour-status").textContent(), /Original camera colours/);

    // Real app reconnect sequence: socket close clears identity, then the server
    // sends reset + the same calibration before fresh native session metadata.
    await page.evaluate(() => window.testSocket.close());
    assert.equal((await snapshot()).colourEnabled, false);
    await page.waitForFunction(() => window.socketCount === 2 && window.pigeonGround.snapshot().connected);
    await emit({ type: "status", source: "udp://synthetic:1234", state: "ready", replay: false, playing: true, reset: true });
    await emit({ type: "calibration", calibration: profile });
    assert.equal((await snapshot()).colourEnabled, false);
    assert.equal(await page.locator("#colour-toggle").isDisabled(), true);
    assert.equal(await page.locator("#colour-strength-b").isDisabled(), true);
    await geometry();
    assert.equal((await snapshot()).colourEnabled, false, "Original-colour preference must survive profile replay");
    assert.deepEqual((await snapshot()).colourStrength, chosen.colourStrength, "Strength preferences must survive profile replay");
    assert.deepEqual((await snapshot()).colourGains, chosen.colourGains);
    await page.locator("#colour-toggle").click();
    assert.equal((await snapshot()).colourEnabled, true);

    // Wrong physical identity gates preview correction. The preference remains
    // on, so verified original hardware can restore it without a second click.
    await geometry(cameras => { cameras[1].device = "synthetic-other-camera"; return cameras; });
    assert.equal((await snapshot()).colourEnabled, false);
    assert.equal(await page.locator("#colour-toggle").isDisabled(), true);
    assert.equal(await page.locator("#colour-strength-a").isDisabled(), true);
    assert.equal(await page.locator("#colour-strength-b").isDisabled(), true);
    assert.match((await snapshot()).geometry.errors.join(" "), /physical camera ID/);
    await geometry();
    assert.equal((await snapshot()).colourEnabled, true);
    await emit({ type: "metadata", record: { type: "frame", camera_id: "B", sensor_crop: [1,0,255,256] } });
    assert.equal((await snapshot()).colourEnabled, false);
    await geometry();
    assert.equal((await snapshot()).colourEnabled, true);
    await page.locator("#home").click();
    assert.equal((await snapshot()).colourEnabled, true, "Reset view must preserve colour choice");
    assert.deepEqual((await snapshot()).colourStrength, chosen.colourStrength, "Reset view must preserve strengths");
    await emit({ type: "status", source: "udp://synthetic:1234", state: "ready", replay: false, playing: true, reset: true });
    assert.equal((await snapshot()).colourEnabled, false, "Session reset must require fresh identity and crop");
    await geometry();
    assert.equal((await snapshot()).colourEnabled, true);

    const neutral = structuredClone(profile);
    neutral.display_colour.reference_camera = null;
    neutral.display_colour.reference_target = "colorchecker_neutrals";
    neutral.display_colour.gains = { A: [1.08,1,.96], B: [1.12,1,1.01] };
    await emit({ type: "calibration", calibration: neutral });
    assert.equal((await snapshot()).colourEnabled, true);
    assert.match(await page.locator("#colour-status").textContent(), /Neutral balance/);
    neutral.display_colour.reference_target = "measured_neutral_surfaces";
    await emit({ type: "calibration", calibration: neutral });
    assert.equal((await snapshot()).colourEnabled, true);
    assert.match(await page.locator("#colour-status").textContent(), /Neutral balance/);

    await emit({ type: "calibration", calibration: null });
    assert.equal((await snapshot()).colourEnabled, false);
    assert.equal(await page.locator("#colour-controls").isVisible(), false);
    gainsNear(await snapshot(), "A", [1, 1, 1]);
    gainsNear(await snapshot(), "B", [1, 1, 1]);

    // A new neutral profile seeds explicit strengths. Lowering balance leaves
    // the same common headroom scalar, unlike the true-original global toggle.
    const scale = .8815789473684211;
    neutral.display_colour.common_headroom_scale = scale;
    neutral.display_colour.gains = { A: [.9760338345864663, scale, .8231610653138871], B: [1, scale, .826822491010134] };
    neutral.display_colour.camera_strengths = { A: 1, B: .5 };
    const interpolate = (name, amount) => neutral.display_colour.gains[name].map(gain => scale + amount * (gain - scale));
    await emit({ type: "calibration", calibration: neutral });
    await geometry();
    assert.deepEqual((await snapshot()).colourStrength, { A: 1, B: .5 });
    gainsNear(await snapshot(), "A", neutral.display_colour.gains.A);
    gainsNear(await snapshot(), "B", interpolate("B", .5));
    assert.equal(await page.locator("#colour-strength-value-b").textContent(), "50%");
    assert.match(await page.locator("#colour-strength-hint").textContent(), /keeps the preview dimming/);
    await strength("A", 0);
    gainsNear(await snapshot(), "A", [scale, scale, scale]);
    await strength("B", 25);
    gainsNear(await snapshot(), "B", interpolate("B", .25));
    assert.ok(Object.values((await snapshot()).colourGains).flat().every(value => value <= 1), "Interpolation must preserve no-added-clipping headroom");
    const headroomChoice = await snapshot();
    await page.locator("#colour-toggle").click();
    assert.equal((await snapshot()).colourEnabled, false, "Toggle must still bypass all preview gains");
    assert.deepEqual((await snapshot()).colourGains, headroomChoice.colourGains);
    await page.evaluate(() => window.testSocket.close());
    await page.waitForFunction(() => window.socketCount === 3 && window.pigeonGround.snapshot().connected);
    await emit({ type: "status", source: "udp://synthetic:1234", state: "ready", replay: false, playing: true, reset: true });
    await emit({ type: "calibration", calibration: neutral });
    await geometry();
    assert.equal((await snapshot()).colourEnabled, false);
    assert.deepEqual((await snapshot()).colourStrength, headroomChoice.colourStrength);
    assert.deepEqual((await snapshot()).colourGains, headroomChoice.colourGains, "Reconnect must not reset strengths to profile defaults");
    await page.locator("#colour-toggle").click();
    assert.equal((await snapshot()).colourEnabled, true);
    assert.deepEqual((await snapshot()).colourGains, headroomChoice.colourGains);

    await emit({ type: "calibration", calibration: { ...profile, display_colour: undefined } });
    await geometry();
    assert.equal((await snapshot()).colourEnabled, false, "A lens bundle alone must never turn matching on");
    assert.equal(await page.locator("#colour-controls").isVisible(), false);
    gainsNear(await snapshot(), "A", [1, 1, 1]);
    gainsNear(await snapshot(), "B", [1, 1, 1]);
    assert.deepEqual(failures, []);

    // Runtime recovery invalidates NEW content, but must not recolor verified
    // pixels already held in the raw and paired textures during a redraw.
    const flashProfile = structuredClone(profile);
    flashProfile.display_colour.gains.B = [1, .75, .5];
    flashProfile.cameras.B.R_camera_from_rig = flashProfile.cameras.A.R_camera_from_rig;
    await emit({ type: "calibration", calibration: flashProfile });
    await geometry();
    const view = async mode => emit({ type: "view", initial: true,
      view: { mode, yaw: 0, pitch: 0, roll: 0, fov: 1.5 } });
    const frame = async (camera_id, timestamp_us, pixels = [90, 120, 180]) => {
      const before = (await snapshot()).decoded[camera_id];
      await page.evaluate(({ camera_id, timestamp_us, pixels }) => {
        const header = new TextEncoder().encode(JSON.stringify({ type: "frame", camera_id,
          timestamp_us, keyframe: true, codec: "avc1.42001f" }));
        const packet = new Uint8Array(4 + header.length + 3);
        new DataView(packet.buffer).setUint32(0, header.length);
        packet.set(header, 4); packet.set(pixels, 4 + header.length);
        window.testSocket.onmessage({ data: packet.buffer });
      }, { camera_id, timestamp_us, pixels });
      await page.waitForFunction(({ camera_id, before }) => window.pigeonGround.snapshot().decoded[camera_id] > before,
        { camera_id, before });
    };
    const pixel = () => page.evaluate(() => {
      window.testRedraw();
      const canvas = document.getElementById("image"), gl = canvas.getContext("webgl2"), pixel = new Uint8Array(4);
      gl.readPixels(Math.floor(canvas.width / 2), Math.floor(canvas.height / 2), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
      return [...pixel].slice(0, 3);
    });
    const near = (actual, expected, message) => expected.forEach((value, index) =>
      assert.ok(Math.abs(actual[index] - value) <= 1, `${message}: ${actual} expected ${expected}`));
    await view("perspective");
    await page.locator("#seam").evaluate(input => {
      input.value = "3"; input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await frame("A", 1000000); await frame("B", 1000000);
    await view("b"); near(await pixel(), [90, 90, 90], "Verified raw frame is corrected");
    await view("perspective"); near(await pixel(), [90, 90, 90], "Verified paired frame is corrected");
    await emit({ type: "status", source: "udp://synthetic:1234", state: "ready", replay: false, playing: true, reset: true });
    assert.equal((await snapshot()).colourEnabled, false, "Current metadata eligibility must still reset");
    near(await pixel(), [90, 90, 90], "Reset must preserve the held pair's correction");
    await view("b"); near(await pixel(), [90, 90, 90], "Reset must preserve the held raw frame's correction");
    await page.evaluate(() => window.testSocket.close());
    near(await pixel(), [90, 90, 90], "Disconnected redraw must keep held correction");
    await page.waitForFunction(() => window.socketCount === 4 && window.pigeonGround.snapshot().connected);
    await emit({ type: "status", source: "udp://synthetic:1234", state: "ready", replay: false, playing: true, reset: true });
    await emit({ type: "calibration", calibration: flashProfile });
    near(await pixel(), [90, 90, 90], "Same-profile reconnect must keep held correction");
    const beforeRecovery = await snapshot();
    await frame("A", 2000000, [120, 160, 240]); await frame("B", 2000000, [120, 160, 240]);
    near(await pixel(), [90, 90, 90], "Reconnect must hold the corrected raw image while metadata is missing");
    assert.equal(await page.evaluate(() => window.testDecodedFrame.format), null, "Withheld decoded frames must close");
    const heldRecovery = await snapshot();
    assert.equal(heldRecovery.pairs, beforeRecovery.pairs, "Unknown frames must not form a new pair");
    assert.equal(heldRecovery.colourHeldFrames.B, beforeRecovery.colourHeldFrames.B + 1,
      "Recovery diagnostics must count withheld frames");
    assert.deepEqual(heldRecovery.pending, { A: 0, B: 0 });
    assert.equal(heldRecovery.presentation.raw.B, 1000000, "Raw presentation PTS must remain with the held pixels");
    assert.equal(heldRecovery.presentation.frame.pts, null, "Held pixels must not advance current-session telemetry");
    assert.ok(heldRecovery.presentation.frame.frameAgeMs > beforeRecovery.presentation.frame.frameAgeMs,
      "Continued decoding must not refresh the held image age");
    await page.waitForFunction(() => /Waiting for camera metadata.*image held/.test(document.getElementById("overlay").textContent));
    await view("perspective");
    near(await pixel(), [90, 90, 90], "Reconnect must also hold the corrected paired image");
    assert.equal((await snapshot()).presentation.pair.b, 1000000);
    await geometry();
    near(await pixel(), [90, 90, 90], "Later metadata must not present discarded unknown content");
    assert.equal((await snapshot()).presentation.frame.pts, null, "Metadata alone cannot resume presentation");
    await frame("A", 3000000); await frame("B", 3000000);
    near(await pixel(), [90, 90, 90], "New verified content restores correction");
    assert.equal((await snapshot()).presentation.frame.pts, 3000000);
    await view("b");
    await page.locator("#colour-toggle").click();
    near(await pixel(), [90, 120, 180], "The original-color choice must bypass held correction");
    await page.locator("#colour-toggle").click();
    await geometry(cameras => { cameras[1].device = "synthetic-other-camera"; return cameras; });
    await view("b");
    await frame("B", 4000000);
    await view("b");
    near(await pixel(), [90, 120, 180], "New mismatched-camera content must use original colors");
    await geometry(); await view("b"); await frame("B", 5000000);
    near(await pixel(), [90, 90, 90], "Matching hardware can verify a subsequent new frame");
    const recoveryStatus = { type: "status", source: "udp://synthetic:1234", generation: 7,
      state: "ready", replay: false, playing: true };
    await emit(recoveryStatus);
    await emit({ ...recoveryStatus, reset: true, preserve_geometry: true });
    assert.equal((await snapshot()).colourEnabled, true, "Same-stream queue recovery must preserve capture geometry");
    await frame("A", 6000000); await frame("B", 6000000);
    near(await pixel(), [90, 90, 90], "Recovered video must stay corrected before metadata repeats");
    await emit({ ...recoveryStatus, generation: 8, reset: true, preserve_geometry: true });
    assert.equal((await snapshot()).colourEnabled, false, "A new receiver generation cannot preserve capture geometry");
    await frame("B", 7000000, [120, 160, 240]);
    near(await pixel(), [90, 90, 90], "New receiver generation must hold prior corrected pixels until verified");
    assert.equal((await snapshot()).presentation.raw.B, 6000000);
    assert.equal((await snapshot()).presentation.frame.pts, null, "New-generation telemetry cannot use the old displayed PTS");
    await geometry(undefined, "session-one");
    await emit({ ...recoveryStatus, generation: 8, source: "udp://other-source:1234", reset: true, preserve_geometry: true });
    assert.equal((await snapshot()).colourEnabled, false, "A different source cannot preserve capture geometry");
    await geometry(undefined, "session-one");
    await emit({ type: "metadata", record: { type: "session", session_id: "session-two", cameras: descriptions() } });
    assert.equal((await snapshot()).colourEnabled, false, "A new capture session must require fresh per-frame crop evidence");
    assert.match((await snapshot()).geometry.unverified.join(" "), /actual crop/);
    await geometry(undefined, "session-two"); await view("b"); await frame("B", 8000000);
    near(await pixel(), [90, 90, 90], "New-session frame metadata restores correction");
    await emit({ type: "metadata", record: { type: "frame", camera_id: "B", session_id: "session-two", sensor_crop: null, scaler_crop: null } });
    await frame("B", 8100000, [120, 160, 240]);
    near(await pixel(), [90, 90, 90], "An occasional missing crop must hold the verified raw image");
    assert.equal((await snapshot()).presentation.raw.B, 8000000);
    assert.equal((await snapshot()).presentation.frame.pts, null);
    await geometry(undefined, "session-two"); await frame("B", 8200000);
    near(await pixel(), [90, 90, 90], "Fresh verified crop evidence resumes presentation");
    assert.equal((await snapshot()).presentation.frame.pts, 8200000);
    await emit({ type: "metadata", record: { type: "frame", camera_id: "B", session_id: "session-two", sensor_crop: [1,0,255,256] } });
    await frame("B", 8300000, [120, 160, 240]); await view("b");
    near(await pixel(), [120, 160, 240], "An explicit crop mismatch must show new content without correction");
    assert.equal((await snapshot()).presentation.raw.B, 8300000);
    assert.equal((await snapshot()).presentation.heldForMetadata.B, false);
    await geometry(undefined, "session-two"); await view("b"); await frame("B", 8400000);

    // A capture session boundary must retire both queued pairs and delayed
    // decoder callbacks while keeping the previous corrected pixels visible.
    await view("perspective");
    await frame("A", 9000000); await frame("B", 9000000);
    await frame("A", 9030000, [120, 160, 240]);
    const beforeSessionChange = await snapshot();
    assert.deepEqual(beforeSessionChange.pending, { A: 1, B: 0 });
    await page.evaluate(() => { window.testHoldDecodeOutputs = true; });
    // Send an encoded frame without awaiting output, since this callback is
    // deliberately delivered only after the capture session changes.
    await page.evaluate(() => {
      const header = new TextEncoder().encode(JSON.stringify({ type: "frame", camera_id: "B",
        timestamp_us: 9030000, keyframe: true, codec: "avc1.42001f" }));
      const packet = new Uint8Array(4 + header.length + 3);
      new DataView(packet.buffer).setUint32(0, header.length);
      packet.set(header, 4); packet.set([120, 160, 240], 4 + header.length);
      window.testSocket.onmessage({ data: packet.buffer });
    });
    await page.waitForFunction(() => window.testHeldDecodeOutputs.length === 1);
    await geometry(undefined, "session-three");
    const changedSession = await snapshot();
    assert.deepEqual(changedSession.pending, { A: 0, B: 0 }, "Session changes must close queued old frames");
    assert.equal(changedSession.presentation.frame.pts, null);
    assert.equal(changedSession.presentation.pair.a, 9000000, "Held pair pixels must retain their original timestamp");
    assert.ok(changedSession.presentation.frame.frameAgeMs >= beforeSessionChange.presentation.frame.frameAgeMs,
      "Session changes must retain the held image age");
    near(await pixel(), [90, 90, 90], "Session changes must retain the corrected held pair");
    await page.evaluate(() => {
      window.testHoldDecodeOutputs = false;
      window.testHeldDecodeOutputs.shift()();
    });
    assert.equal(await page.evaluate(() => window.testDecodedFrame.format), null,
      "Late old-session decoder outputs must close without presentation");
    assert.deepEqual((await snapshot()).decoded, changedSession.decoded,
      "Old-session outputs must not enter the current decoder accounting");
    await frame("B", 9030000);
    assert.equal((await snapshot()).pairs, beforeSessionChange.pairs,
      "The first new B cannot pair with the retired old A");
    assert.equal((await snapshot()).presentation.frame.pts, null);
    await frame("A", 9030000);
    assert.equal((await snapshot()).presentation.frame.pts, 9030000,
      "A fresh pair from both new-session decoders resumes presentation");
    await view("b");
    const otherCamera = structuredClone(flashProfile);
    otherCamera.cameras.B.provenance.device_id = "synthetic-other-camera";
    otherCamera.display_colour.devices.B = "synthetic-other-camera";
    await emit({ type: "calibration", calibration: otherCamera });
    await view("b");
    near(await pixel(), [90, 120, 180], "A different calibration cannot reuse held verification");
    await emit({ type: "status", source: "udp://other-source:1234", generation: 8, state: "ready", replay: false, playing: true, reset: true });
    await frame("B", 8500000, [120, 160, 240]);
    near(await pixel(), [120, 160, 240], "A changed calibration cannot hold pixels verified for the prior camera");
    assert.deepEqual(failures, []);
    console.log(JSON.stringify({ passed: true, checked: ["missing-identity-gate", "original-colour-toggle", "reconnect-preference", "physical-identity-mismatch", "crop-mismatch", "session-reset", "view-reset", "profile-removal", "no-profile-default", "independent-strengths", "identity-baseline", "shared-headroom-baseline", "profile-strength-defaults", "headroom-reconnect-preference", "held-raw-colour", "held-paired-colour", "unverified-recovery-hold", "held-presentation-age-and-pts", "withheld-frame-close-and-no-pair", "mismatched-new-frame-gate", "same-stream-recovery", "new-source-generation-hold", "new-session-crop-gate", "transient-missing-crop-hold", "explicit-crop-mismatch", "session-queued-frame-retirement", "session-delayed-output-retirement", "held-frame-profile-identity"] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
