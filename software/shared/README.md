# Capture and ground interfaces (version 1)

These interfaces connect the native capture service, Python bench tools, and browser. All paths are session-relative unless explicitly supplied by an operator. Runtime evidence belongs under ignored `output/` directories.

## Session artifacts

`session.json` identifies `schema_version: 1`, `session_id`, `created_utc`, `clock_origin_ns`, `clock_domain`, `configuration`, `software`, `hardware`, and `calibration` (a bundle path or null). `clock_origin_ns` is sampled from CLOCK_BOOTTIME once before either camera starts, matching the libcamera SensorTimestamp API; record and investigate any platform timestamp-domain discrepancy. Physical libcamera IDs map explicitly to logical `A` and `B` in configuration. Per-camera `flip_x` and `flip_y` select the actual negotiated orientation; the vendor colour baseline uses FlipY.

`frames.jsonl` contains one record per encoded, captured-only or dropped frame: `schema_version`, `camera_id`, `sequence`, `sensor_timestamp_ns`, `pts_us`, `exposure_us`, `analogue_gain`, `status`, and `drop_reason` (null for successful frames). `sequence` is the camera-buffer sequence, not an application counter. Missing sequence records retain null timestamps. Unknown sensor metadata is null, never invented. Encoded timestamps derive from `(sensor_timestamp_ns - clock_origin_ns)` for both cameras; never rebase each camera independently. The sensor timestamp is a reported capture timestamp, not independently verified exposure time. JSONL is the authoritative timing record; MKV/TS timebases can quantize it.

Additive image-control metadata includes `colour_gains` (`[red, blue]` relative to green), `colour_temperature_k`, `colour_correction_matrix` (3×3 row-major), `awb_enabled`, `ae_enabled`, `exposure_time_mode`, `analogue_gain_mode` and `digital_gain`. Missing reports remain null; older recordings may omit these fields. Requested controls are recorded separately in each camera's `colour_control_provenance`: a request is not proof of its applied value. The private transport PID carries frame metadata unchanged.

`health.jsonl` contains native CLOCK_BOOTTIME samples and explicit events, including camera, encoder, recording, transport, queue, CPU, temperature, clock and undervoltage status where available. The optional Python wrapper records its separate CLOCK_MONOTONIC samples in `host-health.jsonl`; clock domains must be retained when aligning them. The native manifest records a BOOTTIME/MONOTONIC bracket. Missing measurements are null. Each camera writes `A-000001.mkv` or `B-000001.mkv` segments with an index describing their common-timeline start and completion state. Camera, recording and transport failures must remain distinguishable.

`sensors.jsonl` stores full-rate `sensor_sample` records plus typed FC status/events.
Local floating values keep full precision; transport rounding is separate. Each
sample records `source`, `backend`, `sequence`, `valid`, `status`, `values`, error
counters and host read interval. On Linux, `host_clock_domain` is CLOCK_BOOTTIME
although legacy field names retain `monotonic_us`, `read_start_monotonic_us` and
`last_good_monotonic_us`. Publication adds session-relative `pts_us`,
`publication_timestamp_ns`, `clock_domain` and `acquisition_clock_domain`.
These times do not prove the physical conversion instant or IMU synchronization.

## Capture executable

`pv-capture --list-cameras` emits JSON camera IDs. `pv-capture --config <configuration.json>`
starts a session and handles SIGINT/SIGTERM shutdown. `--config -` reads stdin;
`--status [path]` reads the last atomic snapshot.

Configuration version 1 has `session_dir`, `cameras` (objects with `id` A/B and `device` libcamera ID), `width`, `height`, `fps`, `bitrate`, `vbv_bits`, `preset`, `segment_seconds`, `min_free_bytes`, `record`, `udp_destination` (null or host:port), `mux_bitrate`, and optional `duration_seconds`. `profile` is `bench` or `flight`. `session_root`
creates a new random session directory instead of a fixed `session_dir`; zero,
one or two configured cameras are supported. Linux `lock_path` prevents concurrent
capture processes; `status_path` reports lifecycle/components independently of FC
phase. The default Linux lock is `/run/lock/pv-capture.lock`; status defaults to
`<session_dir>/status.json`. The flight template selects `/run/pv-capture-status.json`,
which is also the no-argument `--status` lookup path. Use the same capture lock
for every process that owns the real hardware.

