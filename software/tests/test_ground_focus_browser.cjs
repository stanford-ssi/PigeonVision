// Offline browser check: routes local assets and replaces WebSocket; never
// connects to the operator's live receiver or changes camera controls.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const assets = path.resolve(__dirname, "../python/pigeonvision/ground/static");
(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--enable-gpu"] });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
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
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; window.testSocket = this; queueMicrotask(() => this.onopen?.()); }
        send() {}
        close() { this.readyState = 3; }
      };
    });
    await page.goto("http://127.0.0.1:9876/");
    await page.waitForFunction(() => window.pigeonGround?.snapshot().connected);
    assert.equal(await page.locator("#perspective-controls").isVisible(), false);
    const emit = value => page.evaluate(value => window.testSocket.onmessage({ data: JSON.stringify(value) }), value);
    await emit({ type: "error", component: "source", recoverable: true, message: "No UDP transport received for two seconds" });
    await page.waitForFunction(() => window.pigeonGround.snapshot().errors?.includes("No UDP"));
    await emit({ type: "status", state: "ready", source: "udp://test:5000", replay: false, playing: true });
    await page.waitForFunction(() => window.pigeonGround.snapshot().errors === null);
    assert.equal(await page.locator("#error").isVisible(), false);
    await emit({ type: "error", component: "transport_recording", recoverable: false, message: "Recording disk full" });
    await emit({ type: "error", component: "source", recoverable: true, message: "Second source timeout" });
    await emit({ type: "status", state: "ready", source: "udp://test:5000", replay: false, playing: true });
    await page.waitForFunction(() => window.pigeonGround.snapshot().errors === "Recording disk full");

    await page.locator("#focus-zoom").fill("4");
    await page.locator("#focus-zoom").dispatchEvent("input");
    assert.equal((await page.evaluate(() => window.pigeonGround.snapshot())).focus.A.zoom, 4);
    assert.equal(await page.locator("#focus-value").textContent(), "4×");
    const box = await page.locator("#image").boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 60, box.y + box.height / 2 + 30);
    await page.mouse.up();
    const moved = await page.evaluate(() => window.pigeonGround.snapshot().focus.A.center);
    assert.ok(moved[0] < .5 && moved[1] < .5);
    await page.locator("#rotate-view").click();
    const rotatedA = await page.evaluate(() => window.pigeonGround.snapshot());
    assert.equal(rotatedA.viewerRotation.A, 180);
    assert.equal(rotatedA.viewerRotation.B, 0);
    assert.deepEqual(rotatedA.focus.A.center, moved);
    assert.equal(rotatedA.focus.A.zoom, 4);
    assert.equal(await page.locator("#rotate-view").getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator("#orientation-value").textContent(), "180°");
    await page.selectOption("#view", "b");
    assert.equal(await page.locator("#orientation-value").textContent(), "0°");
    await page.locator("#rotate-view").click();
    assert.equal(await page.locator("#focus-value").textContent(), "1×");
    await page.locator("#image").hover();
    await page.mouse.wheel(0, -800);
    await page.waitForFunction(() => window.pigeonGround.snapshot().focus.B.zoom > 1);
    await page.locator("#home").click();
    const reset = await page.evaluate(() => window.pigeonGround.snapshot().focus);
    assert.deepEqual(reset.B, { zoom: 1, center: [.5, .5] });
    assert.equal(reset.A.zoom, 4);
    assert.deepEqual(await page.evaluate(() => window.pigeonGround.snapshot().viewerRotation), { A: 180, B: 180 });
    await page.selectOption("#view", "a");
    await page.locator("#rotate-view").click();
    assert.deepEqual(await page.evaluate(() => window.pigeonGround.snapshot().viewerRotation), { A: 0, B: 180 });

    const gpu = await page.evaluate(async () => {
      const { Renderer } = await import("/static/projection.js");
      const canvas = document.createElement("canvas");
      canvas.style.cssText = "position:fixed;left:-1000px;top:0;width:320px;height:180px";
      document.body.append(canvas);
      const renderer = new Renderer(canvas);
      const source = document.createElement("canvas");
      source.width = source.height = source.displayWidth = source.displayHeight = 256;
      const context = source.getContext("2d"), pixels = context.createImageData(256, 256);
      for (let y = 0; y < 256; y++) for (let x = 0; x < 256; x++) {
        const p = (y * 256 + x) * 4;
        pixels.data[p] = x; pixels.data[p + 1] = y; pixels.data[p + 3] = 255;
      }
      context.putImageData(pixels, 0, 0);
      renderer.upload("A", source);
      renderer.setRawZoom(4);
      renderer.panRaw([-10, 10]);
      const gl = renderer.gl, samples = [], layout = renderer.rawLayout();
      for (const rotation of [0, 180]) {
        renderer.viewerRotation.A = rotation;
        renderer.draw();
        const direction = rotation === 180 ? -1 : 1;
        for (const [x, y] of [[0, 0], [319, 179], [160, 90]]) {
          const pixel = new Uint8Array(4);
          gl.readPixels(x, y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
          const u = ((x + .5) / 320 - .5) * layout.span[0] * direction + layout.center[0];
          const v = (1 - (y + .5) / 180 - .5) * layout.span[1] * direction + layout.center[1];
          samples.push({ rotation, actual: [...pixel], expected: [Math.max(0, Math.min(255, u * 256 - .5)), Math.max(0, Math.min(255, v * 256 - .5))] });
        }
      }
      // This generated calibration exists only inside the test. Raw rotation
      // must never alter the calibrated renderer's source geometry.
      const synthetic = { K: [[128,0,127.5],[0,128,127.5],[0,0,1]], D: [0,0,0,0], xi: 1,
        crop: [0,0,256,256], output_size: [256,256], R_camera_from_rig: [[1,0,0],[0,1,0],[0,0,1]], max_theta_deg: 110 };
      renderer.calibration = { cameras: { A: synthetic, B: { ...synthetic, R_camera_from_rig: [[-1,0,0],[0,1,0],[0,0,-1]] } } };
      renderer.upload("A", source, true); renderer.upload("B", source, true);
      renderer.mode = "perspective";
      const calibrated = [];
      for (const rotation of [0, 180]) {
        renderer.viewerRotation = { A: rotation, B: rotation };
        renderer.draw();
        const pixel = new Uint8Array(4);
        gl.readPixels(100, 60, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
        calibrated.push([...pixel]);
      }
      if (JSON.stringify(calibrated[0]) !== JSON.stringify(calibrated[1]))
        throw new Error("Raw rotation changed calibrated projection");
      canvas.remove();
      return samples;
    });
    for (const sample of gpu) for (let channel = 0; channel < 2; channel++)
      assert.ok(Math.abs(sample.actual[channel] - sample.expected[channel]) <= 2, JSON.stringify(sample));

    // Synthetic calibration only enables the controls; no live frames or server.
    const lens = name => ({ image_size: [256,256], crop: [0,0,256,256], output_size: [256,256],
      K: [[128,0,127.5],[0,128,127.5],[0,0,1]], D: [0,0,0,0], xi: 1,
      flip_x: false, flip_y: false, max_theta_deg: null,
      R_camera_from_rig: name === "A" ? [[1,0,0],[0,1,0],[0,0,1]] : [[-1,0,0],[0,1,0],[0,0,-1]],
      provenance: { device_id: `synthetic-${name}` } });
    const lookAround = async () => {
      await emit({ type: "calibration", calibration: { schema_version: 1, model: "mei",
        rig_alignment_status: "synthetic_test_only", cameras: { A: lens("A"), B: lens("B") } } });
      await page.selectOption("#view", "perspective");
    };
    const perspective = () => page.evaluate(() => window.pigeonGround.snapshot().perspective);
    const wheel = deltaY => page.locator("#image").dispatchEvent("wheel", { deltaY, cancelable: true });
    const key = "pigeonvision.perspectiveDefaultFov.v1";
    const factory = 110 * Math.PI / 180;
    await lookAround();
    assert.equal(await page.locator("#perspective-controls").isVisible(), true);
    assert.equal(await page.locator("#focus-control").isVisible(), false);
    assert.equal(await page.locator("#perspective-zoom-value").textContent(), "1.00×");
    assert.equal((await perspective()).fov, factory);
    await page.waitForFunction(() => document.getElementById("view-caption").textContent.startsWith("1.00× · 110° look-around"));
    await wheel(-250);
    const chosen = await perspective();
    const expectedZoom = Math.tan(factory / 2) / Math.tan(chosen.fov / 2);
    assert.equal(await page.locator("#perspective-zoom-value").textContent(), `${expectedZoom.toFixed(2)}×`);
    assert.equal(await page.locator("#perspective-fov-value").textContent(), `${(chosen.fov * 180 / Math.PI).toFixed(1)}° horizontal FOV`);
    await page.waitForFunction(prefix => document.getElementById("view-caption").textContent.startsWith(prefix), `${expectedZoom.toFixed(2)}× · ${Math.round(chosen.fov * 180 / Math.PI)}° look-around`);
    await page.locator("#perspective-save-default").click();
    assert.equal((await perspective()).fov, chosen.fov, "Saving must not move the view");
    assert.equal(await page.evaluate(key => JSON.parse(localStorage.getItem(key)), key), chosen.fov);
    await wheel(200);
    await page.locator("#home").click();
    assert.equal((await perspective()).fov, chosen.fov);
    await page.selectOption("#view", "sphere");
    assert.equal(await page.locator("#perspective-controls").isVisible(), false);
    await page.selectOption("#view", "a");
    assert.equal(await page.locator("#perspective-controls").isVisible(), false);
    assert.equal((await page.evaluate(() => window.pigeonGround.snapshot())).focus.A.zoom, 1);
    await page.reload();
    await page.waitForFunction(() => window.pigeonGround?.snapshot().connected);
    await lookAround();
    assert.equal((await perspective()).fov, chosen.fov, "New loads use the browser's saved default");
    assert.equal((await perspective()).zoom, expectedZoom, "Saved default does not redefine factory 1×");
    await page.evaluate(key => localStorage.setItem(key, "2.61"), key);
    await page.reload();
    await page.waitForFunction(() => window.pigeonGround?.snapshot().connected);
    assert.equal((await perspective()).fov, factory, "Out-of-range storage uses factory view");
    await page.addInitScript(() => Object.defineProperty(window, "localStorage", {
      get() { throw new DOMException("Synthetic blocked storage", "SecurityError"); },
    }));
    await page.reload();
    await page.waitForFunction(() => window.pigeonGround?.snapshot().connected);
    await lookAround();
    assert.equal((await perspective()).fov, factory);
    await wheel(-100);
    const pageOnly = (await perspective()).fov;
    await page.locator("#perspective-save-default").click();
    assert.match(await page.locator("#perspective-default-status").textContent(), /page only/);
    await wheel(150);
    await page.locator("#home").click();
    assert.equal((await perspective()).fov, pageOnly);
    assert.deepEqual(failures, []);
    console.log(JSON.stringify({ passed: true, checked: ["source-error-recovery", "storage-error-retention", "raw-focus-controls", "drag-pan", "per-camera-reset", "GPU-source-crop", "independent-180-degree-rotation", "calibration-unaffected", "perspective-focal-zoom", "saved-perspective-default", "storage-validation-and-failure"] }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
