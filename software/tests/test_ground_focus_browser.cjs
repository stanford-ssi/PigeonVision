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
    await page.selectOption("#view", "b");
    assert.equal(await page.locator("#focus-value").textContent(), "1×");
    await page.locator("#image").hover();
    await page.mouse.wheel(0, -800);
    await page.waitForFunction(() => window.pigeonGround.snapshot().focus.B.zoom > 1);
    await page.locator("#home").click();
    const reset = await page.evaluate(() => window.pigeonGround.snapshot().focus);
    assert.deepEqual(reset.B, { zoom: 1, center: [.5, .5] });
    assert.equal(reset.A.zoom, 4);

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
      renderer.draw();
      const gl = renderer.gl, samples = [], layout = renderer.rawLayout();
      for (const [x, y] of [[0, 0], [319, 179], [160, 90]]) {
        const pixel = new Uint8Array(4);
        gl.readPixels(x, y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
        const u = ((x + .5) / 320 - .5) * layout.span[0] + layout.center[0];
        const v = (1 - (y + .5) / 180 - .5) * layout.span[1] + layout.center[1];
        samples.push({ actual: [...pixel], expected: [Math.max(0, Math.min(255, u * 256 - .5)), Math.max(0, Math.min(255, v * 256 - .5))] });
      }
      canvas.remove();
      return samples;
    });
    for (const sample of gpu) for (let channel = 0; channel < 2; channel++)
      assert.ok(Math.abs(sample.actual[channel] - sample.expected[channel]) <= 2, JSON.stringify(sample));
    assert.deepEqual(failures, []);
    console.log(JSON.stringify({ passed: true, checked: ["source-error-recovery", "storage-error-retention", "raw-focus-controls", "drag-pan", "per-camera-reset", "GPU-source-crop"] }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
