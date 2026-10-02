import { Navigation, DEFAULT_PRESETS, validPresets, validPose, wrapYaw } from "./navigation.js";
import { MissionTimeline } from "./mission.js";
import { MissionHUD } from "./hud.js";
import { AttitudeTimeline, imageFromReference, referenceFromImage, transformPose } from "./attitude.js";

const $ = id => document.getElementById(id);
const PRESET_KEY = "pigeonvision.viewPresets.v1";
export class Station {
  constructor(renderer, { send, changed, display, applyDisplay, frame }) {
    this.renderer = renderer;
    this.send = send;
    this.changed = changed;
    this.display = display;
    this.applyDisplay = applyDisplay;
    this.frame = frame;
    this.audience = new URLSearchParams(location.search).get("mode") === "audience";
    document.body.dataset.audience = String(this.audience);
    this.controlling = false;
    this.mission = new MissionTimeline();
    this.hud = new MissionHUD();
    this.pendingReference = null;
    this.lastUpdate = {};
    this.lastMission = {};
    this.missionDirty = true;
    this.lastSent = -Infinity;
    this.sendTimer = null;
    this.navigation = new Navigation(renderer, () => this.viewChanged());
    this.attitudes = new AttitudeTimeline();
    this.horizonRequested = false;
    this.horizonFallback = null;
    this.viewFrame = { pts: null, now: 0, replay: false, frameAgeMs: null, paired: true };
    this.horizonStatus = { requested: false, active: false, available: false, pts: null };
    this.presets = DEFAULT_PRESETS.map(p => p && { ...p });
    try {
      const saved = JSON.parse(localStorage.getItem(PRESET_KEY));
      if (validPresets(saved)) this.presets = saved;
    } catch { /* Page defaults remain usable without browser storage. */ }
    this.drawPresets();
    if ($("horizon-lock")) $("horizon-lock").onchange = () => {
      this.setHorizon($("horizon-lock").checked);
      this.updateHorizonControls();
    };
    $("controls-toggle").onclick = () => {
      const opened = document.body.classList.toggle("controls-open");
      $("controls-toggle").setAttribute("aria-expanded", String(opened));
    };
    $("audience-open").onclick = () => window.open("/?mode=audience", "pigeonvision-audience");
    $("take-control").onclick = () => this.send({ type: "claim_control" });
    $("fullscreen").onclick = () => {
      const action = document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen();
      action.catch(() => { $("pilot-status").textContent = "Fullscreen is unavailable in this browser."; });
    };
    $("pad-reference").onclick = () => {
      if (!this.controlling || !this.mission.sessionId) return;
      const clockNow = performance.now();
      const current = this.frame ? this.frame() : { ...this.viewFrame, now: clockNow,
        frameAgeMs: Number.isFinite(this.viewFrame.frameAgeMs) ?
          Math.max(0, this.viewFrame.frameAgeMs + clockNow - this.viewFrame.now) : null };
      const { pts, now, replay } = current;
      if (this.mission.reference(pts, now, replay)) {
        this.pendingReference = this.mission.pad;
        this.missionDirty = true;
        this.send({ type: "reference", reference: this.mission.pad });
      }
      // The diagnostics interval can lag a new pair or replay reset. Refresh
      // both the reference result and button eligibility at the clicked frame.
      this.update({ ...this.lastUpdate, ...current });
    };
    window.addEventListener("keydown", e => this.keyDown(e));
    window.addEventListener("keyup", e => {
      // Release keys even when focus moved to a control while held.
      if (this.navigation.keys.has(e.code)) this.navigation.key(e.code, false);
    });
    window.addEventListener("blur", () => this.navigation.stop());
    document.addEventListener("visibilitychange", () => { if (document.hidden) this.navigation.stop(); });
  }
  keyDown(e) {
    const editing = e.target.closest?.("input, select, textarea, button, a, summary, [contenteditable=true]");
    if (editing || e.ctrlKey || e.metaKey || e.altKey || !this.controlling || this.audience) return;
    const digit = /^(?:Digit|Numpad)([1-9])$/.exec(e.code);
    if (digit) {
      e.preventDefault();
      if (e.repeat) return;
      e.shiftKey ? this.savePreset(Number(digit[1]) - 1) : this.goPreset(Number(digit[1]) - 1);
    } else if (this.renderer.mode === "perspective" && this.navigation.key(e.code, true)) e.preventDefault();
  }
  drawPresets() {
    const panel = $("view-presets");
    const buttons = this.presets.map((p, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.disabled = !this.controlling;
      button.dataset.preset = index;
      button.textContent = `${index + 1} · ${p?.name || "Save view"}`;
      button.title = `View ${index + 1}. Shift-click or Shift+${index + 1} saves the current angle, roll and zoom.`;
      button.onclick = e => {
        if (!this.controlling) return;
        e.shiftKey || !p ? this.savePreset(index) : this.goPreset(index);
        $("image").focus({ preventScroll: true });
      };
      return button;
    });
    panel.replaceChildren(...buttons.slice(0, 6));
    $("extra-presets")?.replaceChildren(...buttons.slice(6));
  }
  savePreset(index) {
    if (this.renderer.mode !== "perspective" || !this.renderer.calibration) return;
    this.presets[index] = { ...this.navigation.pose(), horizon: this.horizonRequested, name: `Saved ${index + 1}` };
    try { localStorage.setItem(PRESET_KEY, JSON.stringify(this.presets)); } catch { /* Keep in-page presets. */ }
    this.drawPresets();
    $("pilot-status").textContent = `Saved view ${index + 1}`;
  }
  goPreset(index) {
    if (!this.presets[index] || !this.renderer.calibration) return;
    this.renderer.mode = $("view").value = "perspective";
    const horizon = this.presets[index].horizon ?? false;
    if (horizon !== this.horizonRequested && !this.setHorizon(horizon)) return;
    this.navigation.go(this.presets[index], matchMedia("(prefers-reduced-motion: reduce)").matches);
  }
  owner(canControl) {
    if (canControl && !this.audience && this.navigation.followTarget) {
      // A control handoff starts at the last authoritative remote pose, not
      // halfway through the follower's smoothing interval.
      Object.assign(this.renderer, this.navigation.followTarget);
      this.renderer.draw();
      this.changed();
    }
    if (this.controlling !== (canControl && !this.audience)) this.navigation.stop();
    this.controlling = canControl && !this.audience;
    if (!this.controlling) this.navigation.stop();
    document.body.dataset.controlling = String(this.controlling);
    $("pilot-status").textContent = this.controlling ? "You have view control" : "Following operator";
    $("take-control").hidden = this.controlling || this.audience;
    for (const button of document.querySelectorAll("[data-action]")) button.disabled = !this.controlling;
    for (const button of document.querySelectorAll("[data-preset]")) button.disabled = !this.controlling;
    this.updateHorizonControls();
  }
  receiveView(view, initial = false) {
    if (this.controlling && !initial || !validPose(view)) return;
    if (!["a", "b"].includes(view.mode) && !this.renderer.calibration) return;
    if (view.display) this.applyDisplay(view.display);
    const horizon = view.horizon ?? false;
    const coordinateChanged = this.horizonRequested !== horizon;
    this.horizonRequested = horizon;
    const smooth = !initial && !coordinateChanged && view.mode === "perspective" && this.renderer.mode === view.mode &&
      !matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (smooth) this.navigation.follow(view);
    else {
      this.navigation.stop();
      Object.assign(this.renderer, { yaw: view.yaw, pitch: view.pitch, roll: view.roll ?? 0, fov: view.fov });
    }
    this.renderer.mode = view.mode;
    this.updateViewFrame(this.frame ? this.frame() : this.viewFrame);
    $("view").value = view.mode;
    this.changed();
    this.renderer.draw();
    this.update(this.lastUpdate, true);
  }
  viewChanged() {
    this.updateViewFrame(this.frame ? this.frame() : this.viewFrame);
    this.changed();
    this.renderer.draw();
    this.update(this.lastUpdate, true);
    if (!this.controlling) return;
    // Bound network view updates to 20 Hz, with a final trailing pose.
    const remaining = 50 - (performance.now() - this.lastSent);
    if (remaining > 0) {
      if (this.sendTimer === null) this.sendTimer = setTimeout(() => { this.sendTimer = null; this.publishView(); }, remaining);
    } else this.publishView();
  }
  publishView() {
    if (!this.controlling) return;
    this.lastSent = performance.now();
    this.send({ type: "view", view: { ...this.navigation.pose(), mode: this.renderer.mode,
      horizon: this.horizonRequested, display: this.display() } });
  }
  reference(value) {
    this.pendingReference = value;
    this.mission.setReference(value);
    this.missionDirty = true;
  }
  metadata(record, now) {
    this.mission.accept(record, now);
    const previousSession = this.attitudes.sessionId;
    this.attitudes.accept(record, now);
    if (previousSession !== null && previousSession !== this.attitudes.sessionId) {
      this.horizonFallback = null;
      this.renderer.viewTransform = null;
    }
    this.updateViewFrame({ ...this.viewFrame, now,
      frameAgeMs: Number.isFinite(this.viewFrame.frameAgeMs) ?
        Math.max(0, this.viewFrame.frameAgeMs + now - this.viewFrame.now) : null });
    if (this.pendingReference) this.mission.setReference(this.pendingReference);
    this.missionDirty = true;
  }
  resetMission() {
    this.mission.reset(); this.missionDirty = true;
    this.attitudes.reset();
    this.updateViewFrame({ ...this.viewFrame, pts: null });
  }

