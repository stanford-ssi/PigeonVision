const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const root = path.join(__dirname, "../python/pigeonvision/ground/static");
const projection = `data:text/javascript;base64,${Buffer.from(fs.readFileSync(path.join(root, "projection.js"), "utf8")).toString("base64")}`;
const source = fs.readFileSync(path.join(root, "attitude.js"), "utf8").replace('"./projection.js"', JSON.stringify(projection));
const modulePromise = import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const projectionPromise = import(projection);
const near = (a, b, epsilon = 1e-10) => assert.ok(Math.abs(a - b) <= epsilon, `${a} differs from ${b}`);
const rows = flat => [[flat[0], flat[3], flat[6]], [flat[1], flat[4], flat[7]], [flat[2], flat[5], flat[8]]];
const frame = (pts, matrix, session = "demo") => ({ type: "frame", simulated: true, backend: "simulation", session_id: session, pts_us: pts,
  simulation_attitude: { frame: "ENU", source: "scene", R_world_from_rig: rows(matrix) } });

// The scene's image basis has determinant -1. Rotating this basis with a
// proper scene rotation must retain that reflection without flipping pixels.
test("only the exact orthogonal reflected DEMO image basis is accepted", async () => {
  const { DEMO_REFERENCE, validDemoBasis } = await modulePromise;
  assert.equal(validDemoBasis(rows(DEMO_REFERENCE)), true);
  for (const matrix of [[[1, 0, 0], [0, 1, 0], [0, 0, 1]], [[1, 0, 0], [0, 2, 0], [0, 0, -1]],
    [[1, 0, 0], [0, 1, 0], [0, .01, -1]], [[true, 0, 0], [0, 1, 0], [0, 0, -1]],
    [[NaN, 0, 0], [0, 1, 0], [0, 0, -1]], [[1, 0], [0, 1], [0, -1]], null]) assert.equal(validDemoBasis(matrix), false);
});

test("basis selection matches displayed PTS exactly with no future lookup or extrapolation", async () => {
  const { AttitudeTimeline, DEMO_REFERENCE } = await modulePromise;
  const timeline = new AttitudeTimeline();
  timeline.accept({ type: "session", session_id: "demo" }, 0);
  timeline.accept(frame(100, DEMO_REFERENCE), 10);
  timeline.accept(frame(200, DEMO_REFERENCE), 20);
  assert.equal(timeline.at(99, 20, true), null);
  assert.equal(timeline.at(101, 20, true), null);
  assert.equal(timeline.at(201, 20, true), null);
  assert.equal(timeline.at(100, 1000000, true).pts, 100); // paused replay keeps its frame's scene basis
  assert.equal(timeline.at(100, 500, false, 490).pts, 100);
  assert.equal(timeline.at(100, 1011, false, 0), null);
  assert.equal(timeline.at(100, 500, false, 1001), null);
  assert.equal(timeline.at(100, NaN, false, 0), null);
  assert.equal(timeline.at(100, 500, false, -1), null);
});

test("missing flags, real attitude, invalid time and retired sessions cannot enable DEMO lock", async () => {
  const { AttitudeTimeline, DEMO_REFERENCE } = await modulePromise;
  const timeline = new AttitudeTimeline();
  assert.equal(timeline.accept(frame(0, DEMO_REFERENCE), 0), false); // no transported session
  timeline.accept({ type: "session", session_id: "demo" }, 0);
  for (const change of [{ simulated: false }, { backend: "hardware" }, { pts_us: -1 }, { pts_us: .1 },
    { simulation_attitude: { frame: "NED", source: "scene", R_world_from_rig: rows(DEMO_REFERENCE) } },
    { simulation_attitude: { frame: "ENU", source: "estimator", R_world_from_rig: rows(DEMO_REFERENCE) } }])
    assert.equal(timeline.accept({ ...frame(0, DEMO_REFERENCE), ...change }, 0), false);
  assert.equal(timeline.accept(frame(0, DEMO_REFERENCE), NaN), false);
  assert.equal(timeline.accept(frame(0, DEMO_REFERENCE), 0), true);
  timeline.accept({ type: "session", session_id: "second" }, 1);
  assert.equal(timeline.samples.length, 0);
  assert.equal(timeline.accept(frame(0, DEMO_REFERENCE, "demo"), 2), false);
  timeline.accept({ type: "session", session_id: "demo" }, 3);
  assert.equal(timeline.sessionId, "second");
  timeline.accept({ type: "frame", session_id: "invalid-session" }, 4);
  assert.equal(timeline.sessionId, "second");
  timeline.reset();
  timeline.accept({ type: "session", session_id: "demo" }, 0);
  assert.equal(timeline.accept(frame(0, DEMO_REFERENCE), 0), true);
});

test("cache is bounded through out-of-order camera records and session changes", async () => {
  const { AttitudeTimeline, DEMO_REFERENCE } = await modulePromise;
  const timeline = new AttitudeTimeline();
  timeline.accept({ type: "session", session_id: "demo" }, 0);
  for (let i = 700; i >= 0; i--) {
    timeline.accept(frame(i, DEMO_REFERENCE), i);
    timeline.accept({ ...frame(i, DEMO_REFERENCE), camera_id: "B" }, i);
  }
  assert.equal(timeline.samples.length, 512);
  assert.equal(new Set(timeline.samples.map(sample => sample.pts)).size, 512);
  assert.equal(timeline.at(700, 701, true).pts, 700);
  assert.equal(timeline.at(0, 701, true), null);
  for (let i = 0; i < 40; i++) timeline.accept({ type: "session", session_id: `session-${i}` }, i);
  assert.ok(timeline.retiredSessions.size <= 32);
});

test("world-view rays remain fixed through arbitrary reflected scene image bases", async () => {
  const { DEMO_REFERENCE, imageFromReference, referenceFromImage, transformPose } = await modulePromise;
  const { multiplyBasis, perspectiveBasis } = await projectionPromise;
  const worldPose = { yaw: .6, pitch: -.3, roll: .2, fov: 1.1 };
  const expectedWorld = multiplyBasis(DEMO_REFERENCE, perspectiveBasis(worldPose.yaw, worldPose.pitch, worldPose.roll));
  for (const angle of [0, .4, -.7, 1.2]) {
    const c = Math.cos(angle), s = Math.sin(angle);
    const sceneRotation = [1, 0, 0, 0, c, s, 0, -s, c];
    const imageBasis = multiplyBasis(sceneRotation, DEMO_REFERENCE);
    const renderedImage = multiplyBasis(imageFromReference(imageBasis), perspectiveBasis(worldPose.yaw, worldPose.pitch, worldPose.roll));
    const world = multiplyBasis(imageBasis, renderedImage);
    world.forEach((value, i) => near(value, expectedWorld[i]));
    const imagePose = transformPose(worldPose, imageFromReference(imageBasis));
    const roundTrip = transformPose(imagePose, referenceFromImage(imageBasis));
    for (const key of ["yaw", "pitch", "roll", "fov"]) near(roundTrip[key], worldPose[key]);
  }
});
