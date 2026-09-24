import { PRESETS } from "./engine.js";
import { STATIONS } from "./rocket.js";
import { AIRFRAME, finSurfaces, cameraBoxes, boxFaces, recoveryPose, canopyFaces, bodyToWorld, boosterToWorld, airframeProfile, bodyRadius, LAUNCHER } from "./scene.js";
// An engineering observer, never a camera or downlinked view. The ring view
// is in body coordinates; the full view is gravity-aligned, centred on the ring.
const LIVERY = (z) =>
  z < STATIONS.switchTop && z > STATIONS.switchBottom ? [44, 48, 47]
    : z <= STATIONS.switchBottom && z > STATIONS.mainBottom ? [47, 74, 61]
      : [222, 221, 214];
export class RigView {
  constructor(canvas) {
    this.canvas = canvas;
    this.az = -0.9;
    this.el = 0.35;
    this.full = false;
    this.bubbles = true;
    this.drag = null;
    this.state = null;
    canvas.addEventListener("pointerdown", (e) => {
      this.drag = [e.clientX, e.clientY, this.az, this.el];
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!this.drag) return;
      this.az = this.drag[2] - (e.clientX - this.drag[0]) * 0.009;
      this.el = Math.max(-1.3, Math.min(1.3, this.drag[3] + (e.clientY - this.drag[1]) * 0.009));
      this.draw(this.state);
    });
    for (const k of ["pointerup", "pointercancel"])
      canvas.addEventListener(k, () => (this.drag = null));
  }
  draw(s) {
    if (!s) return;
    this.state = s;
    const c = this.canvas,
      dpr = Math.min(devicePixelRatio, 2),
      w = c.clientWidth || 420,
      h = c.clientHeight || 400;
    if (c.width !== Math.round(w * dpr)) c.width = Math.round(w * dpr);
    if (c.height !== Math.round(h * dpr)) c.height = Math.round(h * dpr);
    const x = c.getContext("2d");
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    x.clearRect(0, 0, w, h);
    const full = this.full, pose = recoveryPose(s), R = bodyRadius(s);
    // Full view: world axes. Body points go through the attitude.
    const B = full ? (p) => bodyToWorld(p, s) : (p) => p;
    const booster = pose.separated ? (p) => boosterToWorld(p, pose.booster) : B;
    const pts = [B([0, 0, STATIONS.tip]), B([0, 0, STATIONS.tail])];
    if (full && pose.separated) pts.push(add3(pose.canopy, mul3(pose.axis, pose.depth)), booster([0, 0, STATIONS.tail]), pose.swivel);
    const lowest = Math.min(...pts.map((p) => p[2])), highest = Math.max(...pts.map((p) => p[2]));
    const center = full ? (highest + lowest) / 2 : 0.02,
      scale = full ? Math.min(h / ((highest - lowest) * 1.25 + 0.4), w / (pose.separated ? 3.2 : 1.2)) : Math.min(w, h) / 0.95,
      ca = Math.cos(this.az), sa = Math.sin(this.az), ce = Math.cos(this.el), se = Math.sin(this.el),
      eyeDist = full ? 14 : 2.4;
    const project = ([a, b, z]) => {
      const X = -sa * a + ca * b,
        Y = -se * (ca * a + sa * b) + ce * (z - center),
        D = ce * (ca * a + sa * b) + se * (z - center),
        persp = eyeDist / (eyeDist - D);
      return [w * 0.5 + X * scale * persp, h * 0.52 - Y * scale * persp, D];
    };
    const view = [ce * ca, ce * sa, se];
    const line = (points, color, width = 1, dash = []) => {
      x.strokeStyle = color; x.lineWidth = width; x.setLineDash(dash); x.beginPath();
      points.forEach((p, i) => { const q = project(p); i ? x.lineTo(q[0], q[1]) : x.moveTo(q[0], q[1]); });
      x.stroke(); x.setLineDash([]);
    };
    // A light technical grid gives the two pupils a visible separation.
    const gz = full ? lowest - 0.15 : -0.3, gs = full ? 0.25 : 0.1;
    for (let i = -5; i <= 5; i++) {
      line([[i * gs, -5 * gs, gz], [i * gs, 5 * gs, gz]], "#deded0", 0.7);
      line([[-5 * gs, i * gs, gz], [5 * gs, i * gs, gz]], "#deded0", 0.7);
    }
    const polys = [];
    const light = norm3([-0.45, -0.35, 0.82]);
    const addPoly = (points, rgb, normal, stroke = "transparent") => {
      const q = points.map(project);
      let fill = rgb;
      if (typeof rgb !== "string") {
        const k = normal ? 0.72 + 0.28 * Math.abs(dot3(normal, light)) : 1;
        fill = `rgb(${rgb.map((v) => Math.round(v * k)).join(",")})`;
      }
      polys.push({ q, fill, stroke, depth: q.reduce((a, p) => a + p[2], 0) / q.length });
    };
    // Revolved airframe from the OpenRocket profile.
    const prof = airframeProfile(s), N = full ? 28 : 48;
    const lo = full ? STATIONS.tail : -0.3, hi = full ? STATIONS.tip : 0.42;
    const revolve = (map, zMin, zMax) => {
      for (let i = 0; i < prof.length - 1; i++) {
        let [z0, r0] = prof[i], [z1, r1] = prof[i + 1];
        if (z1 >= zMax || z0 <= zMin) continue;
        const cut = (z) => r0 + (r1 - r0) * (z - z0) / (z1 - z0);
        if (z0 > zMax) { r0 = cut(zMax); z0 = zMax; }
        if (z1 < zMin) { r1 = cut(zMin); z1 = zMin; }
        const rgb = LIVERY((z0 + z1) / 2);
        for (let j = 0; j < N; j++) {
          const a = (j / N) * Math.PI * 2, b = ((j + 1) / N) * Math.PI * 2, m = (a + b) / 2;
          const quad = [[r0 * Math.cos(a), r0 * Math.sin(a), z0], [r0 * Math.cos(b), r0 * Math.sin(b), z0], [r1 * Math.cos(b), r1 * Math.sin(b), z1], [r1 * Math.cos(a), r1 * Math.sin(a), z1]].map(map);
          const n = sub3(map([Math.cos(m), Math.sin(m), 0]), map([0, 0, 0]));
          addPoly(quad, rgb, n);
        }
      }
    };
    if (pose.separated && full) {
      revolve(B, STATIONS.switchBottom, STATIONS.tip);
      revolve(booster, STATIONS.tail, STATIONS.switchBottom);
      for (const f of finSurfaces(R)) addPoly(f.map(booster), [205, 205, 198], sub3(booster(cross3(sub3(f[1], f[0]), sub3(f[3], f[0]))), booster([0, 0, 0])), "#8c8c86");
    } else {
      revolve(B, lo, hi);
      if (full) for (const f of finSurfaces(R)) addPoly(f.map(B), [205, 205, 198], null, "#8c8c86");
    }
    for (const box of cameraBoxes(s)) for (const face of boxFaces(box)) addPoly(face.map(B), "#253331", null, "#182321");
    if (full) for (const face of canopyFaces(pose)) addPoly(face.points, face.color, null, "rgba(0,0,0,.25)");
    if (s.outline) {
      const brakeMap = pose.separated && full ? booster : B;
      for (let j = 0; j < 3; j++) {
        const a = (j * Math.PI * 2) / 3, hinge = ((-108.879142 * Math.PI) / 180) * s.deploy;
        const outline = s.outline.map(([px, py]) => {
          const xx = px - 0.0575, X = Math.cos(hinge) * xx - Math.sin(hinge) * py + 0.0575, Y = Math.sin(hinge) * xx + Math.cos(hinge) * py;
          return brakeMap([Math.cos(a) * X - Math.sin(a) * Y, Math.sin(a) * X + Math.cos(a) * Y, -s.height / 1000]);
        });
        addPoly(outline, "#b8bdbd", null, "#6f7775");
      }
    }
    polys.sort((a, b) => a.depth - b.depth);
    for (const p of polys) {
      x.beginPath();
      p.q.forEach((q, i) => (i ? x.lineTo(q[0], q[1]) : x.moveTo(q[0], q[1])));
      x.closePath(); x.fillStyle = p.fill; x.fill();
      // Hairline in the fill colour hides seams between adjacent quads.
      x.strokeStyle = p.stroke === "transparent" ? p.fill : p.stroke; x.lineWidth = p.stroke === "transparent" ? 0.6 : 0.7; x.stroke();
    }
    if (full && pose.separated) for (const [a, b] of pose.lines) line([a, b], "#9b8a62", 1);
    if (full && !pose.separated && s.altitude < 8) {
      // Launch rail, world-fixed beside the airframe on the pad.
      const g = -s.altitude, rr = R + 0.029, ra = LAUNCHER.railAzimuth, rx = rr * Math.cos(ra) - (s.east || 0), ry = rr * Math.sin(ra) - (s.north || 0);
      line([[rx, ry, g + 0.45], [rx, ry, g + 5.95]], "#6d716e", 2.2);
    }
    // Station annotations along the full airframe.
    x.font = "10px ui-monospace,monospace";
    if (full) {
      const tag = (z, text, part = B) => {
        const p = project(part([R * 1.9, 0, z])), q = project(part([R * 1.05, 0, z]));
        x.strokeStyle = "#9aa39c"; x.lineWidth = 0.7; x.beginPath(); x.moveTo(q[0], q[1]); x.lineTo(p[0], p[1]); x.stroke();
        x.fillStyle = "#5d6b62"; x.fillText(text, p[0] + 4, p[1] + 3);
      };
      tag(0, "CAMERA RING");
      tag(-s.height / 1000, `AIRBRAKES −${s.height} mm`, pose.separated ? booster : B);
      if (!pose.separated) tag(STATIONS.mainBottom - 0.2, "FINS");
    }
    // The sensor crop, not just the circular lens field, limits each meridian.
    const optics = PRESETS[s.preset || "900"],
      halfField = optics.field * Math.PI / 360,
      circleRadius = optics.circle / optics.pitch / 2;
    const edgeTheta = (azimuth) => {
      const radiusPx = Math.min(
        circleRadius,
        optics.w / 2 / Math.max(1e-12, Math.abs(Math.cos(azimuth))),
        optics.h / 2 / Math.max(1e-12, Math.abs(Math.sin(azimuth))),
      );
      const angle = optics.b
        ? Math.asin(Math.min(1, optics.b * radiusPx * optics.pitch / optics.efl)) / optics.b
        : radiusPx / circleRadius * halfField;
      return Math.min(halfField, angle);
    };
    const rho = R + s.stand / 1000, colors = ["#3f8f78", "#967bab"];
    for (const [j, sign] of [[0, 1], [1, -1]]) {
      const color = colors[j], origin = B([sign * rho, 0, 0]), q = project(origin), length = full ? 0.3 : 0.15;
      line([origin, B([sign * (rho + length), 0, 0])], color, 2);
      x.fillStyle = color; x.beginPath(); x.arc(q[0], q[1], full ? 3 : 7, 0, Math.PI * 2); x.fill();
      x.strokeStyle = "#fff"; x.lineWidth = 2; x.stroke();
      x.font = "600 12px system-ui"; x.fillStyle = color; x.fillText(j === 0 ? "A" : "B", q[0] + 10, q[1] - 10);
      if (this.bubbles && !full) {
        const r = 0.3;
        for (let ring = 1; ring <= 4; ring++) {
          const pts = [];
          for (let k = 0; k <= 96; k++) {
            const a = (k / 96) * Math.PI * 2, th = edgeTheta(a) * ring / 4;
            pts.push([sign * (rho + r * Math.cos(th)), r * Math.sin(th) * Math.cos(a), r * Math.sin(th) * Math.sin(a)]);
          }
          line(pts, color + (ring === 4 ? "a0" : "35"), ring === 4 ? 1.4 : 0.8);
        }
        for (let k = 0; k < 8; k++) {
          const a = (k / 8) * Math.PI * 2, pts = [];
          for (let i = 0; i <= 30; i++) {
            const th = (i / 30) * edgeTheta(a);
            pts.push([sign * (rho + r * Math.cos(th)), r * Math.sin(th) * Math.cos(a), r * Math.sin(th) * Math.sin(a)]);
          }
          line(pts, color + "38", 0.8);
        }
      }
    }
    // This shared direction corresponds to the center of the ground viewport.
    const ya = s.bodyYaw, pi = s.bodyPitch,
      d = [Math.cos(pi) * Math.cos(ya), Math.cos(pi) * Math.sin(ya), Math.sin(pi)];
    for (const [j, sign] of [[0, 1], [1, -1]])
      if (Math.acos(Math.max(-1, Math.min(1, sign * d[0]))) <= edgeTheta(Math.atan2(d[2], d[1]))) {
        const o = [sign * rho, 0, 0], p = o.map((v, i) => v + d[i] * (full ? 0.5 : 0.4));
        line([B(o), B(p)], colors[j], 2, [4, 4]);
      }
    x.fillStyle = "#68736b";
    x.font = "11px ui-monospace,monospace";
    if (!full && this.bubbles) x.fillText("CROPPED LENS FIELDS", 18, h - 36);
    x.fillText(
      full ? (pose.separated ? "DROGUE DESCENT · GRAVITY DOWN" : "AIRFRAME · OPENROCKET PROFILE") : `CAMERA RING · ${s.diameter.toFixed(1)} mm Ø`,
      18,
      h - 20,
    );
    void view;
  }
}
const add3 = (a, b) => a.map((v, i) => v + b[i]);
const sub3 = (a, b) => a.map((v, i) => v - b[i]);
const mul3 = (a, k) => a.map((v) => v * k);
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const norm3 = (a) => mul3(a, 1 / Math.hypot(...a));
const cross3 = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