  setHorizon(requested) {
    if (!this.controlling || typeof requested !== "boolean") return false;
    this.updateViewFrame(this.frame ? this.frame() : this.viewFrame);
    const { pts, now, replay, frameAgeMs } = this.viewFrame;
    const sample = this.viewFrame.paired === false ? null : this.attitudes.at(pts, now, replay, frameAgeMs);
    if (requested && (!sample || this.renderer.mode !== "perspective")) return false;
    this.navigation.stop();
    let pose = this.navigation.pose();
    if (requested && !this.horizonRequested) pose = transformPose(pose, referenceFromImage(sample.matrix));
    else if (!requested && this.horizonRequested && this.renderer.viewTransform)
      pose = transformPose(pose, this.renderer.viewTransform);
    // Entering this mode levels around the current world-facing direction.
    if (requested) pose.roll = 0;
    Object.assign(this.renderer, pose);
    this.horizonRequested = requested;
    this.updateViewFrame(this.viewFrame);
    this.viewChanged();
    return true;
  }

  updateViewFrame(value) {
    this.viewFrame = { ...this.viewFrame, ...value };
    const { pts, now, replay, frameAgeMs } = this.viewFrame;
    const sample = this.viewFrame.paired === false ? null : this.attitudes.at(pts, now, replay, frameAgeMs);
    const active = this.horizonRequested && !!sample && this.renderer.mode === "perspective";
    if (active) this.horizonFallback = imageFromReference(sample.matrix);
    // Freezing this transform follows the image/body when the DEMO sample is
    // missing. It never changes to counter-rotate an unmeasured attitude.
    const transform = this.horizonRequested ? this.horizonFallback : null;
    const previous = this.renderer.viewTransform;
    const changed = (previous === null) !== (transform === null) ||
      !!previous && !!transform && previous.some((item, index) => item !== transform[index]);
    this.renderer.viewTransform = transform;
    this.horizonStatus = { requested: this.horizonRequested, active, available: !!sample, pts: sample?.pts ?? null };
    this.updateHorizonControls();
    if (changed) this.renderer.draw();
  }

