// Shared scene geometry for the camera renderer and the observer.
// Coordinates are metres with the camera ring at body z = 0.
import { ORK, STATIONS, profile, finOutline, finAngles } from "./rocket.js";
export const AIRFRAME = {
  bottom: STATIONS.tail, // -2.0508
  shoulder: STATIONS.noseBase, // +0.1524
  noseLength: ORK.nose.length,
  tip: STATIONS.tip,
  joint: STATIONS.switchBottom, // drogue separation joint
  finSpan: ORK.fins.span,
};
// Illustrative recovery layout (not from the .ork): the drogue shares one
// shock cord tied between the avionics bay and the booster.
export const RECOVERY = {
  upperCord: 2.6, // swivel to avionics-bay aft bulkhead
  lowerCord: 4.4, // swivel to booster forward end
  lines: ORK.drogue.lineLength, // saved individual shroud length
  drogueRadius: ORK.drogue.diameter / 2,
  projectedFraction: 0.8, // assumed inflated projection; canopy pattern is absent
  lineCount: ORK.drogue.lines,
};
export const add = (a, b) => a.map((v, i) => v + b[i]);
export const sub = (a, b) => a.map((v, i) => v - b[i]);
export const mul = (a, k) => a.map((v) => v * k);
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => mul(a, 1 / Math.hypot(...a));
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const ease = (x) => { const t = clamp(x, 0, 1); return t * t * (3 - 2 * t); };
export function bodyToWorld(v, s) {
  const r = (s.roll || 0) * Math.PI / 180, t = (s.tilt || 0) * Math.PI / 180;
  const x = Math.cos(r) * v[0] - Math.sin(r) * v[1], y = Math.sin(r) * v[0] + Math.cos(r) * v[1];
  const X = Math.cos(t) * x + Math.sin(t) * v[2], h = (s.heading || 0) * Math.PI / 180;
  return [Math.cos(h) * X - Math.sin(h) * y, Math.sin(h) * X + Math.cos(h) * y, -Math.sin(t) * x + Math.cos(t) * v[2]];
}
export function worldToBody(v, s) {
  const h = -(s.heading || 0) * Math.PI / 180;
  v = [Math.cos(h) * v[0] - Math.sin(h) * v[1], Math.sin(h) * v[0] + Math.cos(h) * v[1], v[2]];
  const r = -(s.roll || 0) * Math.PI / 180, t = -(s.tilt || 0) * Math.PI / 180;
  const x = Math.cos(t) * v[0] + Math.sin(t) * v[2], z = -Math.sin(t) * v[0] + Math.cos(t) * v[2];
  return [Math.cos(r) * x - Math.sin(r) * v[1], Math.sin(r) * x + Math.cos(r) * v[1], z];
}
export const bodyRadius = (s) => (s.diameter || ORK.radius * 2000) / 2000;
export const airframeProfile = (s) => profile(bodyRadius(s));
// Fin quads in rocket coordinates.
export function finFaces(radius) {
  const outline = finOutline(radius);
  return finAngles().map((a) => outline.map(([r, z]) => [Math.cos(a) * r, Math.sin(a) * r, z]));
}
export function finSurfaces(radius) {
  return finFaces(radius).flatMap(q => {
    const n = mul(norm(cross(sub(q[1], q[0]), sub(q[3], q[0]))), ORK.fins.thickness / 2);
    const a = q.map(p => add(p, n)), b = q.map(p => sub(p, n));
    return [a, b, ...q.map((_,i) => [a[i],a[(i+1)%4],b[(i+1)%4],b[i]])];
  });
}
export function cameraBoxes(s) {
  const r = bodyRadius(s), outer = r + s.stand / 1000 - 0.001, inner = r - 0.012;
  return [1, -1].map((sign) => ({ center: [sign * (outer + inner) / 2, 0, 0], half: [(outer - inner) / 2, 0.022, 0.019] }));
}
export function boxFaces({ center: c, half: h }) {
  const v = Array.from({ length: 8 }, (_, i) => c.map((x, k) => x + ((i >> k) & 1 ? 1 : -1) * h[k]));
  return [[0, 2, 3, 1], [4, 5, 7, 6], [0, 1, 5, 4], [2, 6, 7, 3], [0, 4, 6, 2], [1, 3, 7, 5]].map((ids) => ids.map((i) => v[i]));
}

