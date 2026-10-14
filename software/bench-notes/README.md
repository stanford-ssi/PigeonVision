# Bench results

## Cameras

CM5, two IMX900s, 2064×1552 at 30 fps, 4 Mb/s H.264 each. Tests below used a fan.

| Two-minute run | Result |
| --- | --- |
| Native PV-SPI | 29.99 fps per camera; 9.0006 Mb/s TS; Pico counts and CRC matched |
| CPU / temperature | 61.4% / 56.75°C |
| Frame gaps | One startup error and one 66.7 ms interval per camera |

Full-sensor capture avoids the older square crop. Cached DMA buffers improved
encoding throughput. [Saved camera settings](../calibration/bench-2026-10-02/)
include Camera A's 180° rotation and the accepted indoor colour correction.
Measured lens fits use nominal opposed-camera alignment. Flight colour,
exposure synchronization, seam accuracy and one-hour operation remain unverified.

## Flight startup

Two minutes with real cameras, recording and simulated carrier/FC telemetry over
UDP. This test did not use SPI or RF.

| Final build | Result |
| --- | --- |
| Video / sensor samples | 7,201 frames / 28,206 samples delivered |
| Transport | No drops, continuity errors or late video packets |
| CPU / temperature | 62.9% / 63.9°C; no throttling |

A 500 ms queue expiry discarded valid bursts. The final 1 s limit and smaller
frame metadata avoided those drops; maximum queue age was 433 ms. One startup
camera error per camera remains. Physical sensors and autonomous RP2350 startup
still need testing. [SPI setup](../flight/spi.md).

## E200 receiver

M4 Pro, Pluto/IIO firmware, **12 MS/s receive**, **8 Msymbol/s QPSK 2/3**, pilots,
roll-off 0.20. This carries the existing 9 Mb/s transport target.

| Check | Result |
| --- | --- |
| Three-second file | 20,565 / 20,624 exact TS packets |
| Paced sample stream | 20,108 / 20,624 exact packets; every input sample delivered; zero FEC errors |
| Two-minute RX noise | 12.000 MS/s; no reported source error or observed overflow |
| Synthetic video through modem → UDP → viewer | A/B video and telemetry decoded; all 2,749,124 recovered TS bytes recorded unchanged |

Missing packets were confined to the test boundaries. Synchronization and
16-frame decoding batches leave edge data unfinished when the waveform stops.
The fixture also includes one incompletely encoded final input packet. No gaps
occurred inside the recovered packet sequence.

The video fixture recovered 14,623 / 15,106 packets with zero FEC errors. Losing
the initial A keyframe delayed A until its next keyframe: 38 A frames and 67 B
frames decoded. This verifies the software handoff, not complete clip recovery
or a physical radio link.

The same path preserved complete BMI088 accel/gyro batches, BMP581, INA226 and
flight-controller status/events. All test values, units, timestamps and error
flags matched at the viewer WebSocket. Real sensor timing and attitude
stabilization remain unverified.

TX stayed disabled. Launcher-only shutdown stopped both processes in 0.13 s.
Known-signal RF reception and continuous sample integrity remain unverified;
the kernel can clear overflow flags between polls.

[Receiver setup](../python/pigeonvision/ground/README.md#radio-input).
Detailed records stay in ignored local `output/` storage.
