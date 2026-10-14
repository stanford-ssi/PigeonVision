// Receiver recovery using local assets, mocked transport/decoder, real VideoFrames.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const assets = path.resolve(__dirname, "../python/pigeonvision/ground/static");

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--enable-gpu"] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("http://127.0.0.1:8773/**", route => {
      const resource = new URL(route.request().url()).pathname;
      const file = resource === "/" ? "index.html" : resource.replace(/^\/static\//, "");
      if (file === "favicon.ico") return route.fulfill({ status: 204 });
      if (file.includes("..") || !fs.existsSync(path.join(assets, file))) return route.abort();
      const contentType = file.endsWith(".js") ? "application/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".ttf") ? "font/ttf" : file.endsWith(".png") ? "image/png" : "text/html";
      return route.fulfill({ body: fs.readFileSync(path.join(assets, file)), contentType });
    });
    await page.addInitScript(() => {
      window.testDecoders = [];
      window.testDecodeCalls = [];
      window.VideoDecoder = class {
        static async isConfigSupported(config) { return { supported: true, config }; }
        constructor(callbacks) {
          Object.assign(this, callbacks, { state: "unconfigured", decodeQueueSize: 0 });
          window.testDecoders.push(this);
        }
        configure() { this.state = "configured"; }
        close() { this.state = "closed"; }
        decode(chunk) {
          window.testDecodeCalls.push(chunk.timestamp);
          const failure = window.testDecodeFailure;
          window.testDecodeFailure = null;
          if (failure === "sync") throw Error("synthetic synchronous failure");
          if (failure === "async") {
            queueMicrotask(() => { this.state = "closed"; this.error(Error("synthetic asynchronous failure")); });
            return;
          }
          const source = new OffscreenCanvas(64, 64), context = source.getContext("2d");
          context.fillStyle = "rgb(80,120,160)";
          context.fillRect(0, 0, 64, 64);
          const frame = new VideoFrame(source, { timestamp: chunk.timestamp });
          queueMicrotask(() => { if (this.state === "closed") frame.close(); else this.output(frame); });
        }
      };
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; window.testSocket = this; queueMicrotask(() => this.onopen?.()); }
        send() {}
        close() { this.readyState = 3; this.onclose?.(); }
      };
    });
    await page.goto("http://127.0.0.1:8773/");
    await page.waitForFunction(() => window.pigeonGround?.snapshot().connected);
    const emit = value => page.evaluate(value => window.testSocket.onmessage({ data: JSON.stringify(value) }), value);
    const snapshot = () => page.evaluate(() => window.pigeonGround.snapshot());
    const frame = async (name, pts, keyframe = true) => page.evaluate(({ name, pts, keyframe }) => {
      const header = new TextEncoder().encode(JSON.stringify({ type: "frame", camera_id: name,
        timestamp_us: pts, keyframe, codec: "avc1.42001f" }));
      const bytes = new Uint8Array(4 + header.length + 1);
      new DataView(bytes.buffer).setUint32(0, header.length);
      bytes.set(header, 4);
      bytes[bytes.length - 1] = 1;
      window.testSocket.onmessage({ data: bytes.buffer });
    }, { name, pts, keyframe });
    const presented = pts => page.waitForFunction(pts => window.pigeonGround.snapshot().presentation.frame.pts === pts, pts);
    await emit({ type: "status", state: "ready", source: "udp://synthetic:5000", replay: false, playing: true, generation: 1 });
    await frame("A", 0);
    await presented(0);

    await page.evaluate(() => { window.testDecodeFailure = "async"; });
    await frame("A", 10000, false);
    await page.waitForFunction(() => window.pigeonGround.snapshot().errors?.includes("Camera A decoder: synthetic asynchronous failure"));
    assert.equal((await snapshot()).presentation.frame.pts, null, "Failed decoder must invalidate presentation currency");
    await frame("B", 10000);
    await page.waitForFunction(() => window.pigeonGround.snapshot().presentation.raw.B === 10000);
    assert.match((await snapshot()).errors, /Camera A decoder/, "Camera B recovery cannot clear camera A's error");
    await frame("A", 20000, false);
    await frame("A", 30000);
    await presented(30000);
    assert.equal(await page.evaluate(() => window.testDecodeCalls.includes(20000)), false, "Deltas must be skipped until a fresh keyframe");
    assert.equal((await snapshot()).errors, null, "Successful camera A output must clear its recoverable error");

    // A delayed callback from a retired decoder cannot reset its replacement.
    await page.evaluate(() => window.testDecoders[0].error(Error("retired decoder callback")));
    await frame("A", 40000, false);
    await presented(40000);
    assert.equal((await snapshot()).errors, null);

    await page.evaluate(() => { window.testDecodeFailure = "sync"; });
    await frame("A", 50000, false);
    await page.waitForFunction(() => window.pigeonGround.snapshot().errors?.includes("Camera A decoder: synthetic synchronous failure"));
    assert.equal((await snapshot()).presentation.frame.pts, null);
    await emit({ type: "error", component: "source", message: "independent source failure", recoverable: false });
    await frame("A", 60000);
    await presented(60000);
    assert.equal((await snapshot()).errors, "independent source failure", "Decoder recovery must preserve other failures");
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ checked: ["asynchronous decoder recovery", "synchronous decoder recovery", "wait for IDR", "retired decoder isolation", "independent errors preserved"] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
