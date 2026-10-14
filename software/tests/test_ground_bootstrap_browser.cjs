// Late joins show cached pixels without making a stopped live source current.
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
      window.testSent = [];
      window.VideoDecoder = class {
        static async isConfigSupported(config) { return { supported: true, config }; }
        constructor({ output }) { this.output = output; this.state = "unconfigured"; this.decodeQueueSize = 0; }
        configure() { this.state = "configured"; }
        close() { this.state = "closed"; }
        decode(chunk) {
          const source = new OffscreenCanvas(64, 64), context = source.getContext("2d");
          context.fillStyle = "rgb(80,120,160)";
          context.fillRect(0, 0, 64, 64);
          const frame = new VideoFrame(source, { timestamp: chunk.timestamp });
          queueMicrotask(() => { if (this.state === "closed") frame.close(); else this.output(frame); });
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
    await page.goto("http://127.0.0.1:8773/");
    await page.waitForFunction(() => window.pigeonGround?.snapshot().connected);
    await page.locator("#controls-toggle").click();
    await page.locator("#operator-controls").waitFor({ state: "visible" });
    await page.locator("#advanced-controls > summary").click();
    const emit = value => page.evaluate(value => window.testSocket.onmessage({ data: JSON.stringify(value) }), value);
    const snapshot = () => page.evaluate(() => window.pigeonGround.snapshot());
    const frame = (name, pts, keyframe = false) => page.evaluate(({ name, pts, keyframe }) => {
      const header = new TextEncoder().encode(JSON.stringify({ type: "frame", camera_id: name,
        timestamp_us: pts, keyframe, codec: "avc1.42001f" }));
      const bytes = new Uint8Array(4 + header.length + 1);
      new DataView(bytes.buffer).setUint32(0, header.length);
      bytes.set(header, 4);
      bytes[bytes.length - 1] = 1;
      window.testSocket.onmessage({ data: bytes.buffer });
    }, { name, pts, keyframe });
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

    await emit(status(true, 2));
    await emit({ type: "bootstrap_start", id: 2, metadata: metadata("replay") });
    await frame("A", 0, true);
    await frame("B", 0, true);
    await emit({ type: "bootstrap_checkpoint", id: 2, sequence: 1 });
    await page.waitForFunction(() => window.testSent.some(value => value.type === "bootstrap_ack" && value.id === 2));
    await page.waitForFunction(() => window.pigeonGround.snapshot().station.mission.pressureFresh);
    assert.equal((await snapshot()).presentation.frame.pts, 0);
    assert.equal((await snapshot()).pair.cached, false);
    assert.equal((await snapshot()).station.mission.phase, "PAD");
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ checked: ["stopped live snapshot is held", "cached telemetry is stale", "raw and mixed paired currency", "live bootstrap continuation", "paused replay preserved"] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
