# Native CM5 capture

`pv-capture` is the Linux camera data path. It uses the pinned FRAMOS libcamera
stack, the Pi ISP and software libx264. Python bench tools own provisioning,
configuration, calibration and analysis. There is no automatic synthetic camera
fallback and no claim that the target workload has passed on hardware yet.

Build on the CM5 after the FRAMOS kernel/libcamera installation is verified:

```sh
sudo apt install cmake g++ pkg-config libavcodec-dev libavformat-dev libavutil-dev nlohmann-json3-dev
cmake -S software/flight -B build/flight -DCMAKE_BUILD_TYPE=Release
cmake --build build/flight -j2
ctest --test-dir build/flight --output-on-failure
build/flight/pv-capture --list-cameras
build/flight/pv-capture --config output/capture-config.json
```

`pkg-config libcamera` must resolve the **FRAMOS** installation and its headers
(0.5.2 or newer), not an unrelated OS libcamera. FFmpeg 7 or newer must provide libx264;
startup fails clearly if the encoder is unavailable. The API is checked against
FFmpeg 7.1.1 and the retained FRAMOS headers. No driver installation occurs here.

Configuration follows `software/shared/README.md`. `--config -` reads JSON from
stdin. Relative `session_dir` paths resolve against execution CWD, including over
SSH. Existing directories containing a config are allowed; existing session
manifests are refused. Supply one or two explicit physical camera IDs mapped to
logical A/B. `encode:false, record:false, udp_destination:null` measures capture
without encoding. `record:false` with normal encoding measures the encoder only.
Per-camera `flip_x` defaults false and `flip_y` defaults true for the vendor's
full-resolution color workaround; unsupported transforms fail configuration.

The service pins the 2064×1552 RAW10 sensor mode and requests Rec709 YUV420 from
the ISP. Output dimensions select an aspect-ratio crop from the full selected
sensor region. Square profiles preserve full sensor height, then scale to the
requested output size. Both requested and actual per-frame crops are recorded.
`sensor_crop` converts the pixel-array ScalerCrop to canonical full-mode sensor
coordinates using the selected mode's ScalerCropMaximum origin and scale. The
manifest records that transform, active areas, physical IDs and orientation.

The pinned FRAMOS IMX900 driver controls cadence through its private `Frame rate`
control (`0x009819b1`, micro-fps), and exposes VBLANK as a fixed range. Ordinary
libcamera `FrameDurationLimits` alone therefore leaves the sensor at its maximum
rate. Capture resolves the sensor subdevice by exact equality between the camera
ID's device-tree node and `/sys/class/video4linux/v4l-subdev*/device/of_node`.
It verifies the FRAMOS compatible, control ID/name/type/range, writes the requested
rate after mode selection, and reads it back. It then configures the **same mode
again** to refresh libcamera/IPA's cached VBLANK, exposure and frame-duration
limits. The pinned driver's `imx900_set_pad_format` preserves the private rate
when the mode is unchanged. A changed readback or stale frame-duration range
fails configuration; capture must not continue with stale AE timing limits.
The manifest's `frame_rate_control` records the node, requested/readback values,
refreshed duration limits and exposure ceiling. Readback does not prove actual
cadence: check SensorTimestamp deltas and AE behavior on each installed version.
Source provenance: FRAMOS `framos-rpi-drivers/drivers/fr_imx900.c`, functions
`imx900_update_frame_rate`, `imx900_set_ctrl`, `imx900_set_pad_format`; FRAMOS
libcamera `CameraSensorLegacy::setFormat` refreshes control info, followed by
`CameraData::configureIPA`. Versions remain pinned in the platform manifest.

Each camera has eight requested libcamera buffers and a three-frame queue.
Mapped DMA buffers are held by reference-counted AVFrames until x264 releases
them; the completion callback does no encoding or disk/network IO. Each encoder
uses `encoder_threads` slice threads (default two), no B frames, a one-second GOP, repeated SPS/PPS, configured
bitrate/VBV and the selected preset. Dropped frames keep real timestamp gaps.

