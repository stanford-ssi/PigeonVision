// Ground-only camera controls. Angles are relative to the calibrated rig.
const TAU = 2 * Math.PI;
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
export const wrapYaw = x => ((x + Math.PI) % TAU + TAU) % TAU - Math.PI;
export const DEFAULT_PRESETS = [
  { name: "Camera A", yaw: 0, pitch: 0, roll: 0, horizon: false, fov: 1.92 },
  { name: "Camera B", yaw: Math.PI, pitch: 0, roll: 0, horizon: false, fov: 1.92 },
  { name: "Right", yaw: Math.PI / 2, pitch: 0, roll: 0, horizon: false, fov: 1.92 },
  { name: "Left", yaw: -Math.PI / 2, pitch: 0, roll: 0, horizon: false, fov: 1.92 },
  { name: "Up", yaw: 0, pitch: 1.4, roll: 0, horizon: false, fov: 1.92 },
  { name: "Down", yaw: 0, pitch: -1.4, roll: 0, horizon: false, fov: 1.92 },
  null, null, null,
];
export function validPose(p) {
  return p && [p.yaw, p.pitch, p.fov].every(Number.isFinite) &&
    Math.abs(p.yaw) <= Math.PI && Math.abs(p.pitch) <= 1.56 && p.fov >= .25 && p.fov <= 2.6 &&
    (p.roll === undefined || Number.isFinite(p.roll) && Math.abs(p.roll) <= Math.PI) &&
    (p.horizon === undefined || typeof p.horizon === "boolean");
}
export function validPresets(value) {
  return Array.isArray(value) && value.length === 9 && value.every(p => p === null ||
    (validPose(p) && typeof p.name === "string" && p.name.length <= 32));
}
const DIRECTIONS = {
  ArrowLeft: [-1, 0, 0], KeyA: [-1, 0, 0], ArrowRight: [1, 0, 0], KeyD: [1, 0, 0],
  ArrowUp: [0, 1, 0], KeyW: [0, 1, 0], ArrowDown: [0, -1, 0], KeyS: [0, -1, 0],
  KeyQ: [0, 0, -1], KeyE: [0, 0, 1],
};
export class Navigation {
  constructor(view, changed) {
    this.view = view;
    if (this.view.roll === undefined) this.view.roll = 0;
    this.changed = changed;
    this.keys = new Set();
    this.velocity = [0, 0, 0];
    this.transition = null;
    this.followTarget = null;
    this.frame = null;
    this.previous = null;
  }
  key(code, down) {
    if (!(code in DIRECTIONS) && code !== "ShiftLeft" && code !== "ShiftRight") return false;
    down ? this.keys.add(code) : this.keys.delete(code);
    if (down && code in DIRECTIONS) this.transition = null;
    this.schedule();
    return true;
  }
  stop() {
    this.keys.clear();
    this.velocity = [0, 0, 0];
    this.transition = null;
    this.followTarget = null;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = this.previous = null;
  }
  go(pose, instant = false) {
    this.stop();
    if (!validPose(pose)) return;
    this.transition = { start: null, from: this.pose(), to: { ...pose, roll: pose.roll ?? 0 }, instant };
    this.schedule();
  }
  follow(pose) {
    if (!validPose(pose)) return;
    this.followTarget = { yaw: pose.yaw, pitch: pose.pitch, roll: pose.roll ?? 0, fov: pose.fov };
    this.schedule();
  }
  pose() { return { yaw: wrapYaw(this.view.yaw), pitch: this.view.pitch, roll: wrapYaw(this.view.roll), fov: this.view.fov }; }
  schedule() {
    if (this.frame === null) this.frame = requestAnimationFrame(now => this.tick(now));
  }
  tick(now) {
    this.frame = null;
    const dt = this.previous === null ? 0 : Math.min(.05, (now - this.previous) / 1000);
    this.previous = now;
    if (this.followTarget) {
      // Remote poses arrive at 20 Hz. Converge on display frames without
      // predicting beyond the operator's last received angle.
      const target = this.followTarget;
      const delta = [wrapYaw(target.yaw - this.view.yaw), target.pitch - this.view.pitch,
        target.fov - this.view.fov, wrapYaw(target.roll - this.view.roll)];
      const settled = delta.every(v => Math.abs(v) < .00001);
      const blend = settled ? 1 : 1 - Math.exp(-dt / .035);
      this.view.yaw = wrapYaw(this.view.yaw + delta[0] * blend);
      this.view.pitch += delta[1] * blend;
      this.view.fov += delta[2] * blend;
      this.view.roll = wrapYaw(this.view.roll + delta[3] * blend);
      if (settled) this.followTarget = null;
    } else if (this.transition) {
      const t = this.transition;
      t.start ??= now;
      const fraction = t.instant ? 1 : clamp((now - t.start) / 550, 0, 1);
      const ease = fraction * fraction * (3 - 2 * fraction);
      this.view.yaw = wrapYaw(t.from.yaw + wrapYaw(t.to.yaw - t.from.yaw) * ease);
      this.view.pitch = t.from.pitch + (t.to.pitch - t.from.pitch) * ease;
      this.view.fov = t.from.fov + (t.to.fov - t.from.fov) * ease;
      this.view.roll = wrapYaw(t.from.roll + wrapYaw(t.to.roll - t.from.roll) * ease);
      if (fraction === 1) this.transition = null;
    } else {
      const direction = [0, 0, 0];
      for (const key of this.keys) if (DIRECTIONS[key])
        DIRECTIONS[key].forEach((v, i) => direction[i] += v);
      const norm = Math.max(1, Math.hypot(...direction));
      const speed = this.keys.has("ShiftLeft") || this.keys.has("ShiftRight") ? .28 : .9;
      for (let i = 0; i < 3; i++) {
        const target = direction[i] / norm * speed;
        this.velocity[i] += (target - this.velocity[i]) * (1 - Math.exp(-dt / .1));
        if (Math.abs(this.velocity[i]) < .0001) this.velocity[i] = 0;
      }
      this.view.yaw = wrapYaw(this.view.yaw + this.velocity[0] * dt);
      this.view.pitch = clamp(this.view.pitch + this.velocity[1] * dt, -1.56, 1.56);
      this.view.roll = wrapYaw(this.view.roll + this.velocity[2] * dt);
    }
    this.changed();
    if (this.followTarget || this.transition || this.keys.size || this.velocity.some(v => v !== 0)) this.schedule();
    else this.previous = null;
  }
}