// Camera-section attitude after separation. The avionics bay and nose hang
// from the bay's aft bulkhead, so they swing from nose-up to nose-down below
// the drogue: a damped pendulum released from inverted (phi from world down).
function hangAngle(tau, start) {
  if (tau <= 0) return start;
  const zeta = 0.5, w = 2.5, wd = w * Math.sqrt(1 - zeta * zeta), k = Math.exp(-zeta * w * tau);
  const step = k * (Math.cos(wd * tau) + zeta / Math.sqrt(1 - zeta * zeta) * Math.sin(wd * tau));
  // Residual pendulum swing under the drogue (~3.5 s period).
  const swing = 6 * Math.sin(1.8 * tau + 0.4) * ease(tau / 2.5) * Math.exp(-tau / 9);
  return start * step + swing;
}
export function recoveryAttitude(t, row, summary) {
  const tau = t - summary.deployment_time_s;
  if (tau <= 0) return { tilt: row.tilt_deg, roll: row.roll_deg, speed: row.roll_rate_rps, tau };
  const phi = hangAngle(tau, 180 - row.tilt_deg);
  // Slow swivel spin and a little cord wind-up.
  return { tilt: 180 - phi, roll: row.roll_deg + 11 * tau + 9 * Math.sin(0.7 * tau), speed: 0, tau };
}

