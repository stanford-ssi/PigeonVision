// An invented Basin-and-Range desert, in metres, with the pad at the origin.
// It is illustrative relief grown by a simple erosion model, not a survey of
// any launch site. Both camera eyes and the observer use the same maps.
// x = east, y = north, z = up. Pad ground is z = 0.
export const TERRAIN = {
  fine: { size: 1024, span: 32000, detail: 1 },
  far: { size: 1024, span: 204800, detail: 5 },
  noiseSize: 256,
  elevation: 1250, // assumed basin elevation above sea level (air density only)
  sun: { azimuthDeg: 128, elevationDeg: 36 }, // mid-morning, from the south-east
};
const SUN = (() => {
  const a = TERRAIN.sun.azimuthDeg * Math.PI / 180, e = TERRAIN.sun.elevationDeg * Math.PI / 180;
  return [Math.sin(a) * Math.cos(e), Math.cos(a) * Math.cos(e), Math.sin(e)];
})();
export const SUN_DIRECTION = SUN;

// ---------------------------------------------------------------- noise
const GX = new Float32Array(16), GY = new Float32Array(16);
for (let i = 0; i < 16; i++) { GX[i] = Math.cos(i * Math.PI / 8); GY[i] = Math.sin(i * Math.PI / 8); }
function hash(i, j, seed) {
  let n = Math.imul(i, 0x27d4eb2d) ^ Math.imul(j + 0x3c6ef372, 0x165667b1) ^ Math.imul(seed + 1, 0x9e3779b9);
  n = Math.imul(n ^ (n >>> 15), 0x85ebca6b);
  n = Math.imul(n ^ (n >>> 13), 0xc2b2ae35);
  return (n ^ (n >>> 16)) >>> 0;
}
// Gradient noise, roughly in [-1, 1].
function noise(x, y, seed = 0) {
  const i = Math.floor(x), j = Math.floor(y), fx = x - i, fy = y - j;
  const u = fx * fx * fx * (fx * (fx * 6 - 15) + 10), v = fy * fy * fy * (fy * (fy * 6 - 15) + 10);
  let h = hash(i, j, seed) & 15; const a = GX[h] * fx + GY[h] * fy;
  h = hash(i + 1, j, seed) & 15; const b = GX[h] * (fx - 1) + GY[h] * fy;
  h = hash(i, j + 1, seed) & 15; const c = GX[h] * fx + GY[h] * (fy - 1);
  h = hash(i + 1, j + 1, seed) & 15; const d = GX[h] * (fx - 1) + GY[h] * (fy - 1);
  return 1.45 * (a + (b - a) * u + (c - a + (a - b - c + d) * u) * v);
}
function fbm(x, y, octaves, seed = 0) {
  let sum = 0, amp = 0.5, norm = 0;
  for (let k = 0; k < octaves; k++) {
    sum += amp * noise(x, y, seed + k); norm += amp;
    const nx = 1.62 * x - 1.18 * y, ny = 1.18 * x + 1.62 * y; x = nx + 5.3; y = ny - 2.9; amp *= 0.5;
  }
  return sum / norm;
}
// Sharp-crested ridged noise in [0, 1]; later octaves are damped in valleys.
function ridged(x, y, octaves, seed = 0) {
  let sum = 0, amp = 0.5, norm = 0, weight = 1;
  for (let k = 0; k < octaves; k++) {
    let n = 1 - Math.abs(noise(x, y, seed + k)); n *= n;
    sum += amp * n * weight; norm += amp; weight = Math.min(1, n * 1.6);
    const nx = 1.62 * x - 1.18 * y, ny = 1.18 * x + 1.62 * y; x = nx + 5.3; y = ny - 2.9; amp *= 0.5;
  }
  return sum / norm;
}
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const mix = (a, b, t) => a + (b - a) * t;
const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };

// ---------------------------------------------------------------- landform
// Tilted fault-block ranges trending NNE, separated by alluvial basins.
// b: across-strike offset of the crest (m), h: crest height, face: side of
// the steep fault scarp (-1 = scarp faces west), a0/a1: along-strike extent.
const STRIKE = 14 * Math.PI / 180, CS = Math.cos(STRIKE), SS = Math.sin(STRIKE);
const RANGES = [
  { b: 7600, h: 1650, face: -1, steep: 2300, back: 5200, a0: -34000, a1: 30000, seed: 11 },
  { b: -11200, h: 1050, face: 1, steep: 2000, back: 4200, a0: -20000, a1: 26000, seed: 12 },
  { b: 29000, h: 1900, face: -1, steep: 3200, back: 7000, a0: -60000, a1: 45000, seed: 13 },
  { b: -33000, h: 1500, face: 1, steep: 3000, back: 6500, a0: -30000, a1: 70000, seed: 14 },
  { b: 54000, h: 1300, face: 1, steep: 3500, back: 6000, a0: -20000, a1: 80000, seed: 15 },
  { b: -60000, h: 1250, face: -1, steep: 3500, back: 6500, a0: -80000, a1: 20000, seed: 16 },
  { b: 80000, h: 1100, face: -1, steep: 4000, back: 7000, a0: -90000, a1: 30000, seed: 17 },
  { b: -86000, h: 1350, face: 1, steep: 4000, back: 7000, a0: -40000, a1: 90000, seed: 18 },
  { b: 12000, h: 700, face: 1, steep: 1800, back: 3000, a0: -70000, a1: -38000, seed: 19 },
];
// Normalised uplift (0..1 of final crest height) and the target crest height.
function uplift(x, y) {
  const a = x * SS + y * CS, b0 = x * CS - y * SS;
  const wb = b0 + 2600 * noise(a / 21000, b0 / 21000, 3) + 900 * noise(a / 6000, b0 / 6000, 4);
  let best = 0, height = 0;
  for (const r of RANGES) {
    const q = wb - r.b, side = Math.sign(q) === r.face ? r.steep : r.back;
    const across = q / side;
    if (Math.abs(across) > 3) continue;
    const along = smooth(r.a0 - 9000, r.a0 + 7000, a) * smooth(r.a1 + 9000, r.a1 - 7000, a);
    const crest = r.h * (0.62 + 0.38 * fbm(a / 16000, r.seed, 3, r.seed)) * along;
    const profile = Math.exp(-across * across * 1.25) * crest;
    if (profile > height) { height = profile; best = profile; }
  }
  // Low isolated hills break up long basin floors.
  const hills = Math.max(0, ridged(x / 5200, y / 5200, 3, 30) - 0.58) * 900 * smooth(20000, 40000, Math.hypot(x, y));
  return Math.max(best, hills);
}