`encoder_input:"copy"` enables a controlled cached-input experiment. It copies
active YUV pixels into a reusable CPU-allocated AVFrame with padded strides and preserves capture
PTS and colour properties. DMA CPU access ends and the camera request is recycled
after the copy, before encoding. `av_frame_make_writable` prevents overwriting
pixels still referenced by FFmpeg. The default `"dmabuf"` path remains unchanged.
Health includes `encoder_input_copy` and `encoder_input_and_send`; the latter sums
copy preparation and direct send-call durations per frame, so moving time out of
the encoder call cannot be mistaken for a throughput improvement. Check actual
encoded cadence, capture loss and queue dwell as well. Keep full sensor mode,
output size, frame rate, thread count, bitrate/VBV and scene fixed between runs;
alternate the modes after warm-up and compare repeated, compiler-free intervals.

Source review motivates this comparison but does not prove a cache bottleneck:
the pinned [x264 input path](https://code.videolan.org/videolan/x264/-/blob/31e19f9/common/frame.c)
copies/interleaves incoming pixels into internal frames before motion analysis.
Our libcamera FrameBufferAllocator exports PiSP V4L2 MMAP buffers, whereas
[rpicam-apps at the installed commit](https://github.com/raspberrypi/rpicam-apps/blob/24906da670e9/core/dma_heaps.cpp)
uses imported cached DMA-heap buffers. The
[pinned PiSP driver](https://github.com/raspberrypi/linux/blob/stable_20250916/drivers/media/platform/raspberrypi/pisp_be/pisp_be.c)
uses `vb2_dma_contig`; mapping cache attributes depend on the DMA/device path.
Runtime page attributes have not been measured. Copy mode adds another full
image transfer and may be slower; it must earn adoption through hardware results.

`capture_allocator:"dma_heap_cached"` is a separate opt-in experiment that
allocates ISP output directly from `/dev/dma_heap/vidbuf_cached`. The default is
`"libcamera"`, retaining FrameBufferAllocator. Cached mode opens only that heap;
missing permissions, allocation or import failures are errors, with no fallback.
It follows the pinned [rpicam-apps allocation layout](https://github.com/raspberrypi/rpicam-apps/blob/24906da670e9f2468ac18f2885218b2ba3491a20/core/rpicam_app.cpp):
one buffer of negotiated `frameSize`, one plane at offset zero, for each of the
same eight requests. Use negotiated stride and frame size, including padding;
the observed full-sensor layout is stride 2176 and 5065728 bytes, not width ×
height × 1.5. The manifest records the allocator, heap path and resolved target,
plus each buffer's plane offsets and lengths. The configured sensor, ISP crop,
timestamps, colour and encoder settings are unchanged by allocator selection.

With `encoder_input:"dmabuf"`, the existing AVFrame lease holds the imported
buffer until all encoder references release, with no extra pixel copy. CPU reads
remain bracketed by `DMA_BUF_IOCTL_SYNC` START/END READ; EINTR/EAGAIN retry up
to 16 attempts with 1 ms backoff, and terminal sync failures quarantine the
camera's requests until shutdown. The retry loop is bounded; individual kernel
ioctl execution time is not guaranteed by userspace. The
failure callback cannot throw through FFmpeg. START failure rolls back every
successful START; END failure suppresses requeue, while retaining active state
so the main loop still stops the camera. Health reports `dma_quarantined` and
separate `dma_sync_start` / `dma_sync_end` timing counters. Cache synchronization
does not establish device completion: request completion and AVFrame ownership
continue to enforce that ordering.

Compare default versus cached allocation using direct encoder input, equal
full-sensor settings, a stationary scene, and repeated runs after warm-up. Check
actual encoded cadence, accounted losses, queue dwell, CPU/temperature and both
DMA sync and codec time. Decode the completed segments and inspect changing
images for stale pixels/colour/stride corruption; exercise stop and restart.
These checks test the hypothesis, not a proven cache bottleneck. Heap backing
and importer behavior depend on the installed kernel/platform; Pi 5 normally
resolves `vidbuf_cached` to the system heap, but that must be recorded on target.
The [pinned FRAMOS pipeline](https://github.com/framosimaging/framos-libcamera/blob/160625a1f0eee5d7522421e0ed52327f35fcbcff/src/libcamera/pipeline/rpi/common/pipeline_base.cpp)
registers externally supplied request buffers, and the
[kernel DMA API](https://www.kernel.org/doc/html/latest/driver-api/dma-buf.html#cpu-access-to-dma-buffer-objects)
requires explicit CPU sync even when a mapping appears coherent.

Encoded packet references fan out to independent 120-packet recording queues
and a 240-item transport queue. Recorders rotate on IDRs after the configured
segment duration; recording space/write errors disable that sink while transport
continues. Queue loss is marked at its actual packet boundary, and dependent
frames are skipped until a fresh IDR. Sink drops have separate health counters.
Camera/encoder failures disable the affected session; the other continues.
There is no in-process camera restart yet. All queued work drains on SIGINT or
SIGTERM, and output finalization/log failures affect the exit code.

Transport is one 9 Mb/s MPEG-TS program: video PIDs 256/257 and private JSON PID
258. UDP packets contain at most seven whole 188-byte TS packets. The single mux
owner bounds interleaving and paces byte output; socket errors never block capture.
The standard FFmpeg mux's 500 ms delay adds **one common 1,000,000 µs media PTS
offset**. Session metadata declares `transport_pts_offset_us`; receivers subtract
it from both videos. Local MKV and JSONL retain the original shared timeline.
Metadata PES uses producer queue-admission time because asynchronous A/B records cannot
share monotonically ordered capture DTS on one PID; the JSON contains the exact
capture `pts_us`. The timestamp is assigned under the producer-order lock and
retained through pacing; consumer wall time would feed queue delay back into CBR
null padding. Health includes transport queue age, wire bytes and last submitted
video timestamps. Session/geometry metadata repeats once per second for late joins.

The shared origin uses CLOCK_BOOTTIME per libcamera's SensorTimestamp contract,
with a bracketed CLOCK_MONOTONIC sample recorded too. FRAMOS PiSP forwards the CFE
buffer timestamp; this is **not independent proof of exposure timing or of the
kernel clock domain**. The service rejects missing, regressing or implausible
timestamps. Verify the CFE clock on the target, prohibit suspend during a run,
and measure optical synchronization separately. Both sensors initially free-run;
no unverified software or hardware synchronization is silently enabled.

Portable tests (no camera libraries required):

```sh
cmake -S software/flight -B build/flight-portable -DPV_BUILD_CAPTURE=OFF
cmake --build build/flight-portable
ctest --test-dir build/flight-portable --output-on-failure
```

With FFmpeg/JSON development packages, `-DPV_BUILD_MEDIA_TESTS=ON` adds
`pv-media-test input-h264.mp4 new-output-directory [storage-floor|transport-failure|cbr-burst]`.
Use the existing simulator's `docs/assets/video/cil212-camera-a.mp4` fixture.
This runs the actual recorder/TS/UDP implementations against prerecorded H264;
it checks PID/frame counts, private JSON, PCR byte clock, video decode deadlines,
PCR continuity after A stops, IDR segment boundaries and sink failure isolation.
The `cbr-burst` case delivers three-frame encoder bursts and checks that metadata
PES timestamps retain producer admission time while the real UDP consumer paces
the backlog. Replacing this with consumer wall time makes the regression fail.
It does not test camera acquisition, x264 throughput, optics or hardware sync.

Remaining target gates: Linux link/build, DMA layout/caches on the CM5, exact
selected mode/transform/color/crop, capture/encode throughput, long-run bounded
latency and thermal/power behavior, device timestamp domain, physical exposure
synchronization, storage interruption recovery and real calibration.