// World-frame recovery geometry, relative to the camera ring.
export function recoveryPose(s) {
  const tau = s.tau ?? -1, R = RECOVERY;
  const empty = { separated: false, inflation: 0, lines: [], tau };
  if (!(tau > 0)) return empty;
  const u = bodyToWorld([0, 0, 1], s); // camera-section axis, toward the nose
  const attach = bodyToWorld([0, 0, AIRFRAME.joint], s);
  const payout = ease(tau / 0.75), payoutLow = ease(tau / 1.05);
  const swivel = sub(attach, mul(u, R.upperCord * payout + 0.05));
  const up = [0, 0, 1];
  // The booster drops away along the old axis, then hangs 12 deg off the
  // camera section so its cord clears the bay.
  const settle = ease((tau - 0.4) / 1.6);
  const w = norm([-Math.sin(0.21 * settle) + 0.04 * Math.sin(1.3 * tau), 0.05 * Math.sin(0.9 * tau + 1) * settle, -1]);
  const boosterTop = add(swivel, mul(w, R.lowerCord * payoutLow + 0.05));
  const boosterAxis = mul(w, -1); // booster +z points up its cord
  const spin = 0.5 + 0.23 * tau;
  let bx = norm(cross([0, 1, 0], boosterAxis)); const by0 = cross(boosterAxis, bx);
  const bxs = add(mul(bx, Math.cos(spin)), mul(by0, Math.sin(spin))), bys = cross(boosterAxis, bxs);
  // Local booster point p (rocket coords) maps to boosterTop + M (p - joint).
  const booster = { top: boosterTop, x: bxs, y: bys, z: boosterAxis };
  // Drogue trails upwind (world up); early on it streams clear of the bay.
  const inflation = ease((tau - 0.3) / 0.7);
  const lateral = norm([u[0] + 1e-6, u[1], 0]);
  const lean = (1 - ease((tau - 0.2) / 1.4)) * 0.9;
  const axis = norm(add(add(up, mul(lateral, -lean)), [0.05 * Math.sin(2.1 * tau), 0.06 * Math.sin(1.7 * tau + 2), 0]));
  const breathe = 1 + 0.035 * Math.sin(11 * tau) * inflation;
  const radius = R.drogueRadius * R.projectedFraction * (0.25 + 0.75 * inflation) * breathe, depth = radius * (0.9 - 0.25 * inflation);
  const skirtRadius = radius * Math.sqrt(1 - 0.15 ** 2);
  const shroudAxial = Math.sqrt(Math.max(0, R.lines ** 2 - skirtRadius ** 2));
  const canopy = add(swivel, mul(axis, shroudAxial + depth * 0.15));
  const lines = [];
  // Cords bow while slack, straighten once loaded.
  const bow = (a, b, amount, seed) => {
    const mid = mul(add(a, b), 0.5), side = norm(cross(sub(b, a), [0.3, 1, 0.2]));
    const m = add(mid, mul(side, amount * Math.sin(seed + tau * 3)));
    lines.push([a, m], [m, b]);
  };
  bow(attach, swivel, 0.35 * (1 - ease(tau / 0.9)) + 0.015, 1);
  bow(swivel, add(boosterTop, mul(boosterAxis, 0.0)), 0.5 * (1 - ease(tau / 1.2)) + 0.02, 2);
  const ex = norm(cross(axis, [0.2, 0.9, 0.1])), ey = cross(axis, ex);
  const skirt = -0.15 * depth;
  for (let i = 0; i < R.lineCount; i++) {
    const a = i * 2 * Math.PI / R.lineCount;
    const rim = add(add(canopy, mul(axis, skirt)), add(mul(ex, skirtRadius * Math.cos(a)), mul(ey, skirtRadius * Math.sin(a))));
    lines.push([swivel, rim]);
  }
  return { separated: true, tau, attach, swivel, booster, canopy, axis, ex, ey, radius, depth, inflation, lines };
}
// Drogue canopy as a gored spherical cap (for the observer).
export function canopyFaces(pose) {
  if (!pose.separated) return [];
  const faces = [], end = Math.acos(-0.15), gores = 12;
  const point = (a, t) => add(pose.canopy, add(mul(pose.axis, pose.depth * Math.cos(t)), add(mul(pose.ex, pose.radius * Math.sin(t) * Math.cos(a)), mul(pose.ey, pose.radius * Math.sin(t) * Math.sin(a)))));
  for (let i = 0; i < gores; i++) for (let j = 0; j < 5; j++) {
    const a = i * 2 * Math.PI / gores, b = (i + 1) * 2 * Math.PI / gores, t = j * end / 5, v = (j + 1) * end / 5;
    faces.push({ points: [point(a, t), point(b, t), point(b, v), point(a, v)], color: i % 2 === 0 ? "#b5532a" : "#2b2a27" });
  }
  return faces;
}
export const boosterToWorld = (p, b) => add(b.top, add(mul(b.x, p[0]), add(mul(b.y, p[1]), mul(b.z, p[2] - AIRFRAME.joint))));

export function opticalState(t, row, summary, params, preset, field, mesh, outline) {
  const deploy =
    t < 10 ? 0
      : t < 12 ? (t - 10) / 2
        : t < summary.apogee_time_s - 3 ? 1
          : Math.max(0, (summary.apogee_time_s - t) / 3);
  const attitude = recoveryAttitude(t, row, summary);
  return {
    ...params,
    time: t,
    preset,
    mesh,
    outline,
    field,
    // Camera ring height above the pad ground (the tail rests on the pad).
    altitude: row.altitude_m - AIRFRAME.bottom,
    east: row.east_m || 0,
    north: row.north_m || 0,
    heading: row.heading_deg || 0,
    roll: attitude.roll,
    tilt: attitude.tilt,
    speed: attitude.speed,
    tau: attitude.tau,
    deploy,
    noseOffset: 0,
    chuteOpen: attitude.tau > 0 ? ease((attitude.tau - 0.3) / 0.7) : 0,
  };
}
// Launch rail beside the airframe, on the rail-button side (45 deg from fin 1).
export const LAUNCHER = { railAzimuth: (90 + 45) * Math.PI / 180, length: 5.5 };
