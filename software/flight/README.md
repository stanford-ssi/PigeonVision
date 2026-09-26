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

Each camera has eight requested libcamera buffers and a three-frame queue.
Mapped DMA buffers are held by reference-counted AVFrames until x264 releases
them; the completion callback does no encoding or disk/network IO. Each encoder
uses two slice threads, no B frames, a one-second GOP, repeated SPS/PPS, configured
bitrate/VBV and the selected preset. Dropped frames keep real timestamp gaps.

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
Metadata PES uses mux-admission time because asynchronous A/B records cannot
share monotonically ordered capture DTS on one PID; the JSON contains the exact
capture `pts_us`. Session/geometry metadata repeats once per second for late joins.

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
`pv-media-test input-h264.mp4 new-output-directory [storage-floor|transport-failure]`.
Use the existing simulator's `docs/assets/video/cil212-camera-a.mp4` fixture.
This runs the actual recorder/TS/UDP implementations against prerecorded H264;
it checks PID/frame counts, private JSON, PCR byte clock, video decode deadlines,
PCR continuity after A stops, IDR segment boundaries and sink failure isolation.
It does not test camera acquisition, x264 throughput, optics or hardware sync.

Remaining target gates: Linux link/build, DMA layout/caches on the CM5, exact
selected mode/transform/color/crop, capture/encode throughput, long-run bounded
latency and thermal/power behavior, device timestamp domain, physical exposure
synchronization, storage interruption recovery and real calibration.
