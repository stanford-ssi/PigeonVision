#!/usr/bin/env bash
# gr-dvbs2rx 130c31576cfeebb1a5842b24ccaf4a561b6a9990 + TSDuck.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: bash software/tools/e200_receive.sh FREQUENCY_HZ [--dry-run]

Receive DVB-S2 QPSK 2/3, normal frames, pilots, 8 Msymbol/s, roll-off 0.20.
Requires E200 Pluto/IIO firmware, dvbs2-rx and TSDuck's tsp on PATH.

Environment:
  E200_URI    IIO address (default ip:192.168.1.10)
  RX_GAIN_DB  Manual receive gain (default 0; tune on the bench)
  TS_DEST     Raw TS UDP destination (default 127.0.0.1:5000)

--dry-run prints commands without accessing the radio.
EOF
}

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    usage
    exit 0
fi
if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && $2 != --dry-run ) ]]; then
    usage >&2
    exit 2
fi
if [[ ! $1 =~ ^[1-9][0-9]*$ ]]; then
    echo 'Frequency must be a positive integer in Hz.' >&2
    exit 2
fi

receiver=(dvbs2-rx
    --source plutosdr --plutosdr-addr "${E200_URI:-ip:192.168.1.10}"
    --freq "$1" --sym-rate 8e6 --samp-rate 16e6
    --modcod QPSK2/3 --frame-size normal --pilots on
    --rolloff 0.20 --gold-code 0 --multistream off
    --plutosdr-gain-mode manual --plutosdr-gain "${RX_GAIN_DB:-0}"
    --sink fd --out-fd 3 --log)
# Avoid TSDuck's half-buffer preload and large input batches on a live pipe.
forwarder=(tsp --realtime --initial-input-packets 7
    --max-input-packets 7 --max-flushed-packets 7
    -I file - -O ip "${TS_DEST:-127.0.0.1:5000}"
    --packet-burst 7 --enforce-burst)

if [[ ${2:-} == --dry-run ]]; then
    printf '%q ' "${receiver[@]}"
    printf '3>&1 1>&2 | '
    printf '%q ' "${forwarder[@]}"
    printf '\n'
    exit 0
fi
for program in dvbs2-rx tsp; do
    if ! command -v "$program" >/dev/null 2>&1; then
        echo "Missing $program. See the ground receiver README, Radio input." >&2
        exit 127
    fi
done

# Keep ordinary stdout/diagnostics out of binary TS on fd 3.
"${receiver[@]}" 3>&1 1>&2 | "${forwarder[@]}"
