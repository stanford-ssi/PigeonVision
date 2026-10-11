// DEMO image-basis history only. Real flight attitude is not provided here.
import { multiplyBasis, perspectiveBasis } from "./projection.js";

// Column-major reference image basis: +Z forward East, screen up ENU +Z.
// The existing synthetic camera convention is reflected (determinant -1).
export const DEMO_REFERENCE = Object.freeze([0, 1, 0, 0, 0, -1, 1, 0, 0]);
export const transposeBasis = m => [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]];
const dot = (a, b) => a.reduce((sum, value, i) => sum + value * b[i], 0);

export function validDemoBasis(rows) {
  if (!Array.isArray(rows) || rows.length !== 3 || rows.some(row =>
    !Array.isArray(row) || row.length !== 3 || !row.every(Number.isFinite))) return false;
  for (let i = 0; i < 3; i++) for (let j = i; j < 3; j++)
    if (Math.abs(dot(rows[i], rows[j]) - Number(i === j)) > 1e-5) return false;
  const [a, b, c] = rows;
  const determinant = a[0] * (b[1] * c[2] - b[2] * c[1]) -
    a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]);
  return Math.abs(determinant + 1) <= 1e-5;
}

export function poseFromBasis(basis, fov) {
  const yaw = Math.atan2(basis[6], basis[8]);
  const pitch = Math.max(-1.56, Math.min(1.56, Math.asin(Math.max(-1, Math.min(1, -basis[7])))));
  const level = perspectiveBasis(yaw, pitch);
  const right = basis.slice(0, 3);
  const roll = Math.atan2(-dot(right, level.slice(3, 6)), dot(right, level.slice(0, 3)));
  return { yaw, pitch, roll, fov };
}

export const referenceFromImage = matrix => multiplyBasis(transposeBasis(DEMO_REFERENCE), matrix);
export const imageFromReference = matrix => multiplyBasis(transposeBasis(matrix), DEMO_REFERENCE);
export function transformPose(pose, transform) {
  return poseFromBasis(multiplyBasis(transform, perspectiveBasis(pose.yaw, pose.pitch, pose.roll ?? 0)), pose.fov);
}

export class AttitudeTimeline {
  constructor() { this.reset(); }
  reset() { this.sessionId = null; this.samples = []; this.retiredSessions = new Set(); }
  accept(record, now) {
    if (record.type === "session") {
      if (typeof record.session_id !== "string" || !record.session_id || this.retiredSessions.has(record.session_id)) return false;
      if (record.session_id !== this.sessionId) {
        if (this.sessionId) {
          this.retiredSessions.add(this.sessionId);
          if (this.retiredSessions.size > 32) this.retiredSessions.delete(this.retiredSessions.values().next().value);
        }
        this.sessionId = record.session_id;
        this.samples = [];
      }
      return false;
    }
    const attitude = record.simulation_attitude;
    if (record.type !== "frame" || record.simulated !== true || record.backend !== "simulation" ||
        !this.sessionId || record.session_id !== this.sessionId || !Number.isSafeInteger(record.pts_us) || record.pts_us < 0 || !Number.isFinite(now) ||
        attitude?.frame !== "ENU" || attitude?.source !== "scene" || !validDemoBasis(attitude.R_world_from_rig)) return false;
    const rows = attitude.R_world_from_rig;
    const matrix = [rows[0][0], rows[1][0], rows[2][0], rows[0][1], rows[1][1], rows[2][1], rows[0][2], rows[1][2], rows[2][2]];
    const sample = { pts: record.pts_us, matrix, receivedAt: now };
    const index = this.samples.findIndex(item => item.pts >= sample.pts);
    if (index < 0) this.samples.push(sample);
    else if (this.samples[index].pts === sample.pts) this.samples[index] = sample;
    else this.samples.splice(index, 0, sample);
    if (this.samples.length > 512) this.samples.splice(0, this.samples.length - 512);
    return true;
  }
  at(pts, now, replay = false, frameAgeMs = null) {
    if (!Number.isSafeInteger(pts) || pts < 0) return null;
    // Saved DEMO frames can remain paused indefinitely. Live inputs need both
    // a fresh displayed image and a recently received matching scene basis.
    if (!replay && (!Number.isFinite(now) || !Number.isFinite(frameAgeMs) || frameAgeMs < 0 || frameAgeMs > 1000)) return null;
    let lower = 0, upper = this.samples.length;
    while (lower < upper) {
      const middle = (lower + upper) >> 1;
      this.samples[middle].pts < pts ? lower = middle + 1 : upper = middle;
    }
    const sample = this.samples[lower];
    if (!sample || sample.pts !== pts || !replay && now - sample.receivedAt > 1000) return null;
    return sample;
  }
}
