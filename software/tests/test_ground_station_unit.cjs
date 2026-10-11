// Station behavior with real Navigation, MissionTimeline and HUD modules.
// DOM and renderer stubs keep these authority/pose checks independent of a GPU.
const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const root = path.join(__dirname, "../python/pigeonvision/ground/static");
const moduleURL = file => {
  let source = fs.readFileSync(path.join(root, file), "utf8");
  if (file === "attitude.js") source = source.replace('"./projection.js"', JSON.stringify(moduleURL("projection.js")));
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
};

(async () => {
  let source = fs.readFileSync(path.join(root, "station.js"), "utf8");
  for (const file of ["navigation.js", "mission.js", "hud.js", "attitude.js"]) source = source.replace(`"./${file}"`, JSON.stringify(moduleURL(file)));
  const { Station } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
  const { DEMO_REFERENCE, imageFromReference } = await import(moduleURL("attitude.js"));
  const { multiplyBasis, perspectiveBasis } = await import(moduleURL("projection.js"));

  function setup(audience = false) {
    const elements = new Map(), callbacks = new Map();
    let next = 0, now = 0, changed = 0, draws = 0;
    class Element {
      constructor() { this.dataset = {}; this.style = {}; this.attributes = new Map(); this.children = []; this.textContent = ""; }
      setAttribute(name, value) { this.attributes.set(name, value); }
      hasAttribute(name) { return this.attributes.has(name); }
      toggleAttribute(name, value) { value ? this.attributes.set(name, "") : this.attributes.delete(name); }
      replaceChildren(...children) { this.children = children; }
      querySelector() { return new Element(); }
      focus() {}
      classList = { toggle() {}, add() {}, remove() {} };
    }
    const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
    global.document = {
      body: new Element(), hidden: false,
      getElementById: get,
      createElement: () => new Element(),
      querySelector: selector => get(selector),
      querySelectorAll: selector => selector === "[data-preset]" ? get("view-presets").children : [],
      addEventListener() {},
    };
    global.window = { addEventListener() {} };
    global.location = { search: audience ? "?mode=audience" : "" };
    global.localStorage = { getItem: () => null, setItem() {} };
    global.matchMedia = () => ({ matches: false });
    global.performance = { now: () => now };
    global.requestAnimationFrame = callback => { callbacks.set(++next, callback); return next; };
    global.cancelAnimationFrame = id => callbacks.delete(id);
    const renderer = { mode: "perspective", yaw: 0, pitch: 0, roll: 0, viewTransform: null, fov: 1.92, calibration: {}, draw() { draws++; } };
    const display = { focus: { A: { zoom: 2, center: [.4, .6] }, B: { zoom: 1, center: [.5, .5] } },
      rotation: { A: 180, B: 0 }, seam: 3, colour: { requested: true, strength: { A: .4, B: .8 } }, maxSkew: 30 };
    const sent = [], applied = [];
    const station = new Station(renderer, {
      send: value => sent.push(structuredClone(value)), changed: () => changed++,
      display: () => structuredClone(display), applyDisplay: value => applied.push(structuredClone(value)),
    });
    return { station, renderer, display, sent, applied, callbacks, elements, draws: () => draws, changed: () => changed,
      frame(time) { now = time; const pending = [...callbacks.values()]; callbacks.clear(); pending.forEach(callback => callback(now)); } };
  }
  const pose = { mode: "perspective", yaw: 2, pitch: -.4, roll: .7, fov: .9 };
  const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} differs from ${expected}`);
  const basis = r => perspectiveBasis(r.yaw, r.pitch, r.roll);
  const nearBasis = (actual, expected) => actual.forEach((value, i) => near(value, expected[i]));
  const sceneBasis = angle => {
    const c = Math.cos(angle), s = Math.sin(angle);
    return multiplyBasis([1, 0, 0, 0, c, s, 0, -s, c], DEMO_REFERENCE);
  };
  const attitude = (pts, matrix, session = "demo") => ({ type: "frame", simulated: true,
    backend: "simulation", session_id: session, pts_us: pts, simulation_attitude: {
      frame: "ENU", source: "scene", R_world_from_rig: [[matrix[0], matrix[3], matrix[6]],
        [matrix[1], matrix[4], matrix[7]], [matrix[2], matrix[5], matrix[8]]],
    } });
  const demo = (r, matrix = sceneBasis(.4), pts = 100) => {
    r.station.metadata({ type: "session", session_id: "demo", clock_origin_ns: 1000 }, 10);
    r.station.updateViewFrame({ pts, now: 10, replay: true, frameAgeMs: 0, paired: true });
    r.station.metadata(attitude(pts, matrix), 10);
  };
  const padSamples = (r, pts, pressure = 101325, phase = "PAD") => {
    r.station.metadata({ type: "flight_status", session_id: "demo", pts_us: pts,
      receive_monotonic_us: 1 + pts, valid: true, phase }, 0);
    r.station.metadata({ type: "sensor_sample", session_id: "demo", pts_us: pts,
      monotonic_us: 1 + pts, source: "bmp581", valid: true, values: { pressure_pa: pressure } }, 0);
  };

  test("initial cached pose and display apply to owner without rebroadcasting", () => {
    const r = setup();
    r.station.owner(true);
    r.station.receiveView({ ...pose, display: r.display }, true);
    near(r.renderer.yaw, pose.yaw);
    near(r.renderer.pitch, pose.pitch);
    near(r.renderer.roll, pose.roll);
    near(r.renderer.fov, pose.fov);
    assert.deepEqual(r.applied, [r.display]);
    assert.equal(r.sent.length, 0);
    assert.equal(r.callbacks.size, 0);
    r.station.navigation.go({ yaw: .2, pitch: .1, fov: 1.4 });
    const transition = r.station.navigation.transition;
    r.station.receiveView({ ...pose, yaw: -1, display: r.display });
    assert.equal(r.station.navigation.transition, transition, "self echo cannot stop local preset motion");
    assert.equal(r.applied.length, 1);
    r.station.navigation.stop();
  });

  test("follower smoothing never publishes and control acquisition uses authoritative target", () => {
    const r = setup();
    r.station.receiveView({ ...pose, display: r.display });
    assert.equal(r.renderer.yaw, 0);
    r.frame(0);
    r.frame(16);
    assert.ok(r.renderer.yaw > 0 && r.renderer.yaw < pose.yaw);
    assert.equal(r.sent.length, 0);
    r.station.owner(true);
    near(r.renderer.yaw, pose.yaw);
    near(r.renderer.pitch, pose.pitch);
    near(r.renderer.roll, pose.roll);
    near(r.renderer.fov, pose.fov);
    assert.equal(r.station.navigation.followTarget, null);
    assert.equal(r.callbacks.size, 0);
    r.station.publishView();
    assert.equal(r.sent.length, 1);
    assert.equal(r.sent[0].type, "view");
    assert.equal(r.sent[0].view.mode, pose.mode);
    for (const field of ["yaw", "pitch", "roll", "fov"]) near(r.sent[0].view[field], pose[field]);
    assert.deepEqual(r.sent[0].view.display, r.display);
  });

  test("claiming before first animation frame also adopts authoritative pose", () => {
    const r = setup();
    r.station.receiveView(pose);
    r.station.owner(true);
    near(r.renderer.yaw, pose.yaw);
    near(r.renderer.roll, pose.roll);
    assert.equal(r.callbacks.size, 0);
    assert.equal(r.sent.length, 0);
  });

  test("ownership loss stops held navigation and preserves following authority", () => {
    const r = setup();
    r.station.owner(true);
    r.station.navigation.key("KeyD", true);
    r.frame(0);
    const sent = r.sent.length;
    r.station.owner(false);
    const old = r.renderer.yaw;
    r.frame(1000);
    assert.equal(r.renderer.yaw, old);
    assert.equal(r.callbacks.size, 0);
    assert.equal(r.station.navigation.keys.size, 0);
    r.station.publishView();
    assert.equal(r.sent.length, sent);
  });

  test("audience cannot become controlling and ignores keyboard navigation", () => {
    const r = setup(true);
    r.station.owner(true);
    assert.equal(r.station.controlling, false);
    r.station.keyDown({ code: "KeyD", target: { closest: () => null }, preventDefault() { throw Error("readonly key was intercepted"); } });
    assert.equal(r.callbacks.size, 0);
    assert.equal(r.sent.length, 0);
    assert.ok(r.elements.get("view-presets").children.every(button => button.disabled));
    r.station.receiveView(pose, true);
    near(r.renderer.yaw, pose.yaw);
  });

  test("same-session reset holds pressure datum until transported session arrives", () => {
    const r = setup();
    const datum = { pressure: 101325, pts: 0, sessionId: "one" };
    r.station.reference(datum);
    assert.equal(r.station.mission.pad, null);
    r.station.metadata({ type: "session", session_id: "one", clock_origin_ns: 1000000 }, 0);
    assert.deepEqual(r.station.mission.pad, datum);
    r.station.resetMission();
    assert.equal(r.station.mission.pad, null);
    r.station.metadata({ type: "session", session_id: "one", clock_origin_ns: 1000000 }, 0);
    assert.deepEqual(r.station.mission.pad, datum);
    r.station.metadata({ type: "session", session_id: "two", clock_origin_ns: 2000000 }, 0);
    assert.equal(r.station.mission.pad, null);
    r.station.reference(null);
    assert.equal(r.station.pendingReference, null);
  });

  test("pad zero uses the newly presented replay frame before diagnostics catches up", () => {
    const r = setup();
    r.station.owner(true);
    demo(r);
    padSamples(r, 1000000, 101100);
    r.station.update({ pts: 1000000, now: 0, replay: true, frameAgeMs: 0 });
    assert.equal(r.elements.get("pad-reference").disabled, false);
    r.station.resetMission();
    demo(r, sceneBasis(.4), 0);
    padSamples(r, 0);
    assert.equal(r.station.lastUpdate.pts, 1000000, "diagnostics has not observed the restart yet");
    r.elements.get("pad-reference").onclick();
    assert.deepEqual(r.station.mission.pad, { pressure: 101325, pts: 0, sessionId: "demo" });
    assert.deepEqual(r.sent.at(-1), { type: "reference", reference: r.station.mission.pad });
    assert.equal(r.station.lastUpdate.pts, 0);
  });

  test("a previously enabled pad button cannot zero after the displayed frame enters boost", () => {
    const r = setup();
    r.station.owner(true);
    demo(r, sceneBasis(.4), 0);
    padSamples(r, 0);
    r.station.update({ pts: 0, now: 0, replay: true, frameAgeMs: 0 });
    assert.equal(r.elements.get("pad-reference").disabled, false);
    padSamples(r, 1000000, 101000, "BOOST");
    r.station.updateViewFrame({ pts: 1000000, now: 0, replay: true, frameAgeMs: 0 });
    r.elements.get("pad-reference").onclick();
    assert.equal(r.station.mission.pad, null);
    assert.equal(r.sent.length, 0);
    assert.equal(r.elements.get("pad-reference").disabled, true);
  });

  test("pad zero reads the current raw-camera frame getter rather than an older pair", () => {
    const r = setup();
    r.station.owner(true);
    demo(r, sceneBasis(.4), 0);
    padSamples(r, 0, 101325);
    padSamples(r, 200000, 101300);
    r.station.update({ pts: 0, now: 0, replay: true, frameAgeMs: 0 });
    r.renderer.mode = "b";
    r.station.frame = () => ({ pts: 200000, now: 0, replay: true, frameAgeMs: 0 });
    r.elements.get("pad-reference").onclick();
    assert.deepEqual(r.station.mission.pad, { pressure: 101300, pts: 200000, sessionId: "demo" });
  });

  test("saved presets recall roll and legacy remote poses level the view", () => {
    const r = setup();
    r.station.owner(true);
    Object.assign(r.renderer, { yaw: -.3, pitch: .4, roll: .42, fov: 1.25 });
    r.station.savePreset(6);
    near(r.station.presets[6].roll, .42);
    r.renderer.roll = -.2;
    r.station.goPreset(6);
    r.frame(0);
    r.frame(550);
    near(r.renderer.roll, .42);
    r.station.owner(false);
    r.station.receiveView({ mode: "perspective", yaw: 0, pitch: 0, fov: 1.92 }, true);
    assert.equal(r.renderer.roll, 0);
    assert.equal(r.sent.at(-1).type, "view");
  });

  test("horizon is opt-in and needs exact paired DEMO attitude", () => {
    const r = setup();
    r.station.owner(true);
    assert.equal(r.elements.get("horizon-status").textContent, "Needs timed attitude");
    assert.equal(r.elements.get("horizon-lock").disabled, true);
    assert.equal(r.station.setHorizon(true), false);
    demo(r);
    assert.equal(r.station.horizonStatus.available, true);
    assert.equal(r.station.horizonStatus.active, false);
    assert.equal(r.renderer.viewTransform, null);
    assert.equal(r.elements.get("horizon-status").textContent, "Demo attitude ready");
    r.station.updateViewFrame({ paired: false });
    assert.equal(r.station.horizonStatus.available, false);
    assert.equal(r.station.setHorizon(true), false);
    r.station.updateViewFrame({ paired: true, pts: 101 });
    assert.equal(r.station.setHorizon(true), false);
    r.station.updateViewFrame({ pts: 100 });
    assert.equal(r.station.setHorizon(true), true);
    assert.equal(r.station.horizonStatus.active, true);
    assert.equal(r.sent.at(-1).view.horizon, true);
    assert.equal(r.elements.get("horizon-status").textContent, "Scene reference · demo");
    clearTimeout(r.station.sendTimer);
  });

  test("leveling keeps world-facing direction and unleveling keeps the complete visible basis", () => {
    const r = setup(), matrix = sceneBasis(.4);
    r.station.owner(true);
    demo(r, matrix);
    Object.assign(r.renderer, { yaw: .3, pitch: -.2, roll: .8, fov: 1.1 });
    const beforeWorld = multiplyBasis(matrix, basis(r.renderer));
    r.station.setHorizon(true);
    const afterWorld = multiplyBasis(DEMO_REFERENCE, basis(r.renderer));
    nearBasis(afterWorld.slice(6), beforeWorld.slice(6));
    near(r.renderer.roll, 0);
    r.renderer.roll = -.5; // manual world-frame tilt remains available
    const visible = multiplyBasis(r.renderer.viewTransform, basis(r.renderer));
    r.station.setHorizon(false);
    assert.equal(r.renderer.viewTransform, null);
    nearBasis(basis(r.renderer), visible);
    near(r.renderer.fov, 1.1);
    clearTimeout(r.station.sendTimer);
  });

  test("late metadata updates only the displayed PTS and stale input freezes a body offset", () => {
    const r = setup(), first = sceneBasis(.2), later = sceneBasis(.7);
    r.station.owner(true);
    demo(r, first);
    r.station.setHorizon(true);
    const initial = [...r.renderer.viewTransform];
    r.station.metadata(attitude(200, later), 20);
    nearBasis(r.renderer.viewTransform, initial); // newest telemetry cannot rotate an older image
    r.station.updateViewFrame({ pts: 150, now: 30 });
    assert.equal(r.station.horizonStatus.active, false);
    nearBasis(r.renderer.viewTransform, initial);
    r.station.metadata(attitude(150, later), 40);
    assert.equal(r.station.horizonStatus.active, true);
    nearBasis(r.renderer.viewTransform, imageFromReference(later));
    r.station.updateViewFrame({ replay: false, frameAgeMs: 1001, now: 50 });
    assert.equal(r.station.horizonStatus.available, false);
    assert.equal(r.station.horizonStatus.active, false);
    const fixed = [...r.renderer.viewTransform];
    r.station.metadata(attitude(201, first), 60);
    nearBasis(r.renderer.viewTransform, fixed);
    assert.equal(r.elements.get("horizon-status").textContent, "Attitude missing · body view");
    assert.equal(r.elements.get("horizon-lock").disabled, false, "the pilot can leave unavailable lock");
    r.station.setHorizon(false);
    clearTimeout(r.station.sendTimer);
  });

  test("new sessions and replay resets invalidate attitude availability", () => {
    const r = setup();
    r.station.owner(true);
    demo(r);
    r.station.setHorizon(true);
    r.station.resetMission();
    assert.equal(r.station.horizonStatus.active, false);
    assert.equal(r.station.horizonStatus.available, false);
    assert.equal(r.station.attitudes.samples.length, 0);
    r.station.metadata(attitude(100, sceneBasis(.5)), 20);
    assert.equal(r.station.horizonStatus.available, false, "unconfirmed post-reset session cannot enable lock");
    demo(r);
    assert.equal(r.station.horizonStatus.active, true);
    r.station.metadata({ type: "session", session_id: "next", clock_origin_ns: 2000 }, 30);
    assert.equal(r.station.horizonStatus.available, false);
    assert.equal(r.renderer.viewTransform, null);
    r.station.metadata(attitude(100, sceneBasis(.6)), 40);
    r.station.metadata({ type: "session", session_id: "demo", clock_origin_ns: 1000 }, 40);
    assert.equal(r.station.attitudes.sessionId, "next");
    assert.equal(r.station.horizonStatus.available, false);
    clearTimeout(r.station.sendTimer);
  });

  test("presets retain their reference frame and Camera A remains a body view", () => {
    const r = setup();
    r.station.owner(true);
    demo(r);
    r.station.setHorizon(true);
    Object.assign(r.renderer, { yaw: .2, pitch: -.3, roll: .4, fov: 1.2 });
    r.station.savePreset(6);
    assert.equal(r.station.presets[6].horizon, true);
    r.station.goPreset(0);
    r.frame(0); r.frame(550);
    assert.equal(r.station.horizonRequested, false);
    assert.equal(r.elements.get("horizon-lock").checked, false);
    near(r.renderer.yaw, 0); near(r.renderer.roll, 0);
    r.station.goPreset(6);
    r.frame(550); r.frame(1100);
    assert.equal(r.station.horizonRequested, true);
    assert.equal(r.station.horizonStatus.active, true);
    near(r.renderer.yaw, .2); near(r.renderer.pitch, -.3); near(r.renderer.roll, .4);
    r.station.navigation.stop();
    clearTimeout(r.station.sendTimer);
  });

  test("audience follows the shared frame choice but cannot enable or publish lock", () => {
    const r = setup(true);
    demo(r);
    assert.equal(r.station.setHorizon(true), false);
    r.station.receiveView({ ...pose, horizon: true }, true);
    assert.equal(r.station.horizonStatus.active, true);
    assert.equal(r.elements.get("horizon-lock").checked, true);
    assert.equal(r.elements.get("horizon-lock").disabled, true);
    assert.equal(r.sent.length, 0);
    r.station.receiveView({ ...pose, horizon: "true" }, true);
    assert.equal(r.station.horizonRequested, true);
    r.station.receiveView(pose, true);
    assert.equal(r.station.horizonRequested, false, "legacy shared view uses body coordinates");
    assert.equal(r.renderer.viewTransform, null);
    assert.equal(r.sent.length, 0);
  });

  test("mode changes select the displayed pair basis instead of a newer raw-camera PTS", () => {
    const r = setup(), pairedBasis = sceneBasis(.2), rawBasis = sceneBasis(.7);
    r.station.owner(true);
    demo(r, pairedBasis, 0);
    r.station.metadata(attitude(200000, rawBasis), 0);
    r.station.frame = () => ({ pts: r.renderer.mode === "b" ? 200000 : 0,
      now: 0, replay: true, frameAgeMs: 0, paired: true });
    r.renderer.mode = "b";
    r.station.update({ pts: 200000, now: 0, replay: true, frameAgeMs: 0, paired: true });
    r.renderer.mode = "perspective";
    r.station.viewChanged();
    assert.equal(r.station.viewFrame.pts, 0);
    assert.equal(r.station.setHorizon(true), true);
    nearBasis(r.renderer.viewTransform, imageFromReference(pairedBasis));
    // A follower's remote mode change must use the same displayed-image rule.
    r.station.owner(false);
    r.renderer.mode = "b";
    r.station.update({ pts: 200000, now: 0, replay: true, frameAgeMs: 0, paired: true });
    r.station.receiveView({ ...pose, horizon: true }, true);
    assert.equal(r.station.horizonStatus.pts, 0);
    nearBasis(r.renderer.viewTransform, imageFromReference(pairedBasis));
    clearTimeout(r.station.sendTimer);
  });
})().catch(error => { console.error(error); process.exitCode = 1; });
