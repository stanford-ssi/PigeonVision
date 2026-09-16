import { PRESETS } from "./engine.js";
import { AIRFRAME, finFaces, cameraBoxes, boxFaces, recoveryPose, noseFaces, canopyFaces, worldToBody } from "./scene.js";
// An engineering observer, never a camera or downlinked view.
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
      this.el = Math.max(
        -1.3,
        Math.min(1.3, this.drag[3] + (e.clientY - this.drag[1]) * 0.009),
      );
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
    const pose = recoveryPose(s),
      recoveryPoints = pose.lines.flat().map(p => worldToBody(p,s)),
      canopyBody = worldToBody(pose.canopy,s),
      recoveryTop = pose.open > .01 ? canopyBody[2] + pose.radius : 2.3 + pose.sep,
      lowest = Math.min(AIRFRAME.bottom, ...recoveryPoints.map(p=>p[2])),
      highest = Math.max(2.3 + pose.sep, recoveryTop, ...recoveryPoints.map(p=>p[2])),
      center = this.full ? (highest + lowest)/2 : -0.04,
      scale = this.full ? Math.min(h / ((highest-lowest)*1.3),w / (pose.open>.01?6:1.5)) : Math.min(w, h) / 1.05,
      ca = Math.cos(this.az),
      sa = Math.sin(this.az),
      ce = Math.cos(this.el),
      se = Math.sin(this.el);
    const project = ([a, b, z]) => {
      const X = -sa * a + ca * b,
        Y = -se * (ca * a + sa * b) + ce * (z - center),
        D = ce * (ca * a + sa * b) + se * (z - center),
        persp = (this.full ? 9 : 2.4) / ((this.full ? 9 : 2.4) - D);
      return [w * 0.5 + X * scale * persp, h * 0.53 - Y * scale * persp, D];
    };
    const line = (points, color, width = 1, dash = []) => {
      x.strokeStyle = color;
      x.lineWidth = width;
      x.setLineDash(dash);
      x.beginPath();
      points.forEach((p, i) => {
        const q = project(p);
        i ? x.lineTo(q[0], q[1]) : x.moveTo(q[0], q[1]);
      });
      x.stroke();
      x.setLineDash([]);
    };
    // A light technical grid gives the two pupils a visible separation.
    for (let i = -5; i <= 5; i++) {
      line(
        [
          [i * 0.1, -0.5, -0.15],
          [i * 0.1, 0.5, -0.15],
        ],
        "#e8ece8",
        0.7,
      );
      line(
        [
          [-0.5, i * 0.1, -0.15],
          [0.5, i * 0.1, -0.15],
        ],
        "#e8ece8",
        0.7,
      );
    }
    const R = s.diameter / 2000,
      rho = R + s.stand / 1000,
      polys = [];
    const add = (pts, color, stroke = "#596960") =>
      polys.push({
        pts: pts.map(project),
        color,
        stroke,
        depth: pts.map(project).reduce((a, p) => a + p[2], 0) / pts.length,
      });
    const bottom = this.full ? AIRFRAME.bottom : -0.56,
      top = this.full ? AIRFRAME.shoulder : 0.56,
      N = 48;
    for (let i = 0; i < N; i++) {
      const a = (i / N) * Math.PI * 2,
        b = ((i + 1) / N) * Math.PI * 2,
        light = Math.round(222 + 18 * Math.cos(a - 0.6));
      add(
        [
          [R * Math.cos(a), R * Math.sin(a), bottom],
          [R * Math.cos(b), R * Math.sin(b), bottom],
          [R * Math.cos(b), R * Math.sin(b), top],
          [R * Math.cos(a), R * Math.sin(a), top],
        ],
        i % 12 < 2 ? "#40584f" : `rgb(${light},${light + 2},${light})`,
        "transparent",
      );
    }
    for (const box of cameraBoxes(s))
      for (const face of boxFaces(box)) add(face, "#384b49", "#253936");
    if (this.full) {
      for (const face of finFaces(R)) add(face, "#3d5a50", "#30483f");
      for (const face of noseFaces(R,pose)) add(face.map(p=>worldToBody(p,s)), "#b6c3bc", "transparent");
      for (const face of canopyFaces(pose)) add(face.points.map(p=>worldToBody(p,s)),face.color,"transparent");
    }
    if (s.outline)
      for (let j = 0; j < 3; j++) {
        const a = (j * Math.PI * 2) / 3,
          hinge = ((-108.879142 * Math.PI) / 180) * s.deploy;
        const pts = s.outline.map(([px, py]) => {
          const xx = px - 0.0575,
            X = Math.cos(hinge) * xx - Math.sin(hinge) * py + 0.0575,
            Y = Math.sin(hinge) * xx + Math.cos(hinge) * py;
          return [
            Math.cos(a) * X - Math.sin(a) * Y,
            Math.sin(a) * X + Math.cos(a) * Y,
            -s.height / 1000,
          ];
        });
        add(pts, "#b4c4ba", "#75867a");
      }
    if (this.full)
      for (const segment of pose.lines) line(segment.map(p=>worldToBody(p,s)),"#b9a577",1.1);
    polys.sort((a, b) => a.depth - b.depth);
    for (const p of polys) {
      x.beginPath();
      p.pts.forEach((q, i) =>
        i ? x.lineTo(q[0], q[1]) : x.moveTo(q[0], q[1]),
      );
      x.closePath();
      x.fillStyle = p.color;
      x.fill();
      if (p.stroke !== "transparent") {
        x.strokeStyle = p.stroke;
        x.lineWidth = 0.7;
        x.stroke();
      }
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
    const colors = ["#3f8f78", "#967bab"];
    for (const [j, sign] of [
      [0, 1],
      [1, -1],
    ]) {
      const color = colors[j],
        origin = [sign * rho, 0, 0],
        q = project(origin),
        length = this.full ? 0.3 : 0.15;
      line([origin, [sign * (rho + length), 0, 0]], color, 2);
      x.fillStyle = color;
      x.beginPath();
      x.arc(q[0], q[1], this.full ? 3 : 7, 0, Math.PI * 2);
      x.fill();
      x.strokeStyle = "#fff";
      x.lineWidth = 2;
      x.stroke();
      x.font = "600 12px system-ui";
      x.fillStyle = color;
      x.fillText(j === 0 ? "A" : "B", q[0] + 10, q[1] - 10);
      if (this.bubbles && !this.full) {
        const r = 0.3;
        for (let ring = 1; ring <= 4; ring++) {
          const pts = [];
          for (let k = 0; k <= 96; k++) {
            const a = (k / 96) * Math.PI * 2,
              th = edgeTheta(a) * ring / 4;
            pts.push([
              sign * (rho + r * Math.cos(th)),
              r * Math.sin(th) * Math.cos(a),
              r * Math.sin(th) * Math.sin(a),
            ]);
          }
          line(pts, color + (ring === 4 ? "a0" : "35"), ring === 4 ? 1.4 : 0.8);
        }
        for (let k = 0; k < 8; k++) {
          const a = (k / 8) * Math.PI * 2,
            pts = [];
          for (let i = 0; i <= 30; i++) {
            const th = (i / 30) * edgeTheta(a);
            pts.push([
              sign * (rho + r * Math.cos(th)),
              r * Math.sin(th) * Math.cos(a),
              r * Math.sin(th) * Math.sin(a),
            ]);
          }
          line(pts, color + "38", 0.8);
        }
      }
    }
    // This shared direction corresponds to the center of the ground viewport.
    const ya = s.bodyYaw,
      pi = s.bodyPitch,
      d = [
        Math.cos(pi) * Math.cos(ya),
        Math.cos(pi) * Math.sin(ya),
        Math.sin(pi),
      ];
    for (const [j, sign] of [
      [0, 1],
      [1, -1],
    ])
      if (Math.acos(Math.max(-1, Math.min(1, sign * d[0]))) <= edgeTheta(Math.atan2(d[2], d[1]))) {
        const o = [sign * rho, 0, 0],
          p = o.map((v, i) => v + d[i] * (this.full ? 0.5 : 0.4));
        line([o, p], colors[j], 2, [4, 4]);
      }
    x.fillStyle = "#68736b";
    x.font = "11px ui-monospace,monospace";
    if (!this.full && this.bubbles)
      x.fillText("SENSOR-CROPPED COVERAGE", 18, h - 36);
    x.fillText(
      this.full ? "ILLUSTRATIVE AIRFRAME + RECOVERY" : `CAMERA RING · ${s.diameter.toFixed(1)} mm Ø`,
      18,
      h - 20,
    );
  }
}
