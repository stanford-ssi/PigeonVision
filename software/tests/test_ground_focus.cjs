const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const source = fs.readFileSync(path.resolve(__dirname, "../python/pigeonvision/ground/static/projection.js"));
const modulePromise = import(`data:text/javascript;base64,${source.toString("base64")}`);
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-10, `${actual} != ${expected}`);

test("perspective zoom follows focal scale and stored FOV must be finite and bounded", async () => {
  const { perspectiveZoom, validPerspectiveFov, DEFAULT_PERSPECTIVE_FOV } = await modulePromise;
  near(perspectiveZoom(DEFAULT_PERSPECTIVE_FOV), 1);
  near(perspectiveZoom(Math.PI / 2), Math.tan(55 * Math.PI / 180));
  assert.ok(Math.abs(perspectiveZoom(Math.PI / 2) - 110 / 90) > .1);
  for (const value of [.25, DEFAULT_PERSPECTIVE_FOV, 2.6]) assert.equal(validPerspectiveFov(value), true);
  for (const value of [.249, 2.601, null, undefined, "1.2", true, NaN, Infinity, {}, []])
    assert.equal(validPerspectiveFov(value), false);
});

test("1x fits the complete image and cannot pan out of its centred letterbox", async () => {
  const { rawViewTransform } = await modulePromise;
  const layout = rawViewTransform([1600, 900], [1552, 1552], 1, [0, 1]);
  assert.deepEqual(layout.center, [.5, .5]);
  near(layout.span[0], 16 / 9);
  near(layout.span[1], 1);
});

test("4x shows a centred quarter-height source crop without changing aspect", async () => {
  const { rawViewTransform } = await modulePromise;
  const layout = rawViewTransform([1600, 900], [1552, 1552], 4);
  near(layout.span[0], 4 / 9);
  near(layout.span[1], .25);
  assert.deepEqual(layout.center, [.5, .5]);
  near(layout.center[1] - layout.span[1] / 2, .375);
  near(layout.center[1] + layout.span[1] / 2, .625);
});

test("panning clamps every visible edge inside the source when zoom fills the view", async () => {
  const { rawViewTransform } = await modulePromise;
  for (const size of [[1600, 900], [900, 1600]]) {
    for (const center of [[-10, 10], [10, -10]]) {
      const layout = rawViewTransform(size, [2064, 1552], 4, center);
      for (let axis = 0; axis < 2; axis++) {
        assert.ok(layout.center[axis] - layout.span[axis] / 2 >= -1e-12);
        assert.ok(layout.center[axis] + layout.span[axis] / 2 <= 1 + 1e-12);
      }
    }
  }
});

test("drag follows the pointer in image coordinates and remains bounded after resize", async () => {
  const { rawViewTransform, rawDragCenter } = await modulePromise;
  const layout = rawViewTransform([1600, 900], [1552, 1552], 4);
  const center = rawDragCenter(layout, [160, 90], [1600, 900]);
  near(center[0], .5 - .1 * 4 / 9);
  near(center[1], .5 - .1 * .25);
  const resized = rawViewTransform([5000, 400], [1552, 1552], 4, center);
  assert.equal(resized.center[0], .5); // Image narrower than viewport: no extra pan.
});

test("focus zoom is bounded from fit to8x and resetting restores full image", async () => {
  const { rawViewTransform } = await modulePromise;
  assert.equal(rawViewTransform([100, 100], [100, 100], 99).zoom, 8);
  const reset = rawViewTransform([100, 100], [100, 100], 1, [.1, .9]);
  assert.deepEqual(reset, { zoom: 1, span: [1, 1], center: [.5, .5] });
});


test("rotated focus panning follows the pointer without changing source crop bounds", async () => {
  const { rawViewTransform, rawDragCenter } = await modulePromise;
  const layout = rawViewTransform([800, 600], [1552, 1552], 4, [.3, .7]);
  for (const rotation of [0, 180]) {
    const direction = rotation === 180 ? -1 : 1;
    const moved = rawDragCenter(layout, [80, 60], [800, 600], rotation);
    const after = rawViewTransform([800, 600], [1552, 1552], 4, moved);
    for (let axis = 0; axis < 2; axis++) {
      // The source feature initially at viewport centre follows a 10% screen drag.
      const featurePosition = .5 + (layout.center[axis] - after.center[axis]) / (layout.span[axis] * direction);
      near(featurePosition, .6);
      assert.ok(after.center[axis] - after.span[axis] / 2 >= 0);
      assert.ok(after.center[axis] + after.span[axis] / 2 <= 1);
    }
  }
});
