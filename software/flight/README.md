# Native CM5 capture

`pv-capture` runs dual IMX900 capture, software x264, segmented recording and
MPEG-TS/UDP on the CM5 using FRAMOS libcamera and the Pi ISP. Initial hardware
runs are documented in the [bench notes](../bench-notes/2026-09-25.md);
short-run success is not sustained qualification. There is no synthetic fallback.

## Build and configure

After verifying the FRAMOS kernel/libcamera installation, from the repository
root **on the CM5**:

```sh
sudo apt install cmake g++ pkg-config libavcodec-dev libavformat-dev libavutil-dev nlohmann-json3-dev
cmake -S software/flight -B build/flight -DCMAKE_BUILD_TYPE=Release
cmake --build build/flight -j2
ctest --test-dir build/flight --output-on-failure
build/flight/pv-capture --list-cameras
build/flight/pv-capture --config output/capture-config.json
```

`pkg-config libcamera` must resolve FRAMOS headers/library, version 0.5.2 or
newer. FFmpeg 7+ must provide libx264; the API was checked against 7.1.1.
This build does not install drivers.

See [configuration and artifact contracts](../shared/README.md). `--config -`
accepts stdin; relative `session_dir` resolves against execution CWD, including
SSH. A directory containing only configuration is allowed; an existing session
manifest is refused. Map one or two physical device IDs explicitly to A/B.

| Setting | Behavior |
| --- | --- |
| `encode:false, record:false, udp_destination:null` | Capture-only measurement |
| `record:false` with encoding enabled and no UDP | Encoder-only measurement |
| Per-camera `flip_x`, `flip_y` | Defaults false/true for the vendor colour workaround; unsupported transforms fail |
| `encoder_threads` | 1–8 slice threads per camera, default 2 |
| `encoder_input` | `"dmabuf"` default; `"copy"` for the CPU-copy experiment |
| `capture_allocator` | `"libcamera"` default; `"dma_heap_cached"` for imported cached buffers |

## Sensor mode, rate and timestamps

Capture pins **2064×1552 RAW10** and requests Rec709 YUV420. ISP output selects an
aspect-ratio crop from that mode; square profiles preserve full height before
scaling. Requested and actual crops are recorded. Canonical `sensor_crop` converts
pixel-array ScalerCrop using the selected mode's ScalerCropMaximum origin/scale;
the manifest retains that transform, active areas, physical IDs and orientation.

The pinned driver uses private `Frame rate` control **0x009819b1**, in micro-fps,
with fixed VBLANK. `FrameDurationLimits` alone leaves maximum sensor cadence.
Capture finds the sensor by exact device-tree-node equality with
`/sys/class/video4linux/v4l-subdev*/device/of_node`, verifies FRAMOS compatibility
and the control's ID/name/type/range, writes the requested rate after mode
selection, and reads it back. It then reconfigures the **same mode** to refresh
libcamera/IPA VBLANK, exposure and duration limits. Changed readback or stale
limits fail configuration. `frame_rate_control` records this evidence; verify
actual SensorTimestamp cadence and AE behavior rather than relying on readback.
Relevant pinned source: `fr_imx900.c` rate/control/format functions,
`CameraSensorLegacy::setFormat`, and `CameraData::configureIPA`.

Both cameras share CLOCK_BOOTTIME origin per SensorTimestamp's contract; a
bracketed CLOCK_MONOTONIC sample is also recorded. FRAMOS forwards CFE buffer
timestamps, which do not independently prove the kernel clock domain or exposure
time. Missing, regressing or implausible timestamps are rejected. Prohibit suspend,
verify the target clock, and measure exposure synchronization separately. Sensors
initially free-run; drops retain real timestamp gaps.

## Buffer ownership and allocation experiments

Each camera requests eight buffers and uses a three-frame queue. The completion
callback performs no encoding or disk/network I/O. Reference-counted AVFrames
hold mapped buffers until x264 releases them. Encoding uses no B frames, a
one-second GOP, repeated SPS/PPS and configured bitrate/VBV.

`encoder_input:"copy"` copies active YUV pixels and properties into a reusable
CPU AVFrame with independent padded strides. `av_frame_make_writable` protects
retained FFmpeg references. DMA access ends and capture recycles before encoding.
Health separates copy time from `encoder_input_and_send`, their combined cost;
a shorter send call alone is not faster throughput. Copy mode was slower in the
initial comparison and was not adopted.