// Graded basin floors: each basin drains along strike toward a low axis.
const PLAYA = { x: -6400, y: 4600 };
function basinFloor(x, y) {
  const a = x * SS + y * CS, b = x * CS - y * SS;
  const pa = PLAYA.x * SS + PLAYA.y * CS, pb = PLAYA.x * CS - PLAYA.y * SS;
  const axis = pb + 1800 * noise(a / 26000, 5.5, 8);
  const across = Math.abs(b - axis), along = Math.abs(a - pa);
  return 0.011 * Math.sqrt(across * across + 4e6) + 0.0035 * Math.sqrt(along * along + 9e6) - 40
    + 14 * fbm(x / 4200, y / 4200, 4, 9);
}

// ---------------------------------------------------------------- erosion
// Implicit stream-power incision (Braun & Willett 2013) with uplift and
// hillslope diffusion on a coarse grid. Outlets are the grid boundary.
function floodOrder(h, n, order, rcv) {
  // Priority flood: fills pits so every cell drains to the boundary.
  const heap = new Int32Array(n * n), key = new Float64Array(n * n), seen = new Uint8Array(n * n);
  let size = 0;
  const push = (i, k) => {
    let c = size++; heap[c] = i; key[i] = k;
    while (c > 0) { const p = (c - 1) >> 1; if (key[heap[p]] <= k) break; heap[c] = heap[p]; heap[p] = i; c = p; }
  };
  const pop = () => {
    const top = heap[0], last = heap[--size]; let c = 0;
    if (size > 0) {
      heap[0] = last; const k = key[last];
      for (;;) {
        const l = 2 * c + 1; if (l >= size) break;
        const r = l + 1, m = r < size && key[heap[r]] < key[heap[l]] ? r : l;
        if (key[heap[m]] >= k) break; heap[c] = heap[m]; heap[m] = last; c = m;
      }
    }
    return top;
  };
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++)
    if (i === 0 || j === 0 || i === n - 1 || j === n - 1) { const id = j * n + i; seen[id] = 1; rcv[id] = id; push(id, h[id]); }
  let count = 0;
  while (size) {
    const id = pop(), i = id % n, j = (id / n) | 0, k = key[id];
    order[count++] = id; h[id] = k;
    for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) {
      if (!di && !dj) continue;
      const a = i + di, b = j + dj; if (a < 0 || b < 0 || a >= n || b >= n) continue;
      const nb = b * n + a; if (seen[nb]) continue;
      seen[nb] = 1; push(nb, Math.max(h[nb], k + 1e-3));
    }
  }
}
const DI = [-1, 0, 1, -1, 1, -1, 0, 1], DJ = [-1, -1, -1, 0, 0, 1, 1, 1];
const DL = [Math.SQRT2, 1, Math.SQRT2, 1, 1, Math.SQRT2, 1, Math.SQRT2];
function receivers(h, n, rcv, dist) {
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const id = j * n + i;
    if (i === 0 || j === 0 || i === n - 1 || j === n - 1) { rcv[id] = id; dist[id] = 1; continue; }
    let best = 0, r = id, d = 1;
    for (let k = 0; k < 8; k++) {
      const nb = id + DJ[k] * n + DI[k], s = (h[id] - h[nb]) / DL[k];
      if (s > best) { best = s; r = nb; d = DL[k]; }
    }
    rcv[id] = r; dist[id] = d;
  }
}
// Base-level-first stack from the receiver tree, without sorting.
function stack(n, rcv, order) {
  const N = n * n, count = new Int32Array(N + 1), donors = new Int32Array(N);
  for (let i = 0; i < N; i++) if (rcv[i] !== i) count[rcv[i] + 1]++;
  for (let i = 0; i < N; i++) count[i + 1] += count[i];
  const fill = count.slice();
  for (let i = 0; i < N; i++) if (rcv[i] !== i) donors[fill[rcv[i]]++] = i;
  let top = 0, out = 0; const todo = new Int32Array(N);
  for (let i = 0; i < N; i++) if (rcv[i] === i) {
    todo[top++] = i;
    while (top) { const c = todo[--top]; order[out++] = c; for (let k = count[c]; k < count[c + 1]; k++) todo[top++] = donors[k]; }
  }
  return out;
}
function erode(n, span, h, rise, kScale, { iterations, k, m, diffusion, talus }) {
  const N = n * n, dx = span / n, rcv = new Int32Array(N), dist = new Float32Array(N), order = new Int32Array(N), area = new Float32Array(N), tmp = new Float32Array(N);
  // Routing uses a pit-filled copy; the terrain itself keeps its closed basins.
  let routed = h.slice();
  for (let it = 0; it < iterations; it++) {
    for (let i = 0; i < N; i++) h[i] += rise[i] / iterations;
    if (it % 6 === 0) { routed = h.slice(); floodOrder(routed, n, order, rcv); }
    else for (let i = 0; i < N; i++) routed[i] = Math.max(routed[i] + rise[i] / iterations, h[i]);
    receivers(routed, n, rcv, dist);
    const count = stack(n, rcv, order);
    area.fill(dx * dx);
    for (let q = count - 1; q >= 0; q--) { const i = order[q]; if (rcv[i] !== i) area[rcv[i]] += area[i]; }
    for (let q = 0; q < count; q++) {
      const i = order[q], r = rcv[i]; if (r === i) continue;
      const f = k * kScale[i] * Math.pow(area[i], m) / (dist[i] * dx);
      if (h[r] >= h[i] || routed[i] > h[i] + 0.5) continue; // no incision inside filled pits
      h[i] = Math.max((h[i] + f * h[r]) / (1 + f), h[r]);
    }
    // Hillslope creep plus a talus limit keeps ridges sharp but not needle-like.
    tmp.set(h);
    for (let j = 1; j < n - 1; j++) for (let i = 1; i < n - 1; i++) {
      const id = j * n + i, lap = tmp[id - 1] + tmp[id + 1] + tmp[id - n] + tmp[id + n] - 4 * tmp[id];
      let v = tmp[id] + diffusion * lap;
      const lo = Math.min(tmp[id - 1], tmp[id + 1], tmp[id - n], tmp[id + n]) + talus * dx;
      if (v > lo) v = mix(v, lo, 0.35);
      h[id] = v;
    }
  }
  routed = h.slice(); floodOrder(routed, n, order, rcv);
  receivers(routed, n, rcv, dist);
  const count = stack(n, rcv, order);
  area.fill(dx * dx);
  for (let q = count - 1; q >= 0; q--) { const i = order[q]; if (rcv[i] !== i) area[rcv[i]] += area[i]; }
  return { rcv, area };
}

