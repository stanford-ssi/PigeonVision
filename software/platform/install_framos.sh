#!/usr/bin/env bash
# Explicit, target-only installation; pv doctor never invokes this script.
set -euo pipefail
platform_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
expected_kernel=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["os"]["kernel"])' "$platform_dir/versions.json")
if [[ $(uname -m) != aarch64 || $(uname -r) != "$expected_kernel" ]]; then
    printf 'Expected aarch64 kernel %s; found %s %s. Install the pinned OS first.\n' "$expected_kernel" "$(uname -m)" "$(uname -r)" >&2
    exit 1
fi
if [[ ! -r /lib/modules/$(uname -r)/build/Makefile ]]; then
    echo 'Matching kernel headers are missing. Do not replace the kernel with a newer header package.' >&2
    exit 1
fi
if [[ $EUID == 0 ]]; then
    echo 'Run as the bench user with sudo access; builds should not run as root.' >&2
    exit 1
fi
build_root=${PV_VENDOR_BUILD_DIR:-"$HOME/pigeonvision-vendor"}
mkdir -p "$build_root"
log_file="$build_root/install-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$log_file") 2>&1
jobs=${PV_BUILD_JOBS:-2}
[[ $jobs =~ ^[1-9][0-9]*$ ]] || { echo 'PV_BUILD_JOBS must be a positive integer' >&2; exit 1; }

# Preserve the kernel/firmware ABI; apt update is allowed here, apt upgrade is not.
mapfile -t kernel_packages < <(dpkg-query -W -f='${db:Status-Status}\t${binary:Package}\n' 'linux-image*' 'linux-headers*' raspi-firmware rpi-eeprom 2>/dev/null | awk '$1 == "installed" {print $2}')
(( ${#kernel_packages[@]} > 0 )) || { echo 'Could not identify installed kernel packages' >&2; exit 1; }
sudo apt-mark hold "${kernel_packages[@]}"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    git build-essential cmake pkg-config meson ninja-build device-tree-compiler kmod \
    python3-yaml python3-ply python3-jinja2 python3-venv python3-pip \
    libboost-dev libboost-program-options-dev libgnutls28-dev libssl-dev openssl \
    libtiff-dev libjpeg-dev libpng-dev libexif-dev libdrm-dev libepoxy-dev libyaml-dev libudev-dev \
    libavcodec-dev libavformat-dev libavdevice-dev libavutil-dev libswresample-dev \
    nlohmann-json3-dev v4l-utils i2c-tools

checkout() {
    local component=$1 destination=$2 repository revision
    repository=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]]["repository"])' "$platform_dir/versions.json" "$component")
    revision=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]]["commit"])' "$platform_dir/versions.json" "$component")
    if [[ ! -d $destination/.git ]]; then
        git clone "$repository" "$destination"
    fi
    [[ -z $(git -C "$destination" status --porcelain --untracked-files=no) ]] || { echo "Vendor source has local edits: $destination" >&2; return 1; }
    git -C "$destination" fetch origin "$revision"
    git -C "$destination" checkout --detach "$revision"
    [[ $(git -C "$destination" rev-parse HEAD) == "$revision" ]]
}
checkout drivers "$build_root/framos-rpi-drivers"
checkout libcamera "$build_root/framos-libcamera"
checkout rpicam_apps "$build_root/rpicam-apps"

make -C "$build_root/framos-rpi-drivers" NPROC="$jobs" modules dtbs
sudo make -C "$build_root/framos-rpi-drivers" NPROC="$jobs" modules_install dtbs_install
sudo /sbin/depmod -a "$(uname -r)"
test -f /boot/firmware/overlays/fr_imx900.dtbo
module_path=$(/sbin/modinfo -n fr_imx900)
[[ $module_path == /lib/modules/"$(uname -r)"/* || $module_path == /usr/lib/modules/"$(uname -r)"/* ]] || { echo "Driver installed for an unexpected kernel: $module_path" >&2; exit 1; }

# Keep the vendor's PiSP pipeline; omit desktop previews on the Lite bench image.
meson setup --reconfigure "$build_root/framos-libcamera/build" "$build_root/framos-libcamera" \
    --buildtype=release -Dpipelines=rpi/vc4,rpi/pisp -Dipas=rpi/vc4,rpi/pisp \
    -Dv4l2=true -Dgstreamer=disabled -Dtest=false -Dlc-compliance=disabled \
    -Dcam=disabled -Dqcam=disabled -Ddocumentation=disabled -Dpycamera=disabled
ninja -C "$build_root/framos-libcamera/build" -j "$jobs"
sudo ninja -C "$build_root/framos-libcamera/build" install
sudo ldconfig

# Use the explicitly built libcamera ahead of distro copies during compilation.
export PKG_CONFIG_PATH="/usr/local/lib/aarch64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
meson setup --reconfigure "$build_root/rpicam-apps/build" "$build_root/rpicam-apps" \
    -Denable_libav=enabled -Denable_drm=disabled -Denable_egl=disabled \
    -Denable_qt=disabled -Denable_opencv=disabled -Denable_tflite=disabled -Denable_hailo=disabled
meson compile -C "$build_root/rpicam-apps/build" -j "$jobs"
sudo meson install -C "$build_root/rpicam-apps/build"
sudo ldconfig
sudo python3 "$platform_dir/configure_cameras.py" /boot/firmware/config.txt
uname -a
/sbin/modinfo fr_imx900 | head -n 12
/usr/local/bin/rpicam-hello --version
echo "Installation finished. Reboot explicitly, verify camera-1 IO-board jumpers, then run camera_smoke.sh. Log: $log_file"
