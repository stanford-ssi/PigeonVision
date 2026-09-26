# Ground receiver

The receiver uses the shared version 1 transport/calibration contracts. It serves only ground playback controls; there is no flight-control endpoint.

From the repository root, after installing the Python package:

```sh
pv view udp://0.0.0.0:5000
pv replay output/session/transport.ts --calibration output/calibration/calibration.json
```

The standalone interface is also available:

```sh
python -m pigeonvision.ground output/session/transport.ts --calibration output/calibration/calibration.json
```

Open `http://127.0.0.1:8768/` in desktop Chrome with H.264 WebCodecs and WebGL2 support. Python callers can use `pigeonvision.ground.run(source, host="127.0.0.1", port=8768, calibration=None, open_browser=True, replay=None)`; `replay=None` detects a file versus a UDP listening URL. The async application factory is `pigeonvision.ground.server.create_app`.

## Playback and projection

Saved transports begin paused with one pair loaded. Play, Pause, Step pair and Restart share one server playback timeline across connected browser tabs; connecting another replay viewer restarts that shared timeline. Raw A/B views work without calibration. The panoramic, perspective, and coverage views require a Mei bundle; they never substitute simulator imagery for missing camera data.

The crop uses full-sensor pixel centres: `(u - crop_x + 0.5) * scale - 0.5`, followed by output flips. Per-camera rotations use +Z forward through A, +X right and +Y down for the rig. The Mei `xi > 1` second projection branch is rejected. Optional `max_theta_deg` bounds measured coverage; absent angular limits remain explicitly unvalidated.

Session camera descriptions and per-frame canonical `sensor_crop` are checked against the bundle. Device, negotiated flips, sensor mode, output dimensions or crop mismatches disable panoramic views. Missing evidence is explicitly unverified. A raw `scaler_crop` is not treated as full-sensor coordinates without the native capture service's origin/scale conversion. Imported calibration is not a statement that hardware synchronization, edge quality, lens retention, or rig alignment has passed physical testing.

## Timing and failure behavior

MPEG-TS PIDs 256/257 identify A/B and 258 carries JSON metadata. Independent camera offsets are preserved. The native mux's declared `transport_pts_offset_us` is removed once from both video timelines; an external transport with no declaration uses zero. An offset declaration arriving late resets both decoders before subsequent keyframes. The native sender must repeat session metadata for late joins.

The service audits raw 188-byte TS continuity counters before PyAV demux. Packet corruption, a continuity gap, a backward timestamp, and receive-queue overflow cause explicit decoder recovery at IDRs. A 48-message receive queue, browser ingress bound, decoder queue bound and six-frame pairing queues prevent indefinite backlog. Live read failure reconnects; a stopped source displays a held/stale image. There is no synthetic fallback.

WebSocket `/ws` carries the shared Annex-B access-unit envelope and typed status/metadata/calibration/error messages. JSON `{ "type": "control", "action": "play" }` also accepts `pause`, `step` and `restart` for saved sources. `/health` reports counters and first-access-unit delay. That delay and timestamp pairing do not measure camera exposure synchronization or glass-to-glass latency.

## Verification

`software/tests/test_ground_transport.py` generates small H.264 streams and exercises real PyAV mux/demux, fixed inter-camera offsets, private metadata/common mux offset, actual localhost UDP, continuity loss/IDR recovery, HTTP and WebSocket replay. `test_ground_projection.py` compares the reference projection against OpenCV, including rays beyond 90° from the lens axis.

The optional Playwright checks require a running local viewer and desktop Chrome. `test_ground_browser.cjs` verifies dual decode, paused stepping and playback. `test_ground_shader.cjs` compares GPU texture coordinates against Python reference vectors and checks geometry mismatch handling. All these checks use explicitly generated or pre-existing synthetic media. No real camera calibration or hardware qualification is implied.