// ---------------------------------------------------------------- grids
function sampler(field, n, span) {
  // Catmull-Rom sample of a cell-centred grid at world (x, y).
  const at = (i, j) => field[clamp(j, 0, n - 1) * n + clamp(i, 0, n - 1)];
  const cr = (a, b, c, d, t) => b + 0.5 * t * (c - a + t * (2 * a - 5 * b + 4 * c - d + t * (3 * (b - c) + d - a)));
  return (x, y) => {
    const gx = (x / span + 0.5) * n - 0.5, gy = (y / span + 0.5) * n - 0.5;
    const i = Math.floor(gx), j = Math.floor(gy), u = gx - i, v = gy - j, row = [];
    for (let b = -1; b <= 2; b++) row.push(cr(at(i - 1, j + b), at(i, j + b), at(i + 1, j + b), at(i + 2, j + b), u));
    return cr(row[0], row[1], row[2], row[3], v);
  };
}
function bilinear(field, n, span) {
  return (x, y) => {
    const gx = clamp((x / span + 0.5) * n - 0.5, 0, n - 1.001), gy = clamp((y / span + 0.5) * n - 0.5, 0, n - 1.001);
    const i = Math.floor(gx), j = Math.floor(gy), u = gx - i, v = gy - j, o = j * n + i;
    return mix(mix(field[o], field[o + 1], u), mix(field[o + n], field[o + n + 1], u), v);
  };
}
// Coarse landscape: grow ranges under uplift while rivers cut them, then
// bury the range fronts with alluvial fans that coalesce into bajadas.
function coarse(n, span, erosion) {
  const N = n * n, dx = span / n, h = new Float32Array(N), rise = new Float32Array(N), target = new Float32Array(N), kScale = new Float32Array(N);
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = ((i + 0.5) / n - 0.5) * span, y = ((j + 0.5) / n - 0.5) * span, id = j * n + i;
    const u = uplift(x, y);
    target[id] = u;
    const rough = ridged(x / 2600, y / 2600, 4, 40) * 0.5 + fbm(x / 900, y / 900, 3, 41) * 0.15;
    h[id] = u * 0.3 * (0.6 + rough) + basinFloor(x, y);
    rise[id] = u * 1.12 * (0.8 + 0.4 * rough);
    // Basin alluvium is barely incised; bedrock ranges are.
    kScale[id] = 0.04 + 0.96 * smooth(60, 380, u);
  }
  const { rcv, area } = erode(n, span, h, rise, kScale, erosion);
  // Fans: where a sizeable channel leaves the range, deposit a cone.
  const fans = [];
  for (let id = 0; id < N; id++) {
    const r = rcv[id]; if (r === id) continue;
    if (target[id] > 380 && target[r] <= 380 && area[id] > 2e6) fans.push([id, area[id]]);
  }
  const alluvium = new Float32Array(N).fill(-1e9);
  for (const [id, A] of fans) {
    const ai = id % n, aj = (id / n) | 0, apex = h[id] + 4;
    const slope = clamp(0.1 * Math.pow(A / 2e6, -0.2), 0.03, 0.1), reach = Math.min(apex / slope, 3000 + 2.2 * Math.sqrt(A));
    // Concave fan profile out to its toe, then a steeper fade below the floor.
    const toe = apex - slope * reach * 0.62, R = Math.ceil(reach * (1 + Math.sqrt(Math.max(0, toe + 80) / (1.6 * slope * reach))) / dx);
    for (let b = Math.max(0, aj - R); b <= Math.min(n - 1, aj + R); b++)
      for (let a = Math.max(0, ai - R); a <= Math.min(n - 1, ai + R); a++) {
        const d = Math.hypot(a - ai, b - aj) * dx, k = b * n + a;
        const u = d / reach, f = u < 1 ? u - 0.38 * u * u : 0.62 + 0.24 * (u - 1) + 1.6 * (u - 1) * (u - 1), z = apex - slope * reach * f;
        // Smooth union of neighbouring cones.
        const cur = alluvium[k];
        alluvium[k] = cur < -1e8 ? z : Math.max(cur, z) + 6 * Math.exp(-Math.abs(cur - z) / 12);
      }
  }
  const sand = new Float32Array(N);
  for (let id = 0; id < N; id++) {
    const fan = alluvium[id];
    if (fan > h[id]) { sand[id] = Math.min(1, (fan - h[id]) / 25); h[id] = fan; }
  }
  return { h, target, sand, area };
}

