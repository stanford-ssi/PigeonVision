# CM5 → Pico SPI

Baseline: native `pv-capture` → 20 MHz SPI → Pico PIO receiver. The Pico owns
its output clock and inserts null packets below capacity. RF remains untested.

## Camera output

Add these settings to the camera config:

```json
{
  "capture_allocator": "dma_heap_cached",
  "encoder_input": "dmabuf",
  "encoder_threads": 3,
  "udp_destination": null,
  "mux_bitrate": 9000000,
  "spi": {"device": "/dev/spidev0.0", "hz": 20000000, "ready_line": 25}
}
```

Start a fresh Pico `pvtx` run first, then `pv-capture --config capture.json`.
SPI0 must be enabled (`dtparam=spi=on`) and the user needs SPI/GPIO access.
Native output uses Linux spidev and GPIO v2, with no Python SPI packages.
Omit `spi` to use UDP; the two outputs are mutually exclusive.
`spi.gpiochip` can select the RP1 device explicitly; otherwise it is discovered.

The bounded transport queue keeps SPI from blocking capture or recording.
READY low for two seconds stops transport. Failed or short writes are not
retried. Compare final `outputs.transport.spi` counts and CRC with the Pico
report. SPI has no acknowledgment.

## Bench result

Two IMX900s at 2064×1552, 4 Mb/s each, simultaneous recording, with a fan:

| 120-second run | Measured |
| --- | ---: |
| Encoded rate A / B | 29.99 / 29.99 fps |
| MPEG-TS rate | 9.0006 Mb/s |
| Average CPU, all four cores | 61.4% |
| Maximum temperature | 56.75°C |
| Accepted SPI messages | 102,801 |
| Matching CRC chain | `0x0f20927e` |

No transport loss, Pico protocol errors or underruns were observed. Each camera
had one startup error and one 66.7 ms interval after warm-up. The optimized
Python bridge used 78.8% CPU and delivered 29.78/29.80 fps in its 60-second run.
These are bench measurements with airflow, not passive-cooling or RF results.

[Raw results and reproduction commands](https://github.com/Rivercraft911/RP2350_IQ_Benchmark/tree/main/results/cm5-spi).

## Wiring

3.3 V signals. Physical pins refer to the CM5 IO-board and Pico Plus 2 headers.
The final carrier assignment is separate.

| Signal | CM5 GPIO / physical pin | Pico GP / physical pin | Direction |
| --- | --- | --- | --- |
| SCK | GPIO11 / 23 | GP17 / 22 | CM5 → Pico |
| MOSI | GPIO10 / 19 | GP18 / 24 | CM5 → Pico |
| CS_N | GPIO8 / 24 | GP19 / 25 | CM5 → Pico |
| READY | GPIO25 / 22 | GP22 / 29 | Pico → CM5 |
| GND | 20 or 25 | 23 or 28 | Common ground |

MISO is unused. Fit a 4.7 kΩ pull-up to Pico 3.3 V on CS_N and a 4.7–6.8 kΩ
pull-down to GND on READY. Power Pico before CM5 drives SPI. Keep bench jumpers
short, with ground beside SCK, and check pin ownership against camera overlays.
Never enable Pico SPI self-test while the CM5 is wired to these pins.

## PV-SPI v1

Mode 0, MSB first, 8-bit words. One **1332-byte transfer per CS_N assertion**.
CS_N stays high ≥10 µs before checking READY for the next transfer. READY must
be high before every transfer. All multibyte fields are little-endian.

| Offset | Bytes | Field |
| --- | --- | --- |
| 0 | 2 | Magic `0x5650`, wire bytes `50 56` |
| 2 | 1 | Version 1 |
| 3 | 1 | Type 1: TS_DATA; type 0: empty NOP |
| 4 | 2 | Sequence, modulo 65536 |
| 6 | 2 | Payload length: 188 × n, n=1…7; NOP length 0 |
| 8 | 4 | Reserved, zero |
| 12 | 1316 | TS packets beginning with `0x47`, then zero padding |
| 1328 | 4 | IEEE reflected CRC-32 over bytes 0…1327 |

CRC init/final XOR: `0xFFFFFFFF`; `crc32("123456789") = 0xCBF43926`.
`pv spi --vector` produces CRC `0x51be328d` without hardware. File tails,
malformed UDP and invalid TS sync are errors. USB starts/configures the Pico.
Use a fresh receiver run for each sender, which starts at sequence zero.

The Pico report must match sent messages, TS packets, bytes and the CRC chain,
with zero protocol errors or underruns. Let its output drain before its command
expires. Check physical timing on the final wiring and repeat with RF operating.

[Protocol specification](https://github.com/Rivercraft911/RP2350_IQ_Benchmark/blob/main/docs/pv-spi-spec.md).

## Why SPI

At 9 Mb/s TS, framing needs `9 × 1332 / 1316 = 9.11 Mb/s` on the wire.
At 20 MHz that is **46.4% bus time**, including minimum CS gaps, before Linux
scheduling delays. This is not a measured maximum throughput. The current
8 Msym/s QPSK 2/3 pipeline accepts about **10.33 Mb/s TS**.

RS-485 is useful for a longer, noisy cable. With UART 8N1, the same data needs
at least `9.11 × 10/8 = 11.39 Mbaud`, plus flow control. Fast transceivers exist,
but this needs a different Pico receiver. Pico USB is 12 Mb/s full-speed before
overhead. Neither offers a clear advantage for the nearby transmitter board.

Sources: [TI RS-485 guide](https://www.ti.com/lit/an/slla272d/slla272d.pdf),
[RP2350 datasheet, UART §12.1.3.1 and USB §12.7.1.1.1](https://datasheets.raspberrypi.com/rp2350/rp2350-datasheet.pdf).

## Python test sender

Keep `pv spi` for patterns, files and comparisons. From `software/`:

```sh
uv sync --locked --extra spi
. .venv/bin/activate
pv spi --pattern --count 10000 --hz 1e6 --summary output/pattern.json
```

Start the Pico first, and test increasing clocks before 20 MHz. The Python
sender defaults to 1 MHz and a one-second READY timeout. For a live comparison,
use UDP output in the capture config, omit `spi`, then start the bridge before
capture:

```sh
sudo /usr/sbin/sysctl -w net.core.rmem_max=4194304
pv spi --udp 127.0.0.1:1234 --hz 20e6 --transfer tx-only --udp-mode direct \
  --ready-timeout 2 --idle-timeout 15 --duration 130 --summary output/bridge.json
```

Give the bridge time to drain after capture stops. Check its final status,
unsent data and UDP drops as well as the Pico counters. `--mirror-udp IP:PORT`
adds a preview copy, but does not prove Pico acceptance and was disabled in
the comparison runs.
