// External shape from OpenRocket "THE Rocket 7-26-2026.ork" (metres).
// Axial stations are measured from the camera ring (switchband centre), +z
// toward the nose. The camera ring and the airbrake plane are assumed
// placements, not CAD. Radii scale with the configured airframe diameter.
export const ORK = {
  source: "THE Rocket 7-26-2026.ork",
  radius: 0.078359, // OD 156.718 mm
  nose: { length: 0.75, shape: "tangent ogive" },
  noseStraight: 0.1016,
  switchband: 0.1016,
  mainTube: 1.6,
  boattail: { length: 0.4, aftRadius: 0.06, shape: "ellipsoid" },
  // Trapezoid fin set: OpenRocket "bottom" offset +0.14 m puts the fin's aft
  // root 0.14 m behind the main tube, over the boattail.
  fins: { count: 3, root: 0.48, tip: 0.12, sweep: 0.47216048927824744, span: 0.2386, thickness: 0.0075, aftOffset: 0.14 },
  // Rail buttons are 45 deg from the first fin.
  railButtonAngleDeg: 45,
  drogue: { diameter: 0.61, lines: 6, lineLength: 0.3, deploy: "apogee" },
  main: { diameter: 2.1336, lines: 6, lineLength: 0.3, deployAltitude: 200 },
};
const ringFromNose = 0.8516 + ORK.switchband / 2; // 0.9024 m
export const STATIONS = {
  tip: ringFromNose,
  noseBase: ringFromNose - ORK.nose.length, // +0.1524
  switchTop: ORK.switchband / 2, // +0.0508, top of switchband
  switchBottom: -ORK.switchband / 2, // -0.0508, drogue separation joint
  mainBottom: -ORK.switchband / 2 - ORK.mainTube, // -1.6508
  tail: -ORK.switchband / 2 - ORK.mainTube - ORK.boattail.length, // -2.0508
};
// Assumed first fin azimuth in body coordinates. The eyes look along +/-x;
// fins at 90/210/330 deg are mirror-symmetric between the two eyes.
export const FIN_AZIMUTH_DEG = 90;

function ogive(z) {
  // Tangent ogive of length L, base radius R; x measured from the tip.
  const L = ORK.nose.length, R = ORK.radius, rho = (R * R + L * L) / (2 * R), x = STATIONS.tip - z;
  return Math.sqrt(Math.max(0, rho * rho - (L - x) * (L - x))) + R - rho;
}
function boattail(z) {
  // Clipped ellipse, matching Transition.getRadius in OpenRocket 24.12.
  const L = ORK.boattail.length, x = STATIONS.mainBottom - z;
  const R = ORK.radius, a = ORK.boattail.aftRadius;
  const fullLength = L / Math.sqrt(1 - (a / R) ** 2);
  return R * Math.sqrt(Math.max(0, 1 - (x / fullLength) ** 2));
}
export function radiusAt(z) {
  if (z > STATIONS.tip || z < STATIONS.tail) return 0;
  if (z > STATIONS.noseBase) return ogive(z);
  if (z < STATIONS.mainBottom) return boattail(z);
  return ORK.radius;
}
// Piecewise-linear (z, r) profile, top to bottom, nominal radius. The ogive
// and boattail are finely sampled so silhouettes stay smooth at 1552 px.
export function nominalProfile() {
  const pts = [];
  const nose = 48, tail = 16;
  for (let i = 0; i <= nose; i++) {
    const t = i / nose, z = STATIONS.tip - ORK.nose.length * (1 - Math.cos(t * Math.PI / 2)) ** 0.85 * 1;
    pts.push([z, radiusAt(z)]);
  }
  pts[pts.length - 1] = [STATIONS.noseBase, ORK.radius];
  pts.push([STATIONS.mainBottom, ORK.radius]);
  for (let i = 1; i <= tail; i++) {
    const t = i / tail, z = STATIONS.mainBottom - ORK.boattail.length * t ** 1.6;
    pts.push([z, radiusAt(z)]);
  }
  pts[pts.length - 1] = [STATIONS.tail, ORK.boattail.aftRadius];
  return pts;
}
// Rail-button centres in camera-ring coordinates. The camera azimuth is an
// installation assumption; the 45-degree fin-to-button relationship is saved.
export const RAIL_BUTTONS = {
  z: [STATIONS.mainBottom + 1.2192, STATIONS.mainBottom + 0.04],
  diameter: 0.0097, neckDiameter: 0.008, height: 0.0097,
  baseHeight: 0.002, flangeHeight: 0.002,
};
export const PROFILE_POINTS = nominalProfile().length;
export function profile(radius) {
  const k = radius / ORK.radius;
  return nominalProfile().map(([z, r]) => [z, r * k]);
}
// Fin outline in its own (radial, z) plane, from the root leading edge.
export function finOutline(radius) {
  const f = ORK.fins, k = radius / ORK.radius;
  const rootTE = STATIONS.mainBottom - f.aftOffset, rootLE = rootTE + f.root;
  const tipLE = rootLE - f.sweep, tipTE = tipLE - f.tip;
  // FinSet is mounted to the cylindrical Main Airframe, including its overhang.
  const rootR = () => radius;
  return [[rootR(rootLE), rootLE], [radius + f.span, tipLE], [radius + f.span, tipTE], [rootR(rootTE), rootTE]];
}
export function finAngles() {
  return Array.from({ length: ORK.fins.count }, (_, i) => (FIN_AZIMUTH_DEG + i * 360 / ORK.fins.count) * Math.PI / 180);
}
