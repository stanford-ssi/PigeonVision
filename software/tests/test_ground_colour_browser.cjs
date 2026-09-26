// Offline GPU checks. No live receiver, camera controls or network source.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const shaderSource = fs.readFileSync(path.resolve(__dirname, "../python/pigeonvision/ground/static/projection.js"));

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--enable-gpu"] });
  try {
    const page = await browser.newPage({ viewport: { width: 200, height: 200 }, deviceScaleFactor: 1 });
    await page.route("http://127.0.0.1:9877/**", route => {
      const resource = new URL(route.request().url()).pathname;
      if (resource === "/") return route.fulfill({ contentType: "text/html", body: '<canvas id="test" style="width:129px;height:129px"></canvas>' });
      if (resource === "/projection.js") return route.fulfill({ contentType: "application/javascript", body: shaderSource });
      return route.abort();
    });
    await page.goto("http://127.0.0.1:9877/");
    const result = await page.evaluate(async () => {
      const { Renderer } = await import("/projection.js");
      const renderer = new Renderer(document.getElementById("test"));
      const initial = { enabled: renderer.colourEnabled, method: renderer.colourMethod, colour: structuredClone(renderer.colour) };
      const colours = { A: [64, 128, 192], B: [80, 100, 200] };
      for (const name of ["A", "B"]) {
        const source = document.createElement("canvas");
        source.width = source.height = 8;
        const context = source.getContext("2d");
        context.fillStyle = `rgb(${colours[name].join(",")})`;
        context.fillRect(0, 0, 8, 8);
        const frame = new VideoFrame(source, { timestamp: 0 });
        try { renderer.upload(name, frame); renderer.upload(name, frame, true); }
        finally { frame.close(); }
      }
      const camera = { image_size: [8, 8], K: [[2, 0, 3.5], [0, 2, 3.5], [0, 0, 1]],
        D: [0, 0, 0, 0], xi: 1, R_camera_from_rig: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        crop: [0, 0, 8, 8], output_size: [8, 8], flip_x: false, flip_y: false, max_theta_deg: 110 };
      renderer.calibration = { cameras: { A: structuredClone(camera), B: structuredClone(camera) } };
      const geometryBefore = JSON.stringify(renderer.calibration);
      const samples = [];
      const read = (label, mode, seam = 0) => {
        renderer.mode = mode; renderer.seam = seam; renderer.draw();
        const pixel = new Uint8Array(4);
        const gl = renderer.gl;
        gl.readPixels(64, 64, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
        samples.push({ label, pixel: [...pixel], error: gl.getError() });
      };
      read("raw-A-original", "a"); read("raw-B-original", "b");
      renderer.setColourCorrection("A", [1, 1, 1]); renderer.setColourCorrection("B", [1, 1, 1]);
      renderer.colourEnabled = true;
      read("raw-A-identity", "a"); read("raw-B-identity", "b");
      read("perspective-A-identity", "perspective", 2); read("sphere-B-identity", "sphere", 3);
      read("mask-original", "mask");
      renderer.setColourCorrection("A", [1.5, 0.5, 1.2]); renderer.setColourCorrection("B", [0.5, 1.5, 2]);
      read("raw-A-corrected", "a"); read("raw-B-corrected", "b");
      read("perspective-A-corrected", "perspective", 2); read("sphere-B-corrected", "sphere", 3);
      read("perspective-blended", "perspective"); read("sphere-blended", "sphere");
      read("mask-corrected", "mask");
      renderer.colourEnabled = false;
      read("raw-A-disabled", "a"); read("sphere-B-disabled", "sphere", 3);
      renderer.colourEnabled = true; renderer.colour.B.enabled = false;
      read("raw-B-locally-disabled", "b"); read("raw-A-still-corrected", "a");

      const returned = renderer.setColourCorrection("B", [0.1, 5, 1]);
      const clamped = [...renderer.colour.B.gain];
      returned[0] = 20;
      read("raw-B-clamped", "b");
      const stateBeforeInvalid = JSON.stringify(renderer.colour);
      const rejected = [];
      for (const [name, gain] of [["C", [1, 1, 1]], ["B", [NaN, 1, 1]], ["B", [1, Infinity, 1]],
                                 ["B", [1, "1", 1]], ["B", [1, 1]], ["B", null],
                                 ["B", new Array(3)], ["B", [1, , 1]]]) {
        try { renderer.setColourCorrection(name, gain); rejected.push(false); }
        catch (error) { rejected.push(error instanceof TypeError); }
      }
      const invalidPreservedState = stateBeforeInvalid === JSON.stringify(renderer.colour);
      const geometryUnchanged = geometryBefore === JSON.stringify(renderer.calibration);
      renderer.calibration.cameras.A.max_theta_deg = renderer.calibration.cameras.B.max_theta_deg = 1;
      renderer.yaw = Math.PI / 2;
      read("no-coverage-corrected", "perspective");
      renderer.colourEnabled = false;
      read("no-coverage-disabled", "perspective");
      return { initial, colours, samples, clamped, rejected, invalidPreservedState, geometryUnchanged };
    });
    assert.equal(result.initial.enabled, false);
    assert.equal(result.initial.method, "display_rgb_gain");
    for (const name of ["A", "B"]) assert.deepEqual(result.initial.colour[name], { enabled: false, gain: [1, 1, 1] });
    const pixels = Object.fromEntries(result.samples.map(({ label, pixel, error }) => {
      assert.equal(error, 0, label); assert.equal(pixel[3], 255, label);
      return [label, pixel.slice(0, 3)];
    }));
    const near = (label, expected) => expected.forEach((value, index) =>
      assert.ok(Math.abs(pixels[label][index] - value) <= 1, `${label}: ${pixels[label]} expected ${expected}`));
    near("raw-A-original", result.colours.A); near("raw-B-original", result.colours.B);
    for (const [label, original] of [["raw-A-identity", "raw-A-original"], ["raw-B-identity", "raw-B-original"],
        ["perspective-A-identity", "raw-A-original"], ["sphere-B-identity", "raw-B-original"],
        ["raw-A-disabled", "raw-A-original"], ["sphere-B-disabled", "raw-B-original"],
        ["raw-B-locally-disabled", "raw-B-original"], ["mask-corrected", "mask-original"],
        ["no-coverage-corrected", "no-coverage-disabled"]]) assert.deepEqual(pixels[label], pixels[original], label);
    const corrected = (colour, gain) => colour.map((value, index) => Math.min(255, value * gain[index]));
    const a = corrected(result.colours.A, [1.5, 0.5, 1.2]);
    const b = corrected(result.colours.B, [0.5, 1.5, 2]);
    for (const label of ["raw-A-corrected", "perspective-A-corrected", "raw-A-still-corrected"]) near(label, a);
    for (const label of ["raw-B-corrected", "sphere-B-corrected"]) near(label, b);
    for (const label of ["perspective-blended", "sphere-blended"]) near(label, a.map((value, i) => (value + b[i]) / 2));
    assert.deepEqual(result.clamped, [0.5, 2, 1]);
    near("raw-B-clamped", corrected(result.colours.B, result.clamped));
    assert.ok(result.rejected.every(Boolean));
    assert.ok(result.invalidPreservedState && result.geometryUnchanged);
    console.log(JSON.stringify({ passed: true, gpu_samples: result.samples.length, gain_method: result.initial.method,
      checks: "identity, raw/panorama gains, clipping, pre-blend correction, unchanged weights/masks/geometry, toggles, bounded inputs" }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