`encode: false` selects capture-only measurement and requires recording/UDP/SPI disabled. Defaults: 1552 square, 30 fps, 4000000 bit/s per camera, 2000000-bit VBV, ultrafast, 60-second segments, 2147483648-byte free-space floor, recording enabled, no UDP destination, 9000000 bit/s transport. Camera mode is full 2064x1552 RAW10; derive and report an ISP square crop from actual libcamera pixel-array properties. Never silently accept a narrowed sensor mode.

Optional shared `camera_controls` supports `exposure_us` plus `analogue_gain` together (manual exposure), `colour_gains: [red, blue]` (manual white balance), and `colour_correction_matrix` (requires manual white balance). Omit the object to retain vendor defaults. Empty, unknown or incomplete settings fail; runtime support and limits are checked. Compare per-frame reported controls when evaluating a shared-settings experiment, including any ISP adjustment to the requested matrix. See the [native capture guide](../flight/README.md).

The optional capture setting `encoder_threads` is an integer from 1 through 8, defaulting to 2 per camera encoder. It is retained in the effective session configuration so thread-count experiments can be compared without changing the image profile or other encoder settings.

The optional `encoder_input` setting is exactly `"dmabuf"` (default) or `"copy"`. The default passes mapped libcamera buffers to FFmpeg with their capture leases. `copy` copies active YUV pixels and AVFrame properties into one reusable CPU-allocated AVFrame per camera, respecting independent source/destination strides; it ends DMA CPU access and releases the capture request before encoding. Retained FFmpeg references remain protected through `av_frame_make_writable`. Both modes preserve the sensor mode, image dimensions, crop, PTS, colour and encoder settings. The effective configuration and each camera description record the selected mode. This is an experiment switch, not a claim of faster encoding or a different pixel format.

The independent optional `capture_allocator` setting is exactly `"libcamera"` (default) or `"dma_heap_cached"`. Cached allocation imports one negotiated `frame_size` allocation per request from `/dev/dma_heap/vidbuf_cached`, with no silent fallback. Both paths use the same request ownership, CPU READ sync and AVFrame lease. This setting does not imply an encoder copy; compare it with `encoder_input:"dmabuf"` to isolate allocation. Camera descriptions record `capture_allocator`, nullable `dma_heap_path` / `dma_heap_target`, and `buffer_planes` (one list of `{offset,length}` objects per buffer). Heap selection is recorded evidence, not a measurement of runtime page attributes or performance. No arbitrary heap path is accepted in version 1.

Per-camera health `timing_us` reports cumulative `capture_queue_dwell`, `encoder_send_call`, and `encoder_receive_call` counters, each with `samples`, `total_us`, `mean_us`, `min_us`, and `max_us`. Mean and extrema are null before the first observation. Queue dwell runs from enqueue attempt to normal worker dequeue; rejected frames have no dwell sample. Codec counters time individual FFmpeg calls, including EAGAIN, retries, and flush calls, while excluding subsequent metadata/log/output handling and counter bookkeeping. These are wall-clock call durations, not CPU time or complete per-frame encoding latency.

`encoder_input_copy` measures cached-frame preparation (`make_writable`, pixel and property copy) only in copy mode; its sample count stays zero in dmabuf mode. `encoder_input_and_send` has one sample per frame submission, summing that copy interval and all direct `avcodec_send_frame` intervals for that frame, including a retry after EAGAIN. It excludes flush, receive calls, logging/output handling, DMA synchronization/release, and counter bookkeeping. Compare this combined duration together with encoded cadence, capture losses, queue dwell and CPU usage; a lower send-call duration by itself can merely move work into the copy. These cumulative timing snapshots may straddle an in-flight frame; compare drained session totals or a sufficiently long steady interval.

