/* Capture two native fisheyes, encode separately, and multiplex the RF payload.
 * Full: NODE_PATH=... node software/simulator/render_sequence.cjs
 * Smoke: SIM_SMOKE_FRAMES=30 SIM_OUTPUT_DIR=build/simulator-smoke ...
 * Optional: SIM_DURATION_S, SIM_START_S, SIM_URL, SIM_SITE_DIR, FFMPEG, SIM_BROWSER_CHANNEL.
 * Outputs use v3 names; previous panorama assets and manifest.json are untouched.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const cp = require("child_process");
const crypto = require("crypto");
const root = path.resolve(__dirname, "../..");
const site = process.env.SIM_SITE_DIR
  ? path.resolve(process.env.SIM_SITE_DIR)
  : path.join(root, "docs");
const defaultOut = path.join(root, "build/simulator");
const out = process.env.SIM_OUTPUT_DIR
  ? path.resolve(root, process.env.SIM_OUTPUT_DIR)
  : defaultOut;
const url = process.env.SIM_URL || "http://127.0.0.1:8767/docs/?capture=1";
const fps = 30, width = 1552, preset = "900", videoRate = 4, muxRate = 9, muxDelay = 0.5;
const smoke = Number(process.env.SIM_SMOKE_FRAMES || 0);
const start = Number(process.env.SIM_START_S || 0);
const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const fileHash = (name) => sha256(fs.readFileSync(path.join(site, name)));
const sourceFiles = [
  "scenario.js",
  "scene.js",
  "engine.js", "terrain.js", "rocket.js",
  "assets/launch.json", "assets/rocket.json",
  "assets/airbrakes.json",
];
const stableJSON = (value) => JSON.stringify(value, function (_key, v) {
  return v && typeof v === "object" && !Array.isArray(v)
    ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a.localeCompare(b)))
    : v;
});
function findFFmpeg() {
  if (process.env.FFMPEG) return process.env.FFMPEG;
  const found = cp.spawnSync("ffmpeg", ["-version"], { encoding: "utf8" });
  if (found.error || found.status !== 0) throw Error("FFmpeg not found; install it or set FFMPEG.");
  return "ffmpeg";
}
function run(ffmpeg, args, logFile) {
  const result = cp.spawnSync(ffmpeg, args, { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
  fs.writeFileSync(logFile, (result.stdout || "") + (result.stderr || ""));
  if (result.error || result.status !== 0)
    throw Error(`${path.basename(logFile)}: ${result.error || result.stderr?.slice(-3000) || `exit ${result.status}`}`);
  return result;
}
function encoder(ffmpeg, args, logFile) {
  const child = cp.spawn(ffmpeg, args, { stdio: ["pipe", "ignore", "pipe"] });
  const state = { child, failure: null, closed: false, log: "" };
  child.stderr.on("data", (chunk) => { state.log += chunk; });
  child.stdin.on("error", (e) => { state.failure = e; });
  state.done = new Promise((resolve) => {
    child.on("error", (e) => { state.failure = e; });
    child.on("close", (code, signal) => {
      state.closed = true;
      fs.writeFileSync(logFile, state.log);
      resolve({ code, signal });
    });
  });
  state.write = async (buffer) => {
    if (state.closed || state.failure) throw state.failure || Error("Encoder exited early");
    await new Promise((resolve, reject) => child.stdin.write(buffer, (error) => error ? reject(error) : resolve()));
  };
  return state;
}
function decodeCheck(ffmpeg, input, stream, frames, stage, name) {
  const result = run(ffmpeg, [
    "-hide_banner", "-v", "error", "-xerror", "-err_detect", "explode",
    "-i", input, "-map", `0:v:${stream}`, "-an", "-progress", "pipe:1",
    "-nostats", "-f", "null", "-",
  ], path.join(stage, `${name}-decode.log`));
  const decoded = Number([...result.stdout.matchAll(/^frame=(\d+)$/gm)].at(-1)?.[1]);
  if (decoded !== frames) throw Error(`${name}: decoded ${decoded} frames, expected ${frames}`);
  return decoded;
}
function checkPNG(buffer) {
  if (buffer.length < 24 || buffer.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a")
    throw Error("Capture did not return PNG image data");
  const w = buffer.readUInt32BE(16), h = buffer.readUInt32BE(20);
  if (w !== width || h !== width)
    throw Error(`Expected ${width} × ${width} raw camera crop, received ${w} × ${h}`);
}
function checkTransportTiming(filename) {
  // For this constant-rate, no-B-frame transport, confirm every video packet
  // finishes transmission before its PES decode deadline. PCR timestamps set
  // the byte clock, so this also catches bursts hidden by a good average rate.
  const bytes = fs.readFileSync(filename);
  if (bytes.length % 188) throw Error("TS length is not a whole number of packets.");
  let anchor = null, maxClockError = 0;
  const tracks = new Map();
  const timestamp = (b, i) => (
    ((b[i] >> 1) & 7) * 2 ** 30 + b[i + 1] * 2 ** 22 +
    (b[i + 2] >> 1) * 2 ** 15 + b[i + 3] * 2 ** 7 + (b[i + 4] >> 1)
  ) / 90000;
  for (let offset = 0; offset < bytes.length; offset += 188) {
    const p = bytes.subarray(offset, offset + 188);
    if (p[0] !== 0x47) throw Error(`TS sync lost at byte ${offset}`);
    const pid = ((p[1] & 31) << 8) | p[2], afc = (p[3] >> 4) & 3;
    let payload = 4;
    if (afc & 2) {
      const length = p[4];
      if (length >= 7 && (p[5] & 16)) {
        const base = p[6] * 2 ** 25 + p[7] * 2 ** 17 + p[8] * 2 ** 9 + p[9] * 2 + (p[10] >> 7);
        const extension = ((p[10] & 1) << 8) | p[11];
        const clock = base / 90000 + extension / 27000000;
        if (!anchor) anchor = { offset: offset + 12, clock };
        else maxClockError = Math.max(maxClockError, Math.abs(clock - (anchor.clock + (offset + 12 - anchor.offset) * 8 / (muxRate * 1e6))));
      }
      payload += length + 1;
    }
    if (!(afc & 1) || payload >= 188) continue;
    if ((p[1] & 64) && p[payload] === 0 && p[payload + 1] === 0 && p[payload + 2] === 1 && (p[payload + 3] & 0xf0) === 0xe0) {
      const flags = p[payload + 7] >> 6;
      if (!(flags & 2)) throw Error(`Video PID ${pid} has no presentation/decode timestamp.`);
      const dts = timestamp(p, payload + (flags === 3 ? 14 : 9));
      const track = tracks.get(pid) || { first_dts_s: dts, min_packet_slack_s: Infinity, pes_count: 0 };
      track.dts = dts;
      track.pes_count++;
      tracks.set(pid, track);
    }
    const track = tracks.get(pid);
    if (track && anchor) {
      const packetEnd = anchor.clock + (offset + 188 - anchor.offset) * 8 / (muxRate * 1e6);
      track.min_packet_slack_s = Math.min(track.min_packet_slack_s, track.dts - packetEnd);
    }
  }
  if (!anchor || tracks.size !== 2) throw Error("Expected a PCR clock and exactly two timestamped video streams.");
  if (maxClockError > 0.001) throw Error(`TS byte clock disagrees with PCR by ${maxClockError * 1000} ms.`);
  const result = {};
  for (const [pid, track] of tracks) {
    if (track.min_packet_slack_s < 0) throw Error(`Video PID ${pid} misses its decode deadline by ${-track.min_packet_slack_s * 1000} ms.`);
    result[pid] = {
      first_dts_s: track.first_dts_s, first_decode_lead_ms: (track.first_dts_s - anchor.clock) * 1000,
      min_packet_slack_ms: track.min_packet_slack_s * 1000, pes_count: track.pes_count,
    };
  }
  return { first_pcr_s: anchor.clock, max_pcr_clock_error_us: maxClockError * 1e6, video_pids: result };
}
async function main() {
  if (!Number.isInteger(smoke) || smoke < 0 || !Number.isFinite(start) || start < 0)
    throw Error("SIM_SMOKE_FRAMES must be a nonnegative integer; SIM_START_S must be nonnegative.");
  if (smoke && out === defaultOut)
    throw Error("Smoke capture requires SIM_OUTPUT_DIR outside the production launch-video folder.");
  const ffmpeg = findFFmpeg();
  const launch = JSON.parse(fs.readFileSync(path.join(site, "assets/launch.json")));
  const duration = smoke ? smoke / fps : Number(process.env.SIM_DURATION_S || (launch.summary.duration_s - start));
  if (!(duration > 0) || !Number.isFinite(duration) || start + duration > launch.summary.duration_s + 1 / fps)
    throw Error("Capture duration must lie inside launch.json flight duration.");
  const frames = smoke || Math.round(duration * fps);
  if (frames < 1) throw Error("Capture needs at least one frame.");
  const inputs = Object.fromEntries(sourceFiles.map((name) => [name, fileHash(name)]));
  fs.mkdirSync(out, { recursive: true });
  const stage = fs.mkdtempSync(path.join(out, ".render-v3-"));
  let browser;
  const encoders = [];
  const publish = [];
  try {
    browser = await chromium.launch({
      channel: process.env.SIM_BROWSER_CHANNEL || "chrome",
      headless: true,
      args: ["--enable-gpu", "--disable-background-timer-throttling"],
    });
    const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(url);
    await page.waitForFunction(() => window.simReady && typeof window.renderSequenceFrame === "function", {}, { timeout: 60000 });
    // Fail before encoding if the browser still exposes the old panorama capture.
    const first = {};
    for (const camera of ["a", "b"]) {
      const base64 = await page.evaluate((p) => window.renderSequenceFrame(p), {
        t: start, preset, width, kind: `camera-${camera}`,
      });
      first[camera] = Buffer.from(base64, "base64");
      checkPNG(first[camera]);
    }
    const captureMetadata = await page.evaluate(() => {
      if (typeof window.renderSequenceMetadata !== "function")
        throw Error("Capture needs window.renderSequenceMetadata() with CIL212 settings.");
      return window.renderSequenceMetadata();
    });
    if (!/CIL212/.test(JSON.stringify(captureMetadata)))
      throw Error("Capture metadata does not identify the selected CIL212 lens.");
    const encodeArgs = {};
    for (const camera of ["a", "b"]) {
      const name = `cil212-camera-${camera}`;
      const args = [
        "-hide_banner", "-y", "-f", "image2pipe", "-vcodec", "png", "-framerate", String(fps), "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level:v", "5.1",
        "-g", String(fps), "-keyint_min", String(fps), "-sc_threshold", "0", "-bf", "0",
        "-b:v", `${videoRate}M`, "-minrate", `${videoRate}M`, "-maxrate", `${videoRate}M`, "-bufsize", "2M",
        "-x264-params", "nal-hrd=cbr:force-cfr=1:repeat-headers=1",
        "-f", "matroska", path.join(stage, `${name}.mkv`),
      ];
      encodeArgs[camera] = args.map((x) => x.startsWith(stage) ? path.basename(x) : x);
      encoders.push(encoder(ffmpeg, args, path.join(stage, `${name}-encode.log`)));
    }
    const begin = Date.now();
    for (let i = 0; i < frames; i++) {
      for (const [index, camera] of ["a", "b"].entries()) {
        const buffer = i === 0 ? first[camera] : Buffer.from(await page.evaluate(
          (p) => window.renderSequenceFrame(p),
          { t: start + i / fps, preset, width, kind: `camera-${camera}` },
        ), "base64");
        checkPNG(buffer);
        await encoders[index].write(buffer);
      }
      if (errors.length) throw Error(errors.join("\n"));
      if (i % fps === 0) console.log(`Two fisheyes: ${i + 1}/${frames} frames each, ${((Date.now() - begin) / 1000).toFixed(1)} s elapsed`);
    }
    for (const enc of encoders) enc.child.stdin.end();
    const status = await Promise.all(encoders.map((enc) => enc.done));
    status.forEach((result, i) => {
      if (result.code !== 0 || encoders[i].failure)
        throw Error(`Encoder ${i}: ${encoders[i].failure || encoders[i].log.slice(-3000)}`);
    });
    const tsName = "cil212-dual.ts", ts = path.join(stage, tsName);
    const muxArgs = [
      "-hide_banner", "-y", "-i", path.join(stage, "cil212-camera-a.mkv"),
      "-i", path.join(stage, "cil212-camera-b.mkv"), "-map", "0:v:0", "-map", "1:v:0",
      "-c", "copy", "-f", "mpegts", "-muxrate", `${muxRate}M`,
      // Two simultaneous video bursts need transmission time before DTS.
      "-pcr_period", "20", "-pat_period", "0.1", "-muxdelay", String(muxDelay), "-muxpreload", "0", ts,
    ];
    run(ffmpeg, muxArgs, path.join(stage, "cil212-mux.log"));
    if (/dts < pcr|buffer underflow|non.monoton/i.test(fs.readFileSync(path.join(stage, "cil212-mux.log"), "utf8")))
      throw Error("Transport mux reported timing/underflow warnings; inspect cil212-mux.log.");
    const timing = checkTransportTiming(ts);
    const measured = fs.statSync(ts).size * 8 / (frames / fps) / 1e6;
    if (frames >= fps && measured > 9.809987380566072)
      throw Error(`Measured transport ${measured.toFixed(4)} Mb/s exceeds the current RF allowance.`);
    const cameras = {};
    for (const [index, camera] of ["a", "b"].entries()) {
      const name = `cil212-camera-${camera}`, mp4 = `${name}.mp4`;
      const decoded = decodeCheck(ffmpeg, ts, index, frames, stage, name);
      run(ffmpeg, [
        "-hide_banner", "-y", "-i", ts, "-map", `0:v:${index}`, "-c", "copy",
        "-movflags", "+faststart", path.join(stage, mp4),
      ], path.join(stage, `${name}-remux.log`));
      decodeCheck(ffmpeg, path.join(stage, mp4), 0, frames, stage, `${name}-mp4`);
      cameras[camera] = {
        mp4, width, height: width, fps, frames, start_s: start, duration_s: frames / fps,
        video_target_mbps: videoRate, decoded_frames: decoded, codec: "H.264 High", pixel_format: "yuv420p",
      };
      publish.push(mp4, `${name}-encode.log`, `${name}-decode.log`, `${name}-mp4-decode.log`, `${name}-remux.log`);
    }
    // Refuse to associate footage with source files changed while it was rendering.
    for (const [name, hash] of Object.entries(inputs))
      if (fileHash(name) !== hash) throw Error(`${name} changed during capture; rerun after the scene is stable.`);
    const parameters = {
      preset, sensor: "IMX900", lens: "CIL212", sensor_px: [2064, 1552], crop_px: [width, width],
      fps, frames, start_s: start, duration_s: frames / fps, video_target_mbps_each: videoRate,
      mux_target_mbps: muxRate, mux_delay_s: muxDelay, encoding: "Independent native fisheyes; ground decode and stitch",
      capture: captureMetadata,
    };
    const manifest = {
      pipeline_version: 3, camera_scope: "two-fisheyes-ground-stitch", generated_at: new Date().toISOString(),
      smoke: Boolean(smoke), preset, sensor: "IMX900", lens: "CIL212", crop_px: width,
      fps, frames, duration_s: frames / fps, start_s: start, parameters,
      fingerprint: {
        algorithm: "sha256", scene_sha256: sha256(stableJSON(inputs)),
        flight_sha256: inputs["assets/launch.json"],
        config_sha256: sha256(stableJSON(parameters)), inputs,
        generator_sha256: sha256(fs.readFileSync(__filename)),
      },
      flight: "launch.json", flight_target_m: launch.inputs.target_apogee_m,
      sequence_scope: "Pad through ascent, apogee and early descent; does not simulate landing",
      projection: "Two square native sensor crops, unstitched fisheyes",
      codec_loss: true, packet_loss_simulated: false, radio_allowance_mbps: 9.809987380566072,
      cameras, transport: { ts: tsName, video_streams: 2, mux_target_mbps: muxRate, measured_ts_mbps: measured,
        mux_delay_s: muxDelay, pcr_period_ms: 20, pat_period_s: 0.1, timing,
        measured_bytes: fs.statSync(ts).size, measurement: "Whole TS file bytes × 8 / decoded frame duration" },
      encoder_arguments: encodeArgs, mux_arguments: muxArgs.map((x) => x.startsWith(stage) ? path.basename(x) : x),
      limitations: ["Synthetic optical scene, not measured lens MTF or sensor noise", "Codec test does not establish CM5 encode throughput", "No RF packet loss or receiver errors modeled"],
    };
    fs.writeFileSync(path.join(stage, "manifest-v3.json"), JSON.stringify(manifest, null, 2) + "\n");
    await page.screenshot({ path: path.join(stage, "cil212-render-check.png") });
    publish.push(tsName, "cil212-mux.log", "cil212-render-check.png", "manifest-v3.json");
    // Publish the manifest last so it never points at incomplete outputs.
    for (const name of publish) fs.renameSync(path.join(stage, name), path.join(out, name));
    fs.rmSync(stage, { recursive: true });
    console.log(`Complete: ${frames} frames per camera, ${measured.toFixed(4)} Mb/s TS. ${path.join(out, "manifest-v3.json")}`);
  } catch (error) {
    console.error(`Capture stopped. Intermediate files retained at ${stage}`);
    throw error;
  } finally {
    for (const enc of encoders) if (!enc.closed) enc.child.kill("SIGKILL");
    if (browser) await browser.close();
  }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
