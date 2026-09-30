# CM5 → RP2350: PV-SPI v1

`pv-capture` can send MPEG-TS directly to the Pico from its transport worker.
The Pico owns its output clock and inserts null packets below capacity. RF remains untested.

## Native camera output

Use the existing camera config with these settings:

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
SPI uses Linux spidev and GPIO v2 directly; no Python SPI packages are needed for
this path. Omit `spi` to retain UDP output. The two transports are mutually exclusive.
`spi.gpiochip` optionally selects the RP1 device; otherwise discovery resolves device aliases.

The existing bounded transport queue isolates SPI from capture and recording.
READY low for two seconds stops transport. A failed or short write is never retried.
The health/session-end `outputs.transport.spi` record contains counts, pending bytes
and CRC chain. Compare the final record with the Pico report; SPI has no acknowledgment.

With a fan, two 2064×1552 cameras, 4 Mb/s each, simultaneous recording and 20 MHz
SPI, a 120-second run delivered **29.99 fps per camera**, 9.0006 Mb/s TS, and
102,801 accepted messages with CRC chain `0x0f20927e`. CPU averaged 61.4% across
four cores; maximum temperature was 56.75°C. Each camera had one startup error
and one 66.7 ms interval after warm-up. No steady application drops, transport
loss, Pico protocol errors or underruns were observed. This is a two-minute
bench result, not a thermal or RF qualification.

The optimized Python bridge reached 29.78/29.80 fps and 78.8% CPU in its 60-second
comparison. Prefer native SPI for camera tests; keep `pv spi` for file/pattern tests.

## Install

Run from `software/`. Full package:

```sh
uv sync --locked --extra spi
. .venv/bin/activate
```

SPI-only CM5 environment:

```sh
python3 -m venv .venv-spi
.venv-spi/bin/python -m pip install 'spidev==3.8' 'gpiod==2.5.0'
PYTHONPATH="$PWD/python" .venv-spi/bin/python -m pigeonvision.spi_transport --help
```

