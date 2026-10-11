// Offline WebGL pixels verify optical-axis roll without a receiver or playback.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const projection = fs.readFileSync(path.resolve(__dirname, "../python/pigeonvision/ground/static/projection.js"));
const attitude = fs.readFileSync(path.resolve(__dirname, "../python/pigeonvision/ground/static/attitude.js"));

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--enable-gpu"] });
  try {
    const page = await browser.newPage({ viewport: { width: 240, height: 200 }, deviceScaleFactor: 1 });
    await page.route("http://127.0.0.1:9879/**", route => {
      const name = new URL(route.request().url()).pathname;
      if (name === "/") return route.fulfill({ contentType: "text/html", body: '<canvas id="test"></canvas>' });
      if (name === "/projection.js") return route.fulfill({ contentType: "application/javascript", body: projection });
      if (name === "/attitude.js") return route.fulfill({ contentType: "application/javascript", body: attitude });
      return route.abort();
    });
    await page.goto("http://127.0.0.1:9879/");
    const result = await page.evaluate(async () => {
      const { Renderer, multiplyBasis } = await import("/projection.js");
      const { DEMO_REFERENCE, imageFromReference, transformPose } = await import("/attitude.js");
      const canvas = document.getElementById("test");
      canvas.style.width = canvas.style.height = "129px";
      const renderer = new Renderer(canvas);
      const initialRoll = renderer.roll;
      const source = document.createElement("canvas");
      source.width = source.height = 128;
      const context = source.getContext("2d"), image = context.createImageData(128, 128);
      for (let y = 0; y < 128; y++) for (let x = 0; x < 128; x++) {
        const p = (y * 128 + x) * 4;
        image.data[p] = Math.round(x * 255 / 127);
        image.data[p + 1] = Math.round(y * 255 / 127);
        image.data[p + 2] = 24;
        image.data[p + 3] = 255;
      }
      context.putImageData(image, 0, 0);
      const frame = new VideoFrame(source, { timestamp: 0 });
      try {
        for (const name of ["A", "B"]) { renderer.upload(name, frame); renderer.upload(name, frame, true); }
      } finally { frame.close(); }
      const camera = { image_size: [128, 128], K: [[40, 0, 63.5], [0, 40, 63.5], [0, 0, 1]],
        D: [0, 0, 0, 0], xi: 1, R_camera_from_rig: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        crop: [0, 0, 128, 128], output_size: [128, 128], flip_x: false, flip_y: false, max_theta_deg: 110 };
      renderer.calibration = { cameras: { A: structuredClone(camera), B: structuredClone(camera) } };
      renderer.mode = "perspective";
      renderer.fov = Math.PI / 2;
      renderer.seam = 2;
      const originalGeometry = JSON.stringify(renderer.calibration);
      const samples = [];
      function read(mode, roll, width, height, yaw, pitch, viewTransform = null) {
        canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
        Object.assign(renderer, { mode, roll, yaw, pitch, viewTransform });
        renderer.draw();
        const x = (width - 1) / 2, y = (height - 1) / 2;
        const positions = [[x, y], [x + 32, y], [x, y + 32], [x - 32, y], [x, y - 32]];
        const pixels = positions.map(([px, py]) => {
          const pixel = new Uint8Array(4);
          renderer.gl.readPixels(px, py, 1, 1, renderer.gl.RGBA, renderer.gl.UNSIGNED_BYTE, pixel);
          return [...pixel];
        });
        return { pixels, error: renderer.gl.getError() };
      }
      for (const [width, height] of [[129, 129], [161, 97]]) for (const [yaw, pitch] of [[0, 0], [.8, -.5]]) {
        const views = [0, Math.PI / 2, -Math.PI / 2, Math.PI, .7].map(roll => read("perspective", roll, width, height, yaw, pitch));
        samples.push({ width, height, yaw, pitch, views });
      }
      const scene = angle => {
        const c = Math.cos(angle), s = Math.sin(angle);
        return multiplyBasis([1, 0, 0, 0, c, s, 0, -s, c], DEMO_REFERENCE);
      };
      const unchangedModes = ["a", "b", "sphere", "mask"].map(mode => ({ mode,
        level: read(mode, 0, 129, 129, .8, -.5),
        rolled: read(mode, 1.2, 129, 129, .8, -.5, imageFromReference(scene(.4))) }));
      const referenceViews = [];
      for (const angle of [-.45, .2, .6]) for (const roll of [0, .7]) {
        const worldPose = { yaw: .25, pitch: -.2, roll, fov: Math.PI / 2 };
        const transform = imageFromReference(scene(angle));
        const bodyPose = transformPose(worldPose, transform);
        referenceViews.push({
          reference: read("perspective", roll, 129, 129, worldPose.yaw, worldPose.pitch, transform),
          body: read("perspective", bodyPose.roll, 129, 129, bodyPose.yaw, bodyPose.pitch),
        });
      }
      return { initialRoll, samples, referenceViews, unchangedModes,
        geometryUnchanged: originalGeometry === JSON.stringify(renderer.calibration) };
    });
    assert.equal(result.initialRoll, 0);
    assert.ok(result.geometryUnchanged);
    const near = (actual, expected) => expected.forEach((value, i) => assert.ok(Math.abs(actual[i] - value) <= 1,
      `Pixel ${actual} differs from ${expected}`));
    for (const sample of result.samples) {
      const [level, clockwise, counterclockwise, inverted, oblique] = sample.views;
      for (const view of sample.views) {
        assert.equal(view.error, 0);
        near(view.pixels[0], level.pixels[0]); // Viewing-axis ray is unchanged.
      }
      // Columns: centre, right, up, left, down. Positive viewing-frame roll
      // maps screen right onto the original downward ray, independent of yaw,
      // pitch and viewport aspect ratio.
      near(clockwise.pixels[1], level.pixels[4]); near(clockwise.pixels[2], level.pixels[1]);
      near(clockwise.pixels[3], level.pixels[2]); near(clockwise.pixels[4], level.pixels[3]);
      near(counterclockwise.pixels[1], level.pixels[2]); near(counterclockwise.pixels[2], level.pixels[3]);
      near(inverted.pixels[1], level.pixels[3]); near(inverted.pixels[2], level.pixels[4]);
      assert.notDeepEqual(oblique.pixels[1], level.pixels[1]);
    }
    for (const mode of result.unchangedModes) {
      assert.equal(mode.level.error, 0); assert.equal(mode.rolled.error, 0);
      assert.deepEqual(mode.rolled.pixels, mode.level.pixels, mode.mode);
    }
    for (const view of result.referenceViews) {
      assert.equal(view.reference.error, 0); assert.equal(view.body.error, 0);
      view.reference.pixels.forEach((pixel, i) => near(pixel, view.body.pixels[i]));
    }
    console.log(JSON.stringify({ passed: true, perspectivePixelCases: result.samples.length * 5,
      referencePixelCases: result.referenceViews.length,
      checks: ["roll-invariant-optical-axis", "quarter-turn-rays", "arbitrary-yaw-pitch", "viewport-aspect",
        "reference-matrix-composition", "raw-sphere-mask-unchanged"] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
