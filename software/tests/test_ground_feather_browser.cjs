// GPU regression for recorded-crop boundaries, including saturated facing weights.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");

(async () => {
  const browser = await chromium.launch({
    channel: "chrome", headless: true, args: ["--enable-gpu"],
  });
  try {
    const page = await browser.newPage({ deviceScaleFactor: 1 });
    const base = process.env.GROUND_URL || "http://127.0.0.1:8768/";
    // Import the served renderer without opening/subscribing to the live app.
    await page.goto(new URL("/static/projection.js", base).href);
    const result = await page.evaluate(async () => {
      const { Renderer } = await import("/static/projection.js");
      const canvas = document.createElement("canvas");
      canvas.style.cssText = "position:fixed;left:0;top:0;width:129px;height:129px";
      document.body.append(canvas);
      const renderer = new Renderer(canvas);
      const colours = { A: [220, 40, 70], B: [30, 120, 200] };
      for (const name of ["A", "B"]) {
        const source = document.createElement("canvas");
        source.width = source.height = 512;
        const ctx = source.getContext("2d");
        ctx.fillStyle = `rgb(${colours[name].join(",")})`;
        ctx.fillRect(0, 0, 512, 512);
        renderer.upload(name, source, true);
      }
      const front = {
        image_size: [512, 512], K: [[256, 0, 255.5], [0, 256, 64], [0, 0, 1]],
        D: [0, 0, 0, 0], xi: 1,
        R_camera_from_rig: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        crop: [0, 0, 512, 512], output_size: [512, 512],
        flip_x: false, flip_y: false, valid_radius_px: null, max_theta_deg: null,
      };
      const side = {
        ...front, K: [[128, 0, 255.5], [0, 128, 255.5], [0, 0, 1]],
        R_camera_from_rig: [[0, 0, -1], [0, 1, 0], [1, 0, 0]],
      };
      const read = (a, b, pitch = 0, seam = 0, mode = "perspective") => {
        renderer.calibration = { cameras: { A: a, B: b } };
        renderer.mode = mode; renderer.seam = seam;
        renderer.yaw = renderer.roll = 0; renderer.pitch = pitch;
        renderer.draw();
        const gl = renderer.gl, pixel = new Uint8Array(4);
        gl.readPixels(64, 64, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
        if (gl.getError()) throw Error("WebGL error while sampling feather");
        return [...pixel];
      };
      // With xi=1, v=64-256*tan(pitch/2). Side's recorded point remains
      // well inside its crop, while facing preference is exactly front=1.
      const walks = [false, true].map(swapped => {
        const samples = [];
        for (let margin = -1; margin <= 60; margin += 0.5) {
          const pitch = 2 * Math.atan((64 - margin) / 256);
          samples.push({ margin, pixel: swapped ? read(side, front, pitch) : read(front, side, pitch) });
        }
        return { swapped, samples };
      });
      const edge = { ...front, K: [[256, 0, 255.5], [0, 256, 0], [0, 0, 1]] };
      const unavailable = { ...side, max_theta_deg: 1 };
      const singleA = read(edge, unavailable);
      const singleB = read(unavailable, edge);
      const bothAtEdge = read(edge, edge);
      const missing = read(unavailable, unavailable);
      const diagnostics = [1, 2, 3].map(seam => ({ seam, pixel: read(edge, side, 0, seam) }));
      const maskEdge = read(edge, side, 0, 0, "mask");
      const maskInterior = read(front, side, 0, 0, "mask");

      // A crop origin, output downscale and output flips do not change the
      // canonical distance to the source boundary or its blend confidence.
      const midpoint = { ...front, K: [[256, 0, 255.5], [0, 256, 24], [0, 0, 1]] };
      const shifted = c => ({ ...c,
        image_size: [1024, 1024],
        K: [[c.K[0][0], 0, c.K[0][2] + 128], [0, c.K[1][1], c.K[1][2] + 256], [0, 0, 1]],
        crop: [128, 256, 512, 512], output_size: [256, 256], flip_x: true, flip_y: true,
      });
      const resizeOriginal = read(midpoint, side);
      const resizeFlipped = read(shifted(midpoint), shifted(side));
      canvas.remove();
      return { colours, walks, singleA, singleB, bothAtEdge, missing,
        diagnostics, maskEdge, maskInterior, resizeOriginal, resizeFlipped };
    });
    const near = (actual, expected, label) => expected.forEach((value, i) =>
      assert.ok(Math.abs(actual[i] - value) <= 1, `${label}: ${actual} versus ${expected}`));
    for (const { swapped, samples } of result.walks) {
      const preferred = result.colours[swapped ? "B" : "A"];
      const alternate = result.colours[swapped ? "A" : "B"];
      near(samples[0].pixel, alternate, "Only the alternate source is recorded");
      near(samples.find(s => s.margin === 0).pixel, alternate, "Zero edge confidence reopens the alternate");
      near(samples.at(-1).pixel, preferred, "Interior retains saturated facing preference");
      const middle = samples.find(s => s.margin === 24).pixel;
      near(middle, preferred.map((v, i) => (v + alternate[i]) / 2), "Boundary band has a visible transition");
      let maximumJump = 0;
      for (let i = 1; i < samples.length; i++) {
        for (let channel = 0; channel < 3; channel++) {
          const delta = samples[i].pixel[channel] - samples[i - 1].pixel[channel];
          maximumJump = Math.max(maximumJump, Math.abs(delta));
          const direction = preferred[channel] - alternate[channel];
          assert.ok(delta * direction >= -1, "Feather must progress monotonically between recorded sources");
        }
      }
      assert.ok(maximumJump <= 5, `No abrupt source-edge jump: ${maximumJump}/255 per half source pixel`);
    }
    near(result.singleA, result.colours.A, "Single valid A remains fully visible at its edge");
    near(result.singleB, result.colours.B, "Single valid B remains fully visible at its edge");
    near(result.bothAtEdge, result.colours.A.map((v, i) => (v + result.colours.B[i]) / 2), "Zero total confidence has a finite facing fallback");
    assert.deepEqual(result.missing, [31, 38, 41, 255], "No recorded source retains the missing-coverage hatch");
    for (const { seam, pixel } of result.diagnostics)
      near(pixel, result.colours[seam === 3 ? "B" : "A"], `Diagnostic seam ${seam} bypasses edge feather`);
    assert.deepEqual(result.maskEdge, result.maskInterior, "Coverage diagnostics preserve facing weights");
    near(result.resizeOriginal, [125, 80, 135], "Canonical midpoint is half blended");
    assert.deepEqual(result.resizeFlipped, result.resizeOriginal, "Crop origin, resize and flips preserve the feather band");
    console.log(JSON.stringify({ passed: true, source_edge_samples: 246,
      checks: "Saturated A/B preference, continuous edge transitions, zero confidence, one-source exact, missing hatch, diagnostics, crop origin/resize/flips" }));
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
