// Deterministic RAF and real LivePlayout source; no browser or live receiver.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.resolve(__dirname,
  "../python/pigeonvision/ground/static/playout.js"), "utf8");

function fixture() {
  let now = 0, next = 0;
  const callbacks = new Map(), frames = [];
  const context = {
    performance: { now: () => now },
    requestAnimationFrame: callback => {
      const id = ++next;
      callbacks.set(id, { at: (Math.floor(now / (1000 / 60)) + 1) * (1000 / 60), callback });
      return id;
    },
    cancelAnimationFrame: id => callbacks.delete(id),
  };
  vm.runInNewContext(source.replace("export class LivePlayout", "class LivePlayout") +
    "\nglobalThis.LivePlayout = LivePlayout;", context);
  const playout = new context.LivePlayout(() => {});
  const reset = playout.reset.bind(playout), resets = [];
  playout.reset = (camera = null) => { resets.push(camera); reset(camera); };
  return {
    playout, frames, resets,
    enqueue(key, pts, { at = now, clocked = true } = {}) {
      const frame = { key, pts, at, presented: 0, closed: 0,
        present() { this.presented++; }, close() { this.closed++; } };
      frames.push(frame); playout.enqueue(key, frame, clocked); return frame;
    },
    advance(end) {
      while (callbacks.size) {
        const at = Math.min(...[...callbacks.values()].map(value => value.at));
        if (at > end) break;
        now = at;
        for (const [id, value] of [...callbacks]) {
          if (value.at !== at || !callbacks.has(id)) continue;
          callbacks.delete(id); value.callback(at);
        }
      }
      now = end;
    },
  };
}

// A normal 33 ms sequence delivered in catch-up bursts must preserve its clock,
// including when B/pair follow A or B is the first stream to advance the PTS.
for (const leader of ["A", "B"]) {
  const f = fixture();
  f.enqueue(leader, 1000000);
  const clock = f.playout.clock;
  for (let i = 1; i <= 15; i++) {
    const pts = 1000000 + i * 33334;
    f.enqueue(leader, pts);
    f.enqueue(leader === "A" ? "B" : "A", pts);
    f.enqueue("pair", pts);
  }
  assert.equal(f.playout.clock, clock);
  assert.deepEqual(f.resets, []);
  assert.equal(f.playout.latestPTS, 1500010);
  assert.equal(f.playout.snapshot().delayMs, 90);
  assert.ok(Object.values(f.playout.snapshot().pending).every(count => count <= 6));
  f.playout.reset();
  assert.ok(f.frames.every(frame => frame.closed === 1));
}

// Advancing an old arrival anchor must retain the healthy partner's queued
// frame. Suppressing clock catch-up entirely would instead add lasting latency.
{
  const f = fixture();
  const healthy = f.enqueue("B", 1000000);
  for (let i = 0; i <= 7; i++) f.enqueue("A", 1000000 + i * 33333);
  assert.deepEqual(f.resets, []);
  assert.equal(healthy.closed, 0);
  assert.equal(f.playout.clock.pts, 1000000);
  const latestDue = f.playout.clock.at + (1233331 - f.playout.clock.pts) / 1000;
  assert.equal(latestDue, 90);
  f.advance(110);
  assert.equal(healthy.presented, 1);
  assert.ok(f.frames.every(frame => frame.closed === 1));
}

// A true forward discontinuity still discards old queues and becomes due soon.
{
  const f = fixture();
  const old = f.enqueue("A", 1000000);
  f.enqueue("B", 1033333);
  const jumped = f.enqueue("A", 11000000);
  assert.deepEqual(f.resets, [null]);
  assert.equal(old.closed, 1);
  assert.equal(f.playout.clock.pts, 11000000);
  assert.equal(f.playout.latestPTS, 11000000);
  f.advance(110);
  assert.equal(jumped.presented, 1);
  assert.equal(jumped.closed, 1);
}

// A long ordinary pause is not a clock discontinuity when wall time also passes.
{
  const f = fixture();
  f.enqueue("A", 1000000);
  const clock = f.playout.clock;
  f.advance(1000);
  f.enqueue("A", 2000000);
  assert.equal(f.playout.clock, clock);
  assert.deepEqual(f.resets, []);
}

