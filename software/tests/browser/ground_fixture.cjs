// Local viewer assets, mocked transport/decoder, and real browser VideoFrames.
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assets = path.resolve(__dirname, "../../python/pigeonvision/ground/static");
const origin = "http://127.0.0.1:8773";

async function openGroundFixture({ clock = false } = {}) {
  const browser = await chromium.launch({
    channel: process.env.PV_TEST_BROWSER || "chrome",
    headless: true,
    args: ["--enable-gpu", "--enable-unsafe-swiftshader"],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    if (clock) await page.clock.install();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    // No test request may reach an actual viewer, camera, or external service.
    await page.route("**/*", route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) return route.abort();
      const file = url.pathname === "/" ? "index.html" : url.pathname.replace(/^\/static\//, "");
      if (file === "/favicon.ico") return route.fulfill({ status: 204 });
      if (file.includes("..") || !fs.existsSync(path.join(assets, file))) return route.abort();
      const contentType = { ".js": "application/javascript", ".css": "text/css",
        ".ttf": "font/ttf", ".png": "image/png" }[path.extname(file)] || "text/html";
      return route.fulfill({ body: fs.readFileSync(path.join(assets, file)), contentType });
    });
    await page.addInitScript(() => {
      window.testSent = [];
      window.testDecoders = [];
      window.testDecodeCalls = [];
      window.testHeldDecodeOutputs = [];
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
          const output = () => { if (this.state === "closed") frame.close(); else this.output(frame); };
          if (window.testHoldDecodeOutputs) window.testHeldDecodeOutputs.push(output);
          else queueMicrotask(output);
        }
      };
      window.WebSocket = class {
        static OPEN = 1;
        constructor() {
          this.readyState = 1;
          window.testSocket = this;
          queueMicrotask(() => { this.onopen?.(); this.onmessage?.({ data: JSON.stringify({ type: "control_owner", can_control: true }) }); });
        }
        send(value) { window.testSent.push(JSON.parse(value)); }
        close() { this.readyState = 3; this.onclose?.(); }
      };
    });
    await page.goto(origin);
    await page.waitForFunction(() => window.pigeonGround?.snapshot().connected);
    const emit = value => page.evaluate(value => window.testSocket.onmessage({ data: JSON.stringify(value) }), value);
    const snapshot = () => page.evaluate(() => window.pigeonGround.snapshot());
    const frame = (name, pts, keyframe = false, fields = {}) => page.evaluate(({ name, pts, keyframe, fields }) => {
      const header = new TextEncoder().encode(JSON.stringify({ type: "frame", camera_id: name,
        timestamp_us: pts, keyframe, codec: "avc1.42001f", ...fields }));
      const bytes = new Uint8Array(4 + header.length + 1);
      new DataView(bytes.buffer).setUint32(0, header.length);
      bytes.set(header, 4);
      bytes[bytes.length - 1] = 1;
      window.testSocket.onmessage({ data: bytes.buffer });
    }, { name, pts, keyframe, fields });
    return { browser, page, errors, emit, snapshot, frame };
  } catch (error) {
    await browser.close();
    throw error;
  }
}

module.exports = { openGroundFixture };