// Stripes aligned with the local fall line: branching gullies on steep slopes.
function gullies(x, y, gx, gy, scale, seed) {
  const g = Math.hypot(gx, gy) + 1e-6, nx = gx / g, ny = gy / g;
  const across = (x * -ny + y * nx) / scale, down = (x * nx + y * ny) / (scale * 4.5);
  const warp = 0.9 * noise(x / (scale * 3), y / (scale * 3), seed + 7);
  const a = 1 - Math.abs(noise(across + warp, down, seed)), b = 1 - Math.abs(noise(across * 2.3 + warp, down * 2.1, seed + 1));
  return a * a * 0.65 + b * b * 0.35;
}
function buildGrid(kind) {
  const { size: n, span } = TERRAIN[kind], N = n * n, dx = span / n;
  const far = kind === "far";
  const base = far
    ? coarse(256, span, { iterations: 60, k: 0.25, m: 0.5, diffusion: 0.015, talus: 0.6 })
    : coarse(256, span, { iterations: 70, k: 0.35, m: 0.5, diffusion: 0.02, talus: 0.9 });
  const H = sampler(base.h, 256, span), U = bilinear(base.target, 256, span), S = bilinear(base.sand, 256, span);
  const heights = new Float32Array(N), rock = new Float32Array(N), sand = new Float32Array(N);
  const e = dx;
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = ((i + 0.5) / n - 0.5) * span, y = ((j + 0.5) / n - 0.5) * span, id = j * n + i;
    let z = H(x, y);
    const gx = (H(x + e, y) - H(x - e, y)) / (2 * e), gy = (H(x, y + e) - H(x, y - e)) / (2 * e), slope = Math.hypot(gx, gy);
    const buried = S(x, y), mountain = smooth(60, 420, U(x, y)) * (1 - smooth(0.2, 0.9, buried));
    const scale = far ? 1500 : 260;
    // Fall-line gullies cut into slopes; ridged detail sharpens the crests.
    const gully = gullies(x, y, gx, gy, scale, far ? 60 : 50);
    const crest = ridged(x / (scale * 2.2), y / (scale * 2.2), far ? 3 : 4, far ? 61 : 51);
    z += mountain * (slope * scale * 0.32 * (gully - 0.55) + (far ? 90 : 30) * (crest - 0.45) * smooth(0.05, 0.4, slope));
    heights[id] = z; rock[id] = mountain * smooth(0.12, 0.55, slope); sand[id] = buried;
  }
  return { heights, rock, sand, base };
}

// Mesa and buttes with a resistant caprock, south-west of the pad.
const MESAS = [
  { x: -4300, y: -6200, rx: 2300, ry: 1150, rot: 0.5, top: 190 },
  { x: -1500, y: -8200, rx: 520, ry: 380, rot: 1.1, top: 150 },
  { x: -7400, y: -4300, rx: 700, ry: 450, rot: -0.3, top: 120 },
  { x: 2400, y: -10200, rx: 1400, ry: 700, rot: 0.2, top: 230 },
];
function mesaHeight(x, y) {
  let best = 0;
  for (const m of MESAS) {
    const dx = x - m.x, dy = y - m.y, c = Math.cos(m.rot), s = Math.sin(m.rot);
    const u = (c * dx + s * dy) / m.rx, v = (-s * dx + c * dy) / m.ry;
    const r = Math.hypot(u, v) + 0.16 * fbm(x / 700, y / 700, 3, 70) + 0.05 * noise(x / 160, y / 160, 71);
    if (r > 2.2) continue;
    // Flat cap, a caprock cliff, then a concave talus apron with a bench.
    let z;
    if (r < 1) z = m.top * (1 - 0.03 * r * r);
    else if (r < 1.06) z = m.top * mix(0.97, 0.72, (r - 1) / 0.06);
    else { const t = (r - 1.06) / 1.1; z = m.top * 0.72 * Math.pow(Math.max(0, 1 - t), 1.8); z += 10 * smooth(0.25, 0.35, t) * (1 - smooth(0.35, 0.5, t)); }
    best = Math.max(best, z);
  }
  return best;
}