// Resetting one camera preserves the survivor's clock and common PTS progress.
{
  const f = fixture();
  f.enqueue("B", 1000000);
  const healthy = f.enqueue("B", 1033333);
  const clock = f.playout.clock;
  f.enqueue("A", 1000000);
  f.enqueue("pair", 1033333);
  f.playout.reset("A");
  f.enqueue("A", 1033333);
  assert.equal(f.playout.clock, clock);
  assert.equal(f.playout.latestPTS, 1033333);
  f.advance(150);
  assert.equal(healthy.presented, 1);
  assert.ok(f.frames.every(frame => frame.closed === 1));
}

// Full reset/session restart permits backward PTS and ignores cached bootstrap.
{
  const f = fixture();
  f.enqueue("A", 10000000);
  f.playout.reset();
  assert.equal(f.playout.clock, null);
  assert.equal(f.playout.latestPTS, null);
  const cached = f.enqueue("B", 9000000, { clocked: false });
  assert.equal(cached.presented, 1);
  assert.equal(cached.closed, 1);
  assert.equal(f.playout.clock, null);
  assert.equal(f.playout.latestPTS, null);
  const restarted = f.enqueue("A", 0);
  assert.equal(f.playout.clock.pts, 0);
  f.advance(110);
  assert.equal(restarted.presented, 1);
  assert.ok(f.frames.every(frame => frame.closed === 1));
}

// Increasing delay fills the buffer once without replacing its arrival clock.
// Switching back bounds every queue immediately and closes trimmed frames once.
{
  const f = fixture();
  const first = f.enqueue("A", 0);
  f.enqueue("B", 33333);
  const clock = f.playout.clock, floor = clock.at - f.playout.delayMs;
  f.playout.setDelay(220, 16);
  assert.equal(f.playout.clock, clock);
  assert.equal(clock.at - f.playout.delayMs, floor);
  assert.equal(f.playout.snapshot().delayMs, 220);
  assert.equal(f.playout.snapshot().limit, 16);
  assert.equal(first.closed, 0);
  f.advance(150);
  assert.equal(first.presented, 0);
  for (let i = 1; i <= 20; i++) f.enqueue("A", i * 33333);
  assert.equal(f.playout.queues.A.length, 16);
  const before = f.playout.dropped;
  f.playout.setDelay(90, 6);
  assert.equal(f.playout.clock, clock);
  assert.equal(f.playout.snapshot().delayMs, 90);
  assert.equal(f.playout.snapshot().limit, 6);
  assert.equal(f.playout.queues.A.length, 6);
  assert.equal(f.playout.dropped, before + 10);
  assert.deepEqual(f.resets, []);
  f.advance(1000);
  assert.ok(f.frames.every(frame => frame.closed === 1));
}

// Reject bad settings before changing the clock, bounds or frame ownership.
{
  const f = fixture();
  f.enqueue("A", 0);
  const clock = f.playout.clock, at = clock.at;
  for (const [delay, limit] of [[NaN, 16], [Infinity, 16], [-1, 16], [220, 0],
    [220, 1.5], [220, Infinity], ["220", 16]]) {
    assert.throws(() => f.playout.setDelay(delay, limit), { name: "RangeError" });
    assert.equal(f.playout.clock, clock);
    assert.equal(clock.at, at);
    assert.equal(f.playout.delayMs, 90);
    assert.equal(f.playout.limit, 6);
    assert.equal(f.frames[0].closed, 0);
  }
  f.playout.reset();
  assert.equal(f.frames[0].closed, 1);
}

console.log(JSON.stringify({ checked: ["A/B catch-up preserves common clock",
  "clock catch-up retains healthy queued partner without extra latency",
  "bounded default queues", "true forward jump recovers", "wall-time pause",
  "surviving camera", "full backward/session reset", "unclocked bootstrap", "frame closure",
  "buffer switch preserves arrival clock", "shrinking closes bounded queues", "invalid settings are atomic"] }));
