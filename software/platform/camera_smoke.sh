#!/usr/bin/env bash
set -euo pipefail
session_dir=${1:?Usage: camera_smoke.sh OUTPUT_DIRECTORY}
mkdir -p "$session_dir"
if [[ -e "$session_dir/cameras.txt" ]]; then
    echo 'Use a new output directory; existing camera evidence will not be overwritten.' >&2
    exit 1
fi
/usr/local/bin/rpicam-hello --list-cameras 2>&1 | tee "$session_dir/cameras.txt"
ldd /usr/local/bin/rpicam-still > "$session_dir/rpicam-libraries.txt"
uname -a > "$session_dir/kernel.txt"
vcgencmd get_throttled > "$session_dir/throttled.txt"
for camera in 0 1; do
    /usr/local/bin/rpicam-still --camera "$camera" --nopreview --vflip --timeout 3000 \
        --mode 2064:1552:10 --width 2064 --height 1552 \
        --metadata "$session_dir/camera-$camera.json" --metadata-format json \
        --output "$session_dir/camera-$camera.jpg" 2> "$session_dir/camera-$camera.log"
done
pids=()
for camera in 0 1; do
    /usr/local/bin/rpicam-still --camera "$camera" --nopreview --vflip --timeout 3000 \
        --mode 2064:1552:10 --width 2064 --height 1552 \
        --metadata "$session_dir/dual-$camera.json" --metadata-format json \
        --output "$session_dir/dual-$camera.jpg" 2> "$session_dir/dual-$camera.log" &
    pids+=("$!")
done
status=0
for pid in "${pids[@]}"; do wait "$pid" || status=1; done
exit "$status"