Replace `pv spi` below with
`PYTHONPATH="$PWD/python" .venv-spi/bin/python -m pigeonvision.spi_transport`.
The Linux-only extra pins [spidev 3.8](https://pypi.org/project/spidev/3.8/) and
[gpiod 2.5.0](https://pypi.org/project/gpiod/2.5.0/) (official v2). Spidev needs a
C compiler and matching Python development headers. Base `pv view` supports macOS.

SPI0 must be enabled (`dtparam=spi=on`), `/dev/spidev0.0` must exist, and the
user needs SPI/GPIO access. Default READY is line 25 on the single RP1-labelled
chip; missing/ambiguous discovery fails. `--gpiochip /dev/gpiochipN` overrides
discovery; check its mapping because numbering can change with the kernel.

## Bench wiring

3.3 V CMOS; physical pins refer to the CM5 IO-board 40-pin header and Pico Plus 2
bench headers. The final carrier assignment is separate.

| Signal | CM5 GPIO / physical pin | Pico GP / physical pin | Direction |
| --- | --- | --- | --- |
| SCK | GPIO11 / 23 | GP17 / 22 | CM5 → Pico |
| MOSI | GPIO10 / 19 | GP18 / 24 | CM5 → Pico |
| CS_N | GPIO8 (CE0) / 24 | GP19 / 25 | CM5 → Pico |
| READY | GPIO25 / 22 | GP22 / 29 | Pico → CM5 |
| GND | 20 or 25 | 23 or 28 | Common ground |
| MISO, unused in v1 | GPIO9 / 21 | GP20 / 26 | Pico does not drive it |

Fit a 4.7 kΩ pull-up to Pico 3.3 V on CS_N and a 4.7–6.8 kΩ pull-down to GND
on READY. These connect to the rails, not in series with the signal wires. Power
Pico before CM5 drives SPI to avoid backfeeding. At 20 MHz keep jumpers ≤15 cm
with ground beside SCK. Recheck pin ownership against active camera overlays.

## Protocol

Mode 0 (CPOL=0, CPHA=0), MSB first, 8-bit words. Default bring-up clock is
**1 MHz**; service target is **20 MHz**. Exactly **1332 bytes per CS_N assertion**,
using one SPI write (native or `--transfer tx-only`) or one `xfer2` call; a spidev buffer smaller than 1332 bytes is rejected.
CS_N stays high ≥10 µs before sampling READY for the next transfer. READY must
be high immediately before every transfer; its default timeout is 1 s.

All multibyte fields are little-endian:

| Offset | Bytes | Field |
| --- | --- | --- |
| 0 | 2 | Magic 0x5650 (wire bytes `50 56`) |
| 2 | 1 | Version 1 |
| 3 | 1 | Type 1: TS_DATA (type 0: empty NOP) |
| 4 | 2 | Sequence, incrementing modulo 65536 |
| 6 | 2 | Payload length: 188 × n, n=1…7; NOP length 0 |
| 8 | 4 | Reserved, zero |
| 12 | 1316 | TS packets, each starting 0x47, then zero padding |
| 1328 | 4 | IEEE reflected CRC-32 over bytes 0…1327 (`zlib.crc32`) |

CRC init/final XOR are 0xFFFFFFFF; `crc32("123456789") = 0xCBF43926`.
`pv spi --vector` works without hardware: sequence 1, PID 0x100, continuity 0,
payload bytes 0…183; CRC field `8d 32 be 51` (0x51be328d).
File tails, malformed UDP and invalid sync are errors, never silently truncated.
MISO has no acknowledgement; USB configures the Pico transmitter in v1.

## Bring-up and live feed

Start a **fresh** transmitter over USB for each run, from the separate benchmark
firmware checkout:

```sh
python3 host/iqbench.py pvtx --ms 900000
```

Output defaults to 8 Msym/s; `--cpw 4`, `8` or `16` selects 4, 2 or 1 Msym/s.
READY stays low until `pvtx` starts. Each sender starts at sequence zero;
reused receiver counters invalidate gap/chain comparisons.

```sh
mkdir -p output/spi
pv spi --pattern --count 10000 --hz 1e6 --summary output/spi/pattern-1mhz.json
# Repeat at 5, 10, 16 and 20 MHz, with fresh firmware and evidence filenames.
pv spi --pattern --duration 60 --hz 20e6 --summary output/spi/pattern-20mhz.json
pv spi --file capture.ts --hz 20e6 --summary output/spi/file-20mhz.json
```

Count/duration stop at the first limit; a pattern without either defaults to
10000 messages. Files drain at READY capacity without saved-PCR pacing. Keep firmware
running longer than each sender case.

For live capture use a freshly built `pv-capture`, `capture_allocator:
"dma_heap_cached"`, `encoder_input: "dmabuf"`, `encoder_threads: 3`,
`udp_destination: "127.0.0.1:1234"`, and `mux_bitrate: 9000000`.
Verify these values in the resulting `session.json`. An older executable silently
ignored the allocator and thread settings during initial bring-up.

For a Python bridge comparison, start the Pico first and use:

```sh
sudo /usr/sbin/sysctl -w net.core.rmem_max=4194304
pv spi --udp 127.0.0.1:1234 --hz 20e6 --transfer tx-only --udp-mode direct \
  --idle-timeout 15 --duration 70 --summary output/spi/live-20mhz.json &
spi_pid=$!
pv capture --config capture.json --binary /path/to/current/pv-capture
wait "$spi_pid"
```

Here capture uses UDP and no `spi` object. Allow the bridge to drain after capture
ends. Direct UDP removes the receiver thread and software queue; kernel overflow
and unread datagrams remain checked. Defaults retain the older threaded/duplex
path for comparison. Raising sender priority is not needed for the direct path.

Add `--mirror-udp MAC_IP:1234` for a raw TS copy to the viewer. This adds load;
it was not enabled in the SPI comparison runs. Mirroring happens after each SPI
write, so backpressure delays preview and the preview does not prove acceptance.
Only the bridge binds the local input.

## Earlier measurements

The original Python bridge delivered 28.4–28.7 fps. Its passive-heatsink run
stopped at 80°C after 210 seconds. The fan and native sender address different
limits; native results above do not establish passive cooling performance.

Raw logs, chart-ready CSVs, exact Pico firmware and the test harnesses are in
[RP2350_IQ_Benchmark/results/cm5-spi](https://github.com/Rivercraft911/RP2350_IQ_Benchmark/tree/main/results/cm5-spi).

## Evidence and rates

JSON prints on exit; `--summary` creates a new file. Check status/error, sent
messages/bytes/chain, READY waits, unsent data, UDP drops and mirror counts.
UDP buffers 256 messages and requests 4 MiB; Linux `SO_RCVBUF` reports twice
the accepted request, including overhead. Require `receive_buffer_below_spec`
false (≥2 MiB reported) and check TS continuity. Buffering cannot throttle capture. Ctrl-C/timeouts
report remaining data; default UDP idle timeout is 2 s.

At 9 Mb/s, `9e6 / (1316 × 8) = 855 full messages/s`; required SPI line rate is
`9e6 × 1332 / 1316 = 9.11 Mb/s`. Thus 1 MHz cannot carry the live feed.
The reference 8 Msym/s QPSK 2/3 output accepts about 10.33 Mb/s TS (981 full
messages/s); its 24-message queue represents about 25 ms at capacity.

Require zero firmware header/CRC/sync/short/long/overflow/sequence errors and
matching accepted count/chain. The chain accumulates TS_DATA CRC fields (not
NOP); normalize host hex and firmware decimal `pv.crc_chain` to integers.
Stop sender before firmware expiry; allow ≥100 ms drain at 8 Msym/s, longer
at lower rates, then preserve the USB report. Check CS/gap/READY on a scope or
logic analyzer. Follow staged clocks with ten minutes of real TS and cadence/loss checks.

Provenance: sibling `RP2350_IQ_Benchmark`, commit
`86c8a6e7542c5eea4bad4d43594989b728c17b48`, `docs/pv-spi-spec.md` and
`host/cm5/pv_spi_tx.py`, reviewed 2026-09-30. Protocol/vector details are included here.