`dma_sync_start` and `dma_sync_end` measure CPU READ cache synchronization separately from encoder calls. Transient EINTR/EAGAIN errors retry at most 16 times with 1 ms backoff. A terminal sync error sets per-camera `dma_quarantined:true` and `failed:true`, logs `dma_sync_failed`, and prevents further request recycling until the camera stops. The camera retains its buffers, mappings and requests until all encoder references are released and shutdown completes. START/END errors never turn into a successful fallback allocator or permission change.

## Carrier telemetry and FC UART

Backends are explicit: sensors `disabled`/`simulation`/`i2c`, UART
`disabled`/`simulation`/`serial`. I²C mappings supply a device path and address per
source; serial supplies its device path. Configuration and backend labels preserve
provenance. No populated carrier, pin map or physical sensor measurement is implied.

Sensor `values` use SI-named fields: `accel_m_s2[3]`, `gyro_rad_s[3]`, `pressure_pa`,
`temperature_c`, `bus_voltage_v`, `shunt_voltage_v`, `current_a`, `power_w`.
Axes are sensor-native; no rig/body transform is applied. Saturation, FIFO overrun,
pressure range and `power_basis` are reported where applicable. Invalid readings
have `values:null`, never new zero readings. Bosch source revisions are pinned in
[vendor provenance](../flight/vendor/PROVENANCE.md).

Downlink `type:"sensors"` records carry `schema_version:1`, `session_id`, `pts_us`,
`sample_count`, `value_significant_digits:6`, `dropped_samples` and `sources`.
Envelope `backend` and `timestamp_basis` preserve provenance; a source overrides
them only when different. Each source groups rows by validity/status/errors with
`base_monotonic_us`;
row time columns are offsets from that base. Default columns are `sequence`,
`monotonic_us`, `read_start_monotonic_us`, `last_good_monotonic_us`, followed by
source-specific numerical `values.*` columns. Explicit `columns` is retained
when needed. Constant boolean/string values use `shared_values`; absent flags
are not inferred false. Omitted `status` means fresh; omitted `errors` and
`missed_polls` mean zero. Publication is every
100 ms, with ≤2500 bytes per batch, bounded acquisition backlog and transport
loss/rate/expiry counters. Full-precision local JSONL remains independent.

Proposed receive-only FC wire contract (FC firmware acceptance still pending):

```text
PV1,<seq>,<uptime_ms>,<phase>,<event_seq>,<event>*<crc16>\n
```

115200 8N1, no flow control; 3.3 V levels/pins require electrical confirmation.
Maximum 96 bytes including LF; decimal uint32 counters wrap. Phase is UNKNOWN,
PAD, ASCENT, DESCENT or LANDED. Event is NONE, LAUNCH, APOGEE, DEPLOYMENT or
LANDED. CRC-16/CCITT-FALSE covers bytes before `*`: polynomial 0x1021, initial
0xFFFF, no reflection, final XOR zero, four uppercase hexadecimal digits on transmission. Send a 2 Hz
heartbeat and immediate changes; advance `seq` on each frame and `event_seq` on
an event, retaining the latest event in later heartbeats. The receiver emits
`flight_status` and deduplicated `flight_event` records; these are labels only.

At ≥3 s without a valid accepted frame, `valid:false`, `stale:true` and phase
UNKNOWN apply. Bad/duplicate/out-of-order frames do not refresh freshness.
After staleness, two valid progressing frames may establish `inferred_epoch`;
this is conservative reacquisition, not proof of a physical FC reboot. FC uptime
and CM5 receive time remain separate. There is no command/control path.

## Transport and browser

