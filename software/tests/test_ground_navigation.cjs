const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");

(async () => {
  const source = fs.readFileSync(path.join(__dirname, "../python/pigeonvision/ground/static/navigation.js"), "utf8");
  const { Navigation, wrapYaw, validPose, validPresets, DEFAULT_PRESETS } =
    await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
  function rig(initial = {}) {
    const queued = new Map();
    let next = 0, draws = 0;
    global.requestAnimationFrame = callback => { queued.set(++next, callback); return next; };
    global.cancelAnimationFrame = id => queued.delete(id);
    const view = { yaw: 0, pitch: 0, roll: 0, fov: 1.92, ...initial };
    const nav = new Navigation(view, () => draws++);
    return { view, nav, queued, draws: () => draws, frame(now) {
      const callbacks = [...queued.values()];
      queued.clear();
      callbacks.forEach(callback => callback(now));
    } };
  }
  const near = (actual, expected, tolerance = 1e-10) =>
    assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} differs from ${expected}`);

  test("navigation validates stored presets and wraps equivalent yaw angles", () => {
    assert.equal(validPresets(DEFAULT_PRESETS), true);
    assert.equal(validPresets(DEFAULT_PRESETS.slice(1)), false);
    assert.equal(validPresets([...DEFAULT_PRESETS.slice(0, 8), { yaw: 0, pitch: 0, fov: 1, name: "x".repeat(33) }]), false);
    for (const pose of [{ yaw: NaN, pitch: 0, fov: 1 }, { yaw: 0, pitch: 1.57, fov: 1 }, { yaw: 0, pitch: 0, fov: 0 }])
      assert.ok(!validPose(pose));
    near(wrapYaw(3 * Math.PI), -Math.PI);
    near(wrapYaw(-5 * Math.PI / 2), -Math.PI / 2);
  });

  test("held-key motion depends on elapsed time, with comparable 30/60/120 Hz motion", () => {
    function motion(hz) {
      const r = rig();
      r.nav.key("ArrowRight", true);
      r.frame(0);
      for (let frame = 1; frame <= hz; frame++) r.frame(frame * 1000 / hz);
      const angle = r.view.yaw;
      r.nav.stop();
      return angle;
    }
    const at30 = motion(30), at60 = motion(60), at120 = motion(120);
    // Exponential acceleration is integrated with finite frame steps; allow its bounded discretization error.
    near(at30, at120, .012);
    near(at60, at120, .005);
    for (const angle of [at30, at60, at120]) assert.ok(angle > .80 && angle < .84);
  });

  test("diagonal normalization and Shift precision limit angular speed", () => {
    const diagonal = rig();
    diagonal.nav.key("KeyD", true);
    diagonal.nav.key("KeyW", true);
    diagonal.frame(0);
    for (let i = 1; i <= 60; i++) diagonal.frame(i * 1000 / 60);
    near(diagonal.view.yaw, diagonal.view.pitch);
    const distance = Math.hypot(diagonal.view.yaw, diagonal.view.pitch);
    assert.ok(distance < .84 && distance > .80);
    diagonal.nav.stop();
    const precision = rig();
    precision.nav.key("ShiftLeft", true);
    precision.nav.key("KeyD", true);
    precision.frame(0);
    for (let i = 1; i <= 60; i++) precision.frame(i * 1000 / 60);
    assert.ok(precision.view.yaw > .24 && precision.view.yaw < .27);
    precision.nav.stop();
  });

  test("large animation gaps are bounded and pitch never passes the poles", () => {
    const r = rig({ yaw: Math.PI - .005, pitch: 1.55 });
    r.nav.key("ArrowRight", true);
    r.nav.key("ArrowUp", true);
    r.frame(0);
    r.frame(10000);
    assert.ok(Math.abs(wrapYaw(r.view.yaw - (Math.PI - .005))) < .05);
    assert.equal(r.view.pitch, 1.56);
    assert.ok(r.view.yaw >= -Math.PI && r.view.yaw < Math.PI);
    r.nav.stop();
    const down = rig({ pitch: -1.55 });
    down.nav.key("ArrowDown", true);
    down.frame(0);
    down.frame(50);
    assert.equal(down.view.pitch, -1.56);
    down.nav.stop();
  });

  test("presets follow the shortest yaw path and finish at the requested pose", () => {
    const r = rig({ yaw: 3.1, pitch: -.4, fov: 2 });
    r.nav.go({ yaw: -3.1, pitch: .6, fov: 1 });
    r.frame(0);
    r.frame(275);
    near(wrapYaw(r.view.yaw - Math.PI), 0);
    near(r.view.pitch, .1);
    near(r.view.fov, 1.5);
    r.frame(550);
    near(r.view.yaw, -3.1);
    near(r.view.pitch, .6);
    near(r.view.fov, 1);
    assert.equal(r.queued.size, 0);
    r.nav.go({ yaw: .4, pitch: -.2, fov: .8 }, true);
    r.frame(600);
    near(r.view.yaw, .4);
    assert.equal(r.queued.size, 0);
  });

  test("stop used on blur cancels held motion and presets with no residual frame", () => {
    const r = rig();
    assert.equal(r.nav.key("UnrecognizedKey", true), false);
    r.nav.key("KeyD", true);
    r.nav.key("KeyD", true);
    assert.equal(r.queued.size, 1);
    r.frame(0);
    r.frame(50);
    const pose = { ...r.view }, draws = r.draws();
    r.nav.stop();
    assert.equal(r.nav.keys.size, 0);
    assert.deepEqual(r.nav.velocity, [0, 0, 0]);
    assert.equal(r.queued.size, 0);
    r.frame(1000);
    assert.deepEqual(r.view, pose);
    assert.equal(r.draws(), draws);
    r.nav.go({ yaw: 1, pitch: 0, fov: 1 });
    r.nav.stop();
    assert.equal(r.nav.transition, null);
    assert.equal(r.queued.size, 0);
  });

  test("remote following crosses yaw seam on the short path without overshoot", () => {
    const r = rig({ yaw: 3.1, pitch: -.4, fov: 2 });
    const target = { yaw: -3.1, pitch: .6, fov: 1 };
    r.nav.follow(target);
    r.frame(0);
    const distance = wrapYaw(target.yaw - r.view.yaw);
    for (let i = 1; i <= 40; i++) {
      const previous = r.view.yaw;
      r.frame(i * 1000 / 60);
      assert.ok(wrapYaw(r.view.yaw - previous) >= -1e-10);
      assert.ok(Math.abs(wrapYaw(target.yaw - r.view.yaw)) <= distance + 1e-10);
      assert.ok(r.view.pitch >= -.4 && r.view.pitch <= target.pitch);
      assert.ok(r.view.fov >= target.fov && r.view.fov <= 2);
    }
    near(r.view.yaw, target.yaw);
    near(r.view.pitch, target.pitch);
    near(r.view.fov, target.fov);
    assert.equal(r.nav.followTarget, null);
    assert.equal(r.queued.size, 0);
  });

  test("20 Hz remote stream follows at display rates without predicting future poses", () => {
    function follow(hz) {
      const r = rig();
      let target = 0;
      r.nav.follow({ yaw: target, pitch: 0, fov: 1.92 });
      r.frame(0);
      for (let i = 1; i <= hz; i++) {
        const time = i * 1000 / hz;
        const received = Math.floor((time + 1e-6) / 50) * .045;
        if (received > target) {
          target = received;
          r.nav.follow({ yaw: target, pitch: 0, fov: 1.92 });
        }
        r.frame(time);
        assert.ok(r.view.yaw <= target + 1e-10, "follower cannot extrapolate beyond received angle");
      }
      const angle = r.view.yaw;
      assert.ok(target - angle < .06);
      r.nav.stop();
      assert.equal(r.nav.followTarget, null);
      assert.equal(r.queued.size, 0);
      return angle;
    }
    const at30 = follow(30), at60 = follow(60), at120 = follow(120);
    near(at30, at120, .03);
    near(at60, at120, .02);
  });

  test("Q/E roll is smooth, wrapped and frame-rate independent with Shift fine control", () => {
    function motion(code, hz, fine = false) {
      const r = rig();
      if (fine) r.nav.key("ShiftLeft", true);
      assert.equal(r.nav.key(code, true), true);
      r.frame(0);
      const deltas = [];
      for (let i = 1; i <= hz; i++) {
        const before = r.view.roll;
        r.frame(i * 1000 / hz);
        deltas.push(wrapYaw(r.view.roll - before));
      }
      assert.equal(r.view.yaw, 0);
      assert.equal(r.view.pitch, 0);
      assert.equal(r.view.fov, 1.92);
      assert.ok(Math.abs(deltas[0]) < Math.abs(deltas.at(-1)), "roll accelerates gradually");
      const angle = r.view.roll;
      r.nav.stop();
      return angle;
    }
    const at30 = motion("KeyE", 30), at60 = motion("KeyE", 60), at120 = motion("KeyE", 120);
    near(at30, at120, .012);
    near(at60, at120, .005);
    assert.ok(at60 > .8 && at60 < .84);
    near(motion("KeyQ", 60), -at60);
    const fine = motion("KeyE", 60, true);
    assert.ok(fine > .24 && fine < .27);
    const r = rig({ roll: Math.PI - .005 });
    r.nav.key("KeyE", true);
    r.frame(0);
    r.frame(50);
    assert.ok(r.view.roll < 0 && r.view.roll >= -Math.PI);
    assert.ok(wrapYaw(r.view.roll - (Math.PI - .005)) < .05);
    r.nav.stop();
  });

  test("roll presets and remote following use the shortest wrap path", () => {
    const target = { yaw: .2, pitch: -.3, roll: -3.1, fov: 1.3 };
    const r = rig({ roll: 3.1 });
    r.nav.go(target);
    r.frame(0);
    r.frame(275);
    near(wrapYaw(r.view.roll - Math.PI), 0);
    r.frame(550);
    near(r.view.roll, -3.1);
    const follower = rig({ roll: 3.1 });
    follower.nav.follow(target);
    follower.frame(0);
    for (let i = 1; i <= 40; i++) {
      const before = follower.view.roll;
      follower.frame(i * 1000 / 60);
      assert.ok(wrapYaw(follower.view.roll - before) >= -1e-10);
      assert.ok(Math.abs(wrapYaw(target.roll - follower.view.roll)) <= .0832);
    }
    near(follower.view.roll, target.roll);
    assert.equal(follower.queued.size, 0);
  });

  test("legacy presets level roll while invalid roll cannot enter navigation", () => {
    const legacy = DEFAULT_PRESETS.map(p => {
      if (!p) return p;
      const { roll, ...old } = p;
      return old;
    });
    assert.equal(validPresets(legacy), true);
    for (const roll of [null, true, "0", NaN, Infinity, Math.PI + .001])
      assert.ok(!validPose({ yaw: 0, pitch: 0, fov: 1.2, roll }));
    const r = rig({ roll: .9 });
    r.nav.go(legacy[0], true);
    r.frame(0);
    assert.equal(r.view.roll, 0);
    assert.equal(r.nav.pose().roll, 0);
    r.nav.follow({ ...legacy[0], roll: NaN });
    assert.equal(r.queued.size, 0);
  });
})().catch(error => { console.error(error); process.exitCode = 1; });
