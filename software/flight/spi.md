# CM5 → RP2350: PV-SPI v1

`pv spi` sends native MPEG-TS to the Pico, which owns its output clock and inserts
null packets below capacity. Two-camera bench runs reached 9 Mb/s over 20 MHz
SPI with matching CM5/Pico counts and CRC chains. RF remains untested.

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
using one `xfer2` call; a spidev buffer smaller than 1332 bytes is rejected.
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

For a 60-second capture, start the Pico for 80 seconds and the bridge for 70:

```sh
sudo /usr/sbin/sysctl -w net.core.rmem_max=4194304
pv spi --udp 127.0.0.1:1234 --hz 20e6 --idle-timeout 15 --duration 70 \
  --summary output/spi/live-20mhz.json &
spi_pid=$!
sudo renice -n -5 -p "$spi_pid"
pv capture --config capture.json --binary /path/to/current/pv-capture
wait "$spi_pid"
```

The higher sender scheduling priority prevented the queue overflow seen with
three encoder threads per camera at normal priority. It is a bench setting,
not a sustained-30-fps guarantee. Allow the bridge to drain after capture ends.

Add `--mirror-udp MAC_IP:1234` for a raw TS copy to the viewer. This adds load;
it was not enabled in the SPI comparison runs. Mirroring happens after each SPI
write, so backpressure delays preview and the preview does not prove acceptance.
Only the bridge binds the local input.

## Measured limits

Both IMX900s used 2064×1552, a 30 fps request, x264 ultrafast at 4 Mb/s each,
9 Mb/s total MPEG-TS, and simultaneous local recording. Cached buffers and
three encoder threads delivered 28.4–28.7 fps with SPI. The 90-second local
UDP control delivered 30.00 fps per camera with no steady-state drops.

The ten-minute SPI attempt stopped at 80°C after about 210 seconds on the passive
CM5 heatsink. This does not establish a ten-minute or flight-qualified system.
Cooling and combined camera/SPI scheduling need further work.

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