  updateHorizonControls() {
    const checkbox = $("horizon-lock"), status = $("horizon-status");
    if (checkbox) {
      checkbox.checked = this.horizonRequested;
      checkbox.disabled = !this.controlling || this.renderer.mode !== "perspective" ||
        !this.horizonStatus.available && !this.horizonRequested;
    }
    if (status) status.textContent = this.horizonStatus.active ? "Scene reference · demo" :
      this.horizonRequested ? "Attitude missing · body view" :
      this.horizonStatus.available ? "Demo attitude ready" : "Needs timed attitude";
  }
  update(value, poseOnly = false) {
    this.lastUpdate = value;
    if (!poseOnly) this.updateViewFrame(value);
    if (!poseOnly || this.missionDirty) {
      this.lastMission = this.mission.sample(value.pts, value.now ?? performance.now(), value.replay);
      this.missionDirty = false;
    }
    const mission = this.lastMission;
    const r = this.renderer;
    this.hud.update({ ...value, mission, mode: r.mode, yaw: wrapYaw(r.yaw), pitch: r.pitch, roll: wrapYaw(r.roll),
      fov: r.fov, controlling: this.controlling, horizon: this.horizonStatus });
    $("pad-reference").disabled = !this.controlling || !this.mission.sessionId ||
      !mission.pressureFresh || mission.phase !== "PAD";
    $("pad-status").textContent = this.mission.pad
      ? `Zero set · ${(this.mission.pad.pressure / 100).toFixed(1)} hPa`
      : "Set zero on the pad";
  }
}