// Priority flood plus D8 flow on the final grid: drainage for dry washes.
function drainage(h, n, dx, span) {
  const N = n * n, order = new Int32Array(N), rcv = new Int32Array(N), dist = new Float32Array(N), filled = h.slice();
  // Meander noise decorrelates D8 paths from the grid axes.
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = ((i + 0.5) / n - 0.5) * span, y = ((j + 0.5) / n - 0.5) * span;
    filled[j * n + i] += dx * 0.05 * fbm(x / (dx * 9), y / (dx * 9), 3, 80);
  }
  floodOrder(filled, n, order, rcv);
  receivers(filled, n, rcv, dist);
  const area = new Float32Array(N).fill(1);
  for (let q = N - 1; q >= 0; q--) { const i = order[q]; if (rcv[i] !== i) area[rcv[i]] += area[i]; }
  for (let i = 0; i < N; i++) if (filled[i] - h[i] > dx * 0.03) area[i] = Math.min(area[i], 4);
  return { area, filled };
}
function blur(src, n, radius, passes = 3) {
  let a = src.slice(), b = new Float32Array(src.length);
  for (let p = 0; p < passes; p++) {
    for (const horizontal of [true, false]) {
      for (let line = 0; line < n; line++) {
        let acc = 0;
        const at = (k) => { const kk = clamp(k, 0, n - 1); return horizontal ? a[line * n + kk] : a[kk * n + line]; };
        for (let k = -radius; k <= radius; k++) acc += at(k);
        for (let k = 0; k < n; k++) {
          const o = horizontal ? line * n + k : k * n + line;
          b[o] = acc / (2 * radius + 1);
          acc += at(k + radius + 1) - at(k - radius);
        }
      }
      [a, b] = [b, a];
    }
  }
  return a;
}
// Soft sun visibility from the terrain (no detail), at half resolution.
function sunlight(h, n, span) {
  const m = n >> 1, dx = span / n, out = new Float32Array(n * n), half = new Float32Array(m * m);
  let top = -1e9; for (const v of h) top = Math.max(top, v);
  const H = bilinear(h, n, span), tanE = SUN[2] / Math.hypot(SUN[0], SUN[1]);
  const sx = SUN[0] / Math.hypot(SUN[0], SUN[1]), sy = SUN[1] / Math.hypot(SUN[0], SUN[1]);
  for (let j = 0; j < m; j++) for (let i = 0; i < m; i++) {
    const x = ((i + 0.5) / m - 0.5) * span, y = ((j + 0.5) / m - 0.5) * span, z0 = H(x, y) + 2;
    let vis = 1, d = dx * 1.5;
    for (let k = 0; k < 44 && vis > 0; k++) {
      const hz = H(x + sx * d, y + sy * d), clear = z0 + d * tanE - hz;
      vis = Math.min(vis, clamp(0.5 + clear / (d * 0.06 + 6), 0, 1));
      d *= 1.14; d += dx * 0.5;
      if (z0 + d * tanE > top) break;
    }
    half[j * m + i] = vis;
  }
  const S = bilinear(half, m, span);
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) out[j * n + i] = S(((i + 0.5) / n - 0.5) * span, ((j + 0.5) / n - 0.5) * span);
  return out;
}