`capture_allocator:"dma_heap_cached"` opens only `/dev/dma_heap/vidbuf_cached`;
permission, allocation or import failures have no fallback. Each request receives
one negotiated `frameSize` allocation with one plane at offset zero. Respect
stride/padding: the observed full-sensor layout is stride **2176**, **5065728 bytes**.
The manifest records allocator, heap/resolved target and plane offsets/lengths.
With `encoder_input:"dmabuf"`, leases retain imported buffers without an extra copy;
sensor, crop, colour, timestamps and encoder settings remain unchanged.

CPU reads require `DMA_BUF_IOCTL_SYNC` START/END READ. EINTR/EAGAIN retry at most
16 times with 1 ms backoff; kernel ioctl execution itself has no userspace time
bound. Terminal failures quarantine requests until shutdown. START failure rolls
back successful STARTs; END failure prevents requeue while preserving active
state so shutdown stops the camera. The failure callback cannot throw through
FFmpeg. Health reports `dma_quarantined`, `dma_sync_start` and `dma_sync_end`.
Sync does not replace request-completion and AVFrame lifetime ordering.

Compare equal settings after warmup with repeated controls. Check encoded cadence,
losses, dwell, CPU/temperature, DMA and codec times; decode segments for stale
pixels/stride/colour errors and exercise stop/restart. Initial cached-buffer
results improved throughput, but runtime page attributes and sustained reliability
remain unqualified. Heap backing/import behavior is platform-dependent.

Implementation references: [x264 input copy](https://code.videolan.org/videolan/x264/-/blob/31e19f9/common/frame.c),
[rpicam cached allocation](https://github.com/raspberrypi/rpicam-apps/blob/24906da670e9f2468ac18f2885218b2ba3491a20/core/rpicam_app.cpp),
[PiSP vb2_dma_contig](https://github.com/raspberrypi/linux/blob/stable_20250916/drivers/media/platform/raspberrypi/pisp_be/pisp_be.c),
[FRAMOS buffer import](https://github.com/framosimaging/framos-libcamera/blob/160625a1f0eee5d7522421e0ed52327f35fcbcff/src/libcamera/pipeline/rpi/common/pipeline_base.cpp),
[kernel CPU-sync contract](https://www.kernel.org/doc/html/latest/driver-api/dma-buf.html#cpu-access-to-dma-buffer-objects).

## Recording and transport

Encoded packets feed independent 120-packet recording queues and a 240-item
transport queue. Recorders rotate at IDRs after the segment duration. Space/write
errors disable recording while transport continues; queue loss is marked at its
actual boundary, skipping dependent frames until an IDR. Sinks have separate drop
counters. Camera/encoder failure stops that camera; the other continues, with no
in-process restart yet. SIGINT/SIGTERM drains work; finalization/log failures
make the exit unsuccessful.

One 9 Mb/s TS program uses video PIDs 256/257 and private JSON PID 258; UDP payloads
contain at most seven 188-byte packets. One paced mux owner bounds interleaving;
socket errors do not block capture. The mux's 500 ms decode lead adds **one common
1,000,000 µs PTS offset**, declared in repeated session metadata and subtracted
from both streams by ground. MKV/JSONL retain original capture PTS.

Metadata PES uses producer queue-admission timestamps assigned under its ordering
lock; JSON keeps exact capture `pts_us`. Substituting paced-consumer wall time
feeds backlog into CBR padding. Health records transport queue age, bytes and last
video timestamps; session/geometry metadata repeats each second for late joins.

## Verification

Portable tests need no camera libraries:

```sh
cmake -S software/flight -B build/flight-portable -DPV_BUILD_CAPTURE=OFF
cmake --build build/flight-portable
ctest --test-dir build/flight-portable --output-on-failure
```

With FFmpeg/JSON development packages, `-DPV_BUILD_MEDIA_TESTS=ON` adds
`pv-media-test input-h264.mp4 new-output-directory [storage-floor|transport-failure|cbr-burst]`.
Use `docs/assets/video/cil212-camera-a.mp4`. It exercises actual recorder/TS/UDP
code: PID/frame counts, private JSON, PCR/decode timing, A-stop clock continuity,
IDR segmentation, sink isolation and producer timestamps under encoder bursts.
It does not qualify acquisition, encoding speed, optics or synchronization.
See the [qualification targets](../README.md#benchmark-and-qualify) for remaining
long-run, latency, power, storage and calibration measurements.