MPEG-TS: video PIDs 256 (A), 257 (B), private data PID 258 (binary private data, stream type 0x06), each private-data PES containing one UTF-8 JSON frame record (or a versioned sensor/FC/health/session record identified by `type`). Metadata PES scheduling uses common-clock producer queue-admission time to keep its single stream monotonic; this timestamp is immutable while queued. Never replace it with the paced consumer's current time: doing so feeds queue delay into CBR null padding and increases latency. The JSON preserves each frame's original `pts_us` and sequence. Budget metadata within the 9000000 bit/s total; additional sensor/UART records
use a 250000 bit/s TS token budget and a separate 64-record queue. Its loss does
not trigger video keyframe recovery. UDP datagrams contain an integral number of 188-byte TS packets, at most seven (1316 bytes). No RTP. Preserve the shared video PTS timeline, with no independent stream rebasing or B frames. Use one-second IDRs with repeated SPS/PPS; recorder segments start on IDRs. PCR is configured for 20 ms and PAT/PMT for 100 ms; verify clock continuity with
one/both cameras absent before treating telemetry-only transport as qualified.

`pv spi --udp 127.0.0.1:1234` consumes this feed unchanged and wraps each group of up to seven TS packets in one fixed 1332-byte PV-SPI v1 transfer. READY gates every transfer; a 16-bit sequence number and IEEE CRC-32 cover the SPI framing. Set capture `udp_destination` to the same local endpoint. `--mirror-udp MAC_IP:1234` sends a raw TS copy after each completed SPI write for the existing ground receiver. This copy does not establish RP2350 acceptance. The sender reports sent-message CRC chain, waits, source/queue losses and unsent data separately from native capture evidence. See the self-contained [SPI interface and bring-up guide](../flight/spi.md).

The native mux applies one common 1,000,000 microsecond PTS offset to both video streams for its 500 ms decoding lead. Repeated session metadata declares `transport_pts_offset_us`; the ground receiver subtracts it before pairing video and capture metadata. JSONL and local Matroska retain original capture PTS. This offset preserves the phase between A and B. Session metadata also carries negotiated geometry, including `sensor_crop` in canonical full-mode coordinates, for calibration compatibility checks.

The Python ground receiver demuxes the same live or recorded TS path with PyAV. Each browser WebSocket binary message comprises a four-byte big-endian JSON-header length, a UTF-8 JSON header, then an Annex-B H.264 access unit. Header fields: `type: "frame"`, `camera_id`, `timestamp_us`, `keyframe`, `codec` (RFC 6381 AVC codec string derived from SPS). Key access units include SPS/PPS. WebSocket text messages carry typed `status`, `metadata`, `calibration`, and `error` JSON objects. Ground accepts metadata-only TS sources and displays telemetry/phase freshness.
Ground playback control and configuration are separate from flight control.

Each browser decoder retains original timestamps. Queue bounds and stale/missing cameras are explicit. Synthetic media is an explicit source, never an automatic fallback. Ground routes and CLI integration live in `pigeonvision.ground`.

`pv-flight.service` uses `Restart=no`. A new SPI session needs coordinated
RP2350/link reset; uncertain native SPI transfers latch the transport failure and
produce exit 78 after shutdown. READY is buffer credit and a completed ioctl is
not an acceptance ACK. Carrier power sequencing and PA control remain unmapped.
See the [architecture source](../architecture.drawio) and
[startup/shutdown preview](../diagrams/startup-shutdown.svg).

## Calibration

`calibration.json` contains `schema_version: 1`, `model: "mei"`, `cameras` keyed by A/B, and validation evidence. Each camera has `image_size: [width,height]` for the canonical unflipped full sensor, `K` (3x3), `D` (four Mei radial/tangential distortion coefficients), `xi`, `R_camera_from_rig` (3x3), `crop: [x,y,width,height]`, `output_size`, `flip_x`, `flip_y`, and `valid_radius_px` (null if no circular mask was measured; otherwise centred on K's principal point in full-sensor pixels). Fitting observations are unflipped using explicit dataset orientation. Projection: rotate the rig ray; normalize it; use x/(z+xi), y/(z+xi), then radial/tangential distortion, K, crop, pixel-centre scaling `(u-x+0.5)*scale-0.5`, and flips. Reject z+xi<=0 and, when xi>1, z<=-1/xi; also apply measured angular coverage and image masks. This is an infinity projection; near-field parallax is not corrected.

Calibration utilities must distinguish fitting observations from held-out validation, preserve rejected observations and report error by image region and seam overlap. Lack of real calibration is an explicit uncalibrated state, never a fabricated successful calibration.