// ---------------------------------------------------------------- output
let cached;
export function terrainMaps() {
  if (cached) return cached;
  const started = (typeof performance !== "undefined" ? performance : Date).now();
  const farGrid = buildGrid("far"), fineGrid = buildGrid("fine");
  const fine = TERRAIN.fine, far = TERRAIN.far;
  const farH = bilinear(farGrid.heights, far.size, far.span);
  // Morph the fine grid into the far grid at its border so the two match.
  const n = fine.size, dx = fine.span / n;
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = ((i + 0.5) / n - 0.5) * fine.span, y = ((j + 0.5) / n - 0.5) * fine.span, id = j * n + i;
    const edge = Math.max(Math.abs(x), Math.abs(y)), w = smooth(fine.span / 2 - 3200, fine.span / 2 - 200, edge);
    let z = fineGrid.heights[id];
    const mesa = mesaHeight(x, y);
    if (mesa > 0) { z = Math.max(z, z * 0.2 + mesa + 0.8 * Math.min(z, 60)); fineGrid.rock[id] = Math.max(fineGrid.rock[id], smooth(0.8, 0.97, mesa / 230) * 0.9); }
    fineGrid.heights[id] = mix(z, farH(x, y), w);
  }
  // Local base: the pad sits on a distal fan, flattened for the launch area.
  const padZ = bilinear(fineGrid.heights, n, fine.span)(0, 0);
  for (const g of [fineGrid, farGrid]) for (let i = 0; i < g.heights.length; i++) g.heights[i] -= padZ;
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = ((i + 0.5) / n - 0.5) * fine.span, y = ((j + 0.5) / n - 0.5) * fine.span, id = j * n + i;
    const r = Math.hypot(x, y), f = smooth(420, 70, r);
    fineGrid.heights[id] = mix(fineGrid.heights[id], 0, f);
  }
  // A dry lake fills the lowest part of the local basin.
  let playaLevel = 1e9;
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = ((i + 0.5) / n - 0.5) * fine.span, y = ((j + 0.5) / n - 0.5) * fine.span;
    if (Math.hypot(x - PLAYA.x, y - PLAYA.y) < 600) playaLevel = Math.min(playaLevel, fineGrid.heights[j * n + i]);
  }
  playaLevel += 2.2;
  const playa = new Float32Array(n * n);
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = ((i + 0.5) / n - 0.5) * fine.span, y = ((j + 0.5) / n - 0.5) * fine.span, id = j * n + i;
    const pu = (x - PLAYA.x) * CS - (y - PLAYA.y) * SS, pv = (x - PLAYA.x) * SS + (y - PLAYA.y) * CS;
    const near = smooth(3200, 1800, Math.hypot(pu * 1.9, pv) + 500 * fbm(x / 1500, y / 1500, 3, 95));
    if (near > 0 && fineGrid.heights[id] < playaLevel + 1) {
      playa[id] = near * smooth(playaLevel + 1, playaLevel - 0.5, fineGrid.heights[id]);
      fineGrid.heights[id] = mix(fineGrid.heights[id], Math.max(fineGrid.heights[id], playaLevel), near);
    }
  }
  const grids = {}, noPlaya = new Float32Array(TERRAIN.far.size ** 2);
  for (const [kind, g] of [["fine", fineGrid], ["far", farGrid]]) {
    const { size, span } = TERRAIN[kind], cell = span / size, N = size * size, h = g.heights, pl = kind === "fine" ? playa : noPlaya;
    // Shallow washes cut the fans; their flow strength also drives colour.
    const { area } = drainage(h, size, cell, span);
    let wash = new Float32Array(N);
    for (let id = 0; id < N; id++) {
      const a = area[id] * cell * cell, s = kind === "fine" ? 1 : 0.2;
      const w = smooth(1.2e5 / s, 4e6 / s, a);
      wash[id] = w;
    }
    wash = blur(wash, size, 1, 1).map((v, id) => Math.min(1, v * 2.2));
    if (kind === "fine") for (let id = 0; id < N; id++) h[id] -= wash[id] * (1.5 + 2.5 * smooth(4e6, 6e7, area[id] * cell * cell)) * (1 - g.rock[id]);
    const sun = sunlight(h, size, span), wide = blur(h, size, kind === "fine" ? 6 : 4), tight = blur(h, size, 2, 2);
    const colours = new Uint8Array(N * 4), props = new Uint8Array(N * 4);
    let hmax = -1e9;
    for (let j = 0; j < size; j++) for (let i = 0; i < size; i++) {
      const id = j * size + i, x = ((i + 0.5) / size - 0.5) * span, y = ((j + 0.5) / size - 0.5) * span, z = h[id];
      hmax = Math.max(hmax, z);
      const up = i < size - 1 ? h[id + 1] : z, dn = i > 0 ? h[id - 1] : z, no = j < size - 1 ? h[id + size] : z, so = j > 0 ? h[id - size] : z;
      const slope = Math.hypot(up - dn, no - so) / (2 * cell);
      const cavity = clamp((wide[id] - z) / (kind === "fine" ? 90 : 500), -1, 1), convex = clamp((z - tight[id]) / 8, -1, 1);
      const rock = clamp(g.rock[id] + smooth(0.45, 0.9, slope) * 0.6, 0, 1), sandy = g.sand[id];
      const n1 = fbm(x / 1400, y / 1400, 4, 90), n2 = fbm(x / 330, y / 330, 3, 91), n3 = noise(x / 90, y / 90, 92);
      // Linear-light albedo. Bajada gravels are tan with dark desert varnish on
      // older surfaces; washes are paler sand; ranges are grey limestone to the
      // east and darker volcanic rock to the west; the playa is pale clay.
      const west = smooth(-2000, -7000, x * CS - y * SS);
      let r = 0.37, gg = 0.30, b = 0.225;
      const varnish = smooth(-0.1, 0.5, n1) * 0.5 * (1 - wash[id]);
      r -= 0.09 * varnish; gg -= 0.075 * varnish; b -= 0.055 * varnish;
      const shrubs = clamp(0.35 + 0.5 * n2 + 0.35 * wash[id], 0, 1) * (1 - rock) * (1 - pl[id]);
      r = mix(r, 0.12, 0.28 * shrubs); gg = mix(gg, 0.115, 0.28 * shrubs); b = mix(b, 0.075, 0.28 * shrubs);
      // Weathered, varnished rock is darker and warmer than fresh stone.
      // Colluvium on range slopes: varnished stony soil with scrub, darker
      // and browner than basin alluvium. Basin slopes (< 5 %) are unaffected.
      const colluvium = smooth(0.07, 0.28, slope) * (1 - rock);
      r = mix(r, 0.2, 0.65 * colluvium); gg = mix(gg, 0.163, 0.65 * colluvium); b = mix(b, 0.122, 0.65 * colluvium);
      const rr = mix(0.2, 0.135, west), rg = mix(0.168, 0.098, west), rb = mix(0.133, 0.074, west);
      const strata = 0.5 + 0.5 * Math.sin(z / (kind === "fine" ? 23 : 70) + 3 * n1);
      const rockTone = 0.86 + 0.2 * strata * (1 - west) + 0.12 * n3;
      r = mix(r, rr * rockTone, rock); gg = mix(gg, rg * rockTone, rock); b = mix(b, rb * rockTone, rock);
      // Loose talus and colluvium in hollows are paler than the ribs.
      const talus = smooth(0, 0.6, cavity) * (1 - rock * 0.5) * smooth(80, 400, z - 0) * 0.4;
      r += 0.06 * talus; gg += 0.05 * talus; b += 0.04 * talus;
      const w = wash[id] * (1 - rock) * (1 - pl[id]);
      r = mix(r, 0.47, 0.55 * w); gg = mix(gg, 0.40, 0.55 * w); b = mix(b, 0.30, 0.55 * w);
      r = mix(r, 0.56, pl[id] * 0.9); gg = mix(gg, 0.53, pl[id] * 0.9); b = mix(b, 0.47, pl[id] * 0.9);
      const tone = 1 + 0.12 * n2 - 0.06 * convex * rock;
      const o = id * 4;
      colours[o] = clamp(Math.round(255 * Math.sqrt(clamp(r * tone, 0, 1))), 0, 255);
      colours[o + 1] = clamp(Math.round(255 * Math.sqrt(clamp(gg * tone, 0, 1))), 0, 255);
      colours[o + 2] = clamp(Math.round(255 * Math.sqrt(clamp(b * tone, 0, 1))), 0, 255);
      colours[o + 3] = Math.round(255 * clamp(rock, 0, 1));
      props[o] = Math.round(255 * sun[id]);
      props[o + 1] = Math.round(255 * clamp(1 - 0.75 * Math.max(0, cavity), 0, 1));
      props[o + 2] = Math.round(255 * clamp(rock, 0, 1));
      props[o + 3] = Math.round(255 * clamp(w, 0, 1));
    }
    grids[kind] = { heights: h, colours, props, max: hmax, levels: maxMips(h, size, props, kind) };
  }
  cached = { ...grids, noise: noiseTexture(), seconds: ((typeof performance !== "undefined" ? performance : Date).now() - started) / 1000 };
  return cached;
}
// Maximum mip chain of the bilinear surface plus detail headroom. Level 0
// cell (i, j) spans texel centres i..i+1, j..j+1.
function maxMips(h, n, props, kind) {
  const amp = kind === "fine" ? DETAIL_FINE : DETAIL_FAR, levels = [];
  let cur = new Float32Array(n * n);
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const a = Math.min(i + 1, n - 1), b = Math.min(j + 1, n - 1);
    let m = -1e9, r = 0;
    for (const k of [j * n + i, j * n + a, b * n + i, b * n + a]) { m = Math.max(m, h[k]); r = Math.max(r, props[k * 4 + 2] / 255); }
    // detailOctaves() is bounded by 1.03; keep a small margin.
    cur[j * n + i] = m + 1.08 * (amp.base + amp.rock * r) + 0.05;
  }
  levels.push(cur);
  for (let s = n >> 1; s >= 1; s >>= 1) {
    const prev = cur, p = s * 2; cur = new Float32Array(s * s);
    for (let j = 0; j < s; j++) for (let i = 0; i < s; i++)
      cur[j * s + i] = Math.max(prev[2 * j * p + 2 * i], prev[2 * j * p + 2 * i + 1], prev[(2 * j + 1) * p + 2 * i], prev[(2 * j + 1) * p + 2 * i + 1]);
    levels.push(cur);
  }
  return levels;
}
// Tileable smooth gradient noise for shader detail (4 channels).
function noiseTexture() {
  const n = TERRAIN.noiseSize, period = 32, out = new Uint8Array(n * n * 4);
  for (let c = 0; c < 4; c++) for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = i / n * period, y = j / n * period;
    const I = Math.floor(x), J = Math.floor(y), fx = x - I, fy = y - J;
    const u = fx * fx * fx * (fx * (fx * 6 - 15) + 10), v = fy * fy * fy * (fy * (fy * 6 - 15) + 10);
    const g = (a, b, px, py) => { const k = hash(((a % period) + period) % period, ((b % period) + period) % period, 200 + c) & 15; return GX[k] * px + GY[k] * py; };
    const A = g(I, J, fx, fy), B = g(I + 1, J, fx - 1, fy), C = g(I, J + 1, fx, fy - 1), D = g(I + 1, J + 1, fx - 1, fy - 1);
    const val = 1.45 * (A + (B - A) * u + (C - A + (A - B - C + D) * u) * v);
    out[(j * n + i) * 4 + c] = clamp(Math.round(127.5 + 127.5 * val), 0, 255);
  }
  return out;
}
// Shader detail amplitude (m): base everywhere, plus rock-scaled relief.
const DETAIL_FINE = { base: 0.6, rock: 22 }, DETAIL_FAR = { base: 3, rock: 70 };

