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
    await page.route("http://127.0.0.1:9876/**", route => {
      const resource = new URL(route.request().url()).pathname;
      const file = resource === "/" ? "index.html" : resource.replace(/^\/static\//, "");
      if (file.includes("..") || !fs.existsSync(path.join(assets, file))) return route.abort();
      const contentType = file.endsWith(".js") ? "application/javascript" : file.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ body: fs.readFileSync(path.join(assets, file)), contentType });
    });
    await page.addInitScript(() => {
      window.socketCount = 0;
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; window.testSocket = this; window.socketCount++; queueMicrotask(() => this.onopen?.()); }
        send() { throw Error("No outbound controls are expected in this offline fixture"); }
        close() { this.readyState = 3; this.onclose?.(); }
      };
    });
    await page.goto("http://127.0.0.1:9876/");
    await page.waitForFunction(() => window.pigeonGround?.snapshot().connected);
    const emit = value => page.evaluate(value => window.testSocket.onmessage({ data: JSON.stringify(value) }), value);
    const snapshot = () => page.evaluate(() => window.pigeonGround.snapshot());
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
    const geometry = async (alter = value => value) => {
      await emit({ type: "metadata", record: { type: "session", cameras: alter(descriptions()) } });
      for (const camera_id of ["A", "B"])
        await emit({ type: "metadata", record: { type: "frame", camera_id, sensor_crop: [0,0,256,256] } });
    };
    await emit({ type: "calibration", calibration: profile });
    assert.equal((await snapshot()).colourEnabled, false);
    assert.equal(await page.locator("#colour-controls").isVisible(), true);
    assert.equal(await page.locator("#colour-toggle").isDisabled(), true);
    await geometry();
    assert.equal((await snapshot()).colourEnabled, true);
    assert.equal(await page.locator("#colour-toggle").getAttribute("aria-pressed"), "true");
    assert.match(await page.locator("#colour-status").textContent(), /Matched to camera A/);
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
    await geometry();
    assert.equal((await snapshot()).colourEnabled, false, "Original-colour preference must survive profile replay");
    await page.locator("#colour-toggle").click();
    assert.equal((await snapshot()).colourEnabled, true);

    // Wrong physical identity gates preview correction. The preference remains
    // on, so verified original hardware can restore it without a second click.
    await geometry(cameras => { cameras[1].device = "synthetic-other-camera"; return cameras; });
    assert.equal((await snapshot()).colourEnabled, false);
    assert.equal(await page.locator("#colour-toggle").isDisabled(), true);
    assert.match((await snapshot()).geometry.errors.join(" "), /physical camera ID/);
    await geometry();
    assert.equal((await snapshot()).colourEnabled, true);
    await emit({ type: "metadata", record: { type: "frame", camera_id: "B", sensor_crop: [1,0,255,256] } });
    assert.equal((await snapshot()).colourEnabled, false);
    await geometry();
    assert.equal((await snapshot()).colourEnabled, true);
    await page.locator("#home").click();
    assert.equal((await snapshot()).colourEnabled, true, "Reset view must preserve colour choice");
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
    assert.match(await page.locator("#colour-status").textContent(), /Neutral chart balance/);

    await emit({ type: "calibration", calibration: null });
    assert.equal((await snapshot()).colourEnabled, false);
    assert.equal(await page.locator("#colour-controls").isVisible(), false);
    await emit({ type: "calibration", calibration: { ...profile, display_colour: undefined } });
    await geometry();
    assert.equal((await snapshot()).colourEnabled, false, "A lens bundle alone must never turn matching on");
    assert.equal(await page.locator("#colour-controls").isVisible(), false);
    assert.deepEqual(failures, []);
    console.log(JSON.stringify({ passed: true, checked: ["missing-identity-gate", "original-colour-toggle", "reconnect-preference", "physical-identity-mismatch", "crop-mismatch", "session-reset", "view-reset", "profile-removal", "no-profile-default"] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
