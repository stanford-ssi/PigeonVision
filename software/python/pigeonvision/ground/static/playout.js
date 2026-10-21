// Live PTS are a common camera clock. Absorb short arrival/decode bursts before
// choosing the newest due image on a display refresh; never queue compressed AUs.
export class LivePlayout {
  constructor(presented, { delayMs = 90, limit = 6 } = {}) {
    this.presented = presented;
    this.delayMs = delayMs;
    this.limit = limit;
    this.queues = { A: [], B: [], pair: [] };
    this.clock = null;
    this.latestPTS = null;
    this.request = null;
    this.dropped = 0;
  }
  enqueue(key, item, clocked = true) {
    if (!clocked) {
      try { item.present(); } finally { item.close(); }
      this.presented([key]);
      return;
    }
    if (this.clock && this.clock.at + (item.pts - this.clock.pts) / 1000 - performance.now() > this.delayMs + 200) {
      if (item.pts - this.latestPTS > 200000) {
        this.reset(); // A forward timestamp discontinuity must not freeze the view.
      } else {
        // Catch up to the earlier arrival time and keep queued frames.
        const at = Number.isFinite(item.at) ? item.at : performance.now();
        this.clock.at = Math.min(this.clock.at, at + this.delayMs - (item.pts - this.clock.pts) / 1000);
      }
    }
    this.latestPTS = Math.max(this.latestPTS ?? item.pts, item.pts);
    const queue = this.queues[key];
    queue.push(item);
    queue.sort((a, b) => a.pts - b.pts);
    while (queue.length > this.limit) {
      queue.shift().close();
      this.dropped++;
    }
    if (this.clock === null) {
      const pts = Math.min(...Object.values(this.queues).filter(q => q.length).map(q => q[0].pts));
      // Decoder startup can be much slower than steady output. Its first
      // completion must not add permanent latency or overflow healthy queues.
      const at = Number.isFinite(item.at) ? item.at : performance.now();
      this.clock = { pts, at: at + this.delayMs };
    }
    this.schedule();
  }
  schedule() {
    if (this.request === null && Object.values(this.queues).some(q => q.length))
      this.request = requestAnimationFrame(() => this.tick(performance.now()));
  }
  tick(now) {
    this.request = null;
    const due = this.clock.pts + (now - this.clock.at) * 1000;
    const changed = [];
    for (const [key, queue] of Object.entries(this.queues)) {
      let selected = null;
      while (queue.length && queue[0].pts <= due) {
        if (selected) { selected.close(); this.dropped++; }
        selected = queue.shift();
      }
      if (!selected) continue;
      try { selected.present(); } finally { selected.close(); }
      changed.push(key);
    }
    if (changed.length) this.presented(changed);
    this.schedule();
  }
  reset(camera = null) {
    if (this.request !== null) cancelAnimationFrame(this.request);
    this.request = null;
    for (const key of camera === null ? ["A", "B", "pair"] : [camera, "pair"])
      for (const item of this.queues[key].splice(0)) item.close();
    if (camera === null) {
      this.clock = null;
      this.latestPTS = null;
    }
    // Keep the shared clock for a surviving camera. Repeated failures of its
    // partner must not keep postponing frames which already have a due time.
    this.schedule();
  }
  snapshot() {
    return { delayMs: this.delayMs, dropped: this.dropped,
      pending: Object.fromEntries(Object.entries(this.queues).map(([key, queue]) => [key, queue.length])) };
  }
}