export const TERRAIN_GLSL = `
uniform sampler2D fineHeight,fineMax,fineColour,fineProps,farHeight,farMax,farColour,farProps,detailNoise;
uniform float fineTop,farTop;
const vec3 SUN=vec3(${SUN.map(v => v.toFixed(6)).join(",")});
const float FINE_SPAN=${fine().toFixed(1)},FAR_SPAN=${farSpan().toFixed(1)},GRID_N=${TERRAIN.fine.size.toFixed(1)};
const float K_CURVE=${(1 / (2 * 8.5e6)).toExponential(6)}; // Earth curvature with standard refraction (4/3 R)
const float SITE_ASL=${TERRAIN.elevation.toFixed(1)};
float detailOctaves(vec2 p,float lambda,float footprint){
 // Ridged detail from a tileable noise texture; octaves below a pixel fade out.
 float sum=0.,amp=.5,w=1.;
 mat2 R=mat2(.8,.6,-.6,.8);
 for(int k=0;k<4;k++){
  float fade=1.-smoothstep(.35,1.,footprint/lambda);
  if(fade<=0.)break;
  float n=textureLod(detailNoise,p/(lambda*32.),0.)[k]*2.-1.;
  n=1.-abs(n);n*=n;
  sum+=amp*fade*(n*w-.42);w=clamp(n*1.7,0.,1.);
  p=R*p*2.03+vec2(17.,-9.);lambda*=.5;amp*=.5;
 }
 return sum*1.9;
}
// Height of the terrain surface including detail. g: 0 fine, 1 far.
float surfaceH(vec2 p,int g,float footprint){
 if(g==0){
  vec2 uv=p/FINE_SPAN+.5;float h=textureLod(fineHeight,uv,0.).r;float r=textureLod(fineProps,uv,0.).b;
  float pad=smoothstep(14.,70.,length(p));
  return h+${DETAIL_FINE.base.toFixed(1)}*pad*detailOctaves(p,24.,footprint)+${DETAIL_FINE.rock.toFixed(1)}*r*detailOctaves(p*.93+vec2(310.,-120.),190.,footprint);
 }
 vec2 uv=p/FAR_SPAN+.5;float h=textureLod(farHeight,uv,0.).r;float r=textureLod(farProps,uv,0.).b;
 return h+${DETAIL_FAR.base.toFixed(1)}*detailOctaves(p,140.,footprint)+${DETAIL_FAR.rock.toFixed(1)}*r*detailOctaves(p*.93+vec2(310.,-120.),1100.,footprint);
}
float rayZ(vec3 o,vec3 d,float t,float h2){return o.z+d.z*t+K_CURVE*h2*t*t;}
float rayZmin(vec3 o,vec3 d,float a,float b,float h2){
 float za=rayZ(o,d,a,h2),zb=rayZ(o,d,b,h2),m=min(za,zb);
 float tv=-d.z/(2.*K_CURVE*max(h2,1e-12));
 if(tv>a&&tv<b)m=min(m,rayZ(o,d,tv,h2));
 return m;
}
// Maximum-mipmap traversal (Tevs et al. 2008) with exact sub-cell marching.
bool traceGrid(int g,vec3 o,vec3 d,float t0,float t1,float pixel,out float tHit){
 float span=g==0?FINE_SPAN:FAR_SPAN;
 float scale=GRID_N/span,h2=dot(d.xy,d.xy);
 vec2 c0=(o.xy/span+.5)*GRID_N-.5,dc=d.xy*scale;
 vec2 inv=vec2(abs(dc.x)>1e-12?1./dc.x:1e30,abs(dc.y)>1e-12?1./dc.y:1e30);
 vec2 dirStep=step(0.,dc);
 float t=t0;int level=6;
 float prevT=t;bool havePrev=false;
 for(int it=0;it<256;it++){
  if(t>=t1)return false;
  vec2 c=c0+t*dc;float size=float(1<<level);
  vec2 cell=floor(c/size);
  if(any(lessThan(cell,vec2(0.)))||any(greaterThanEqual(cell*size,vec2(GRID_N-1.))))return false;
  vec2 bound=(cell+dirStep)*size;vec2 tb=(bound-c0)*inv;
  float tn=min(min(tb.x,tb.y),t1);tn=max(tn,t+1e-3);
  float hmax=g==0?texelFetch(fineMax,ivec2(cell),level).r:texelFetch(farMax,ivec2(cell),level).r;
  if(rayZmin(o,d,t,tn,h2)>hmax){t=tn+.002+t*2e-6;havePrev=false;if(level<9)level++;continue;}
  if(level>0){level--;continue;}
  // Level 0: march the detailed surface between t and tn.
  float a=t;
  if(d.z<0.){float enter=(hmax-o.z)/d.z;a=max(a,enter-1.);}
  float stepLen=max(.25,pixel*a*.5),tt=a;bool done=true;
  for(int k=0;k<48;k++){
   vec3 p=o+tt*d;
   float gap=rayZ(o,d,tt,h2)-surfaceH(p.xy,g,pixel*tt);
   if(gap<0.){
    float lo=havePrev?prevT:max(t0,tt-stepLen),hi=tt;
    for(int r=0;r<7;r++){float mid=.5*(lo+hi);vec3 q=o+mid*d;if(rayZ(o,d,mid,h2)-surfaceH(q.xy,g,pixel*mid)<0.)hi=mid;else lo=mid;}
    tHit=hi;return true;
   }
   prevT=tt;havePrev=true;
   if(tt>=tn){done=true;break;}
   // Gap-limited step (safe for slopes < ~2), floored at half a pixel.
   stepLen=max(.25,max(pixel*tt*.5,gap*.4));
   tt=min(tt+stepLen,tn);done=false;
  }
  if(!done){t=tt;continue;}
  t=tn+.002+t*2e-6;
  if(level<9)level++;
 }
 // Budget exhausted: finish with a bounded march that reports only real
 // crossings of the surface, never an invented one.
 float prev=t;
 for(int k=0;k<96;k++){
  if(t>=t1)return false;
  vec3 p=o+t*d;
  if(any(greaterThan(abs(p.xy),vec2(span*.5))))return false;
  float gap=rayZ(o,d,t,h2)-surfaceH(p.xy,g,pixel*t);
  if(gap<0.){
   float lo=prev,hi=t;
   for(int r=0;r<8;r++){float mid=.5*(lo+hi);vec3 q=o+mid*d;if(rayZ(o,d,mid,h2)-surfaceH(q.xy,g,pixel*mid)<0.)hi=mid;else lo=mid;}
   tHit=hi;return true;
  }
  prev=t;t+=max(.5,max(gap*.5,pixel*t*1.5));
 }
 return false;
}
`;
function fine() { return TERRAIN.fine.span; }
function farSpan() { return TERRAIN.far.span; }
