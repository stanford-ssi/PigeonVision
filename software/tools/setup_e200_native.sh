#!/usr/bin/env bash
# Native Apple Silicon RX dependencies. Does not discover or open a radio.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work="$root/output/e200-native"
prefix="$work/env"
revision=130c31576cfeebb1a5842b24ccaf4a561b6a9990
lock="$root/software/platform/e200-osx-arm64.lock"
if [[ $(uname -s) != Darwin || $(uname -m) != arm64 ]]; then
    echo 'This setup targets native Apple Silicon macOS.' >&2
    exit 2
fi
mkdir -p "$work/bootstrap"
mamba="$work/bootstrap/bin/micromamba"
if [[ ! -x $mamba ]]; then
    curl -fL https://conda.anaconda.org/conda-forge/osx-arm64/micromamba-2.9.0-0.tar.bz2 \
        -o "$work/bootstrap/micromamba.tar.bz2"
    printf '%s  %s\n' 500f5074feb8d02c4296ef9921c3650ed2874171805a9fbb8fbb53896433646b \
        "$work/bootstrap/micromamba.tar.bz2" | shasum -a 256 -c -
    tar -xjf "$work/bootstrap/micromamba.tar.bz2" -C "$work/bootstrap" bin/micromamba
fi
action=create
[[ ! -d $prefix/conda-meta ]] || action=install
"$mamba" "$action" -y -r "$work/mamba" -p "$prefix" --file "$lock"
if [[ ! -d $work/gr-dvbs2rx/.git ]]; then
    git clone https://github.com/igorauad/gr-dvbs2rx.git "$work/gr-dvbs2rx"
fi
if [[ $(git -C "$work/gr-dvbs2rx" rev-parse HEAD) != "$revision" ]]; then
    if [[ -n $(git -C "$work/gr-dvbs2rx" status --porcelain) ]]; then
        echo 'Receiver checkout has local changes; preserve them before changing revisions.' >&2
        exit 2
    fi
    git -C "$work/gr-dvbs2rx" checkout --detach "$revision"
fi
git -C "$work/gr-dvbs2rx" submodule update --init --recursive
"$prefix/bin/python" "$root/software/tools/e200_apply_receiver_patches.py" "$work/gr-dvbs2rx"
# CMake 4 needs the compatibility floor for the older cpu_features submodule.
# Keep upstream DEBUG_LOGS=ON: OFF has a GR_LOG_DEBUG macro compilation error.
"$mamba" run -p "$prefix" cmake -S "$work/gr-dvbs2rx" -B "$work/build" -G Ninja \
    -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_PREFIX_PATH="$prefix" \
    -DPYTHON_EXECUTABLE="$prefix/bin/python" -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DENABLE_DOXYGEN=OFF -DENABLE_MANPAGES=OFF -DDEBUG_LOGS=ON
"$mamba" run -p "$prefix" cmake --build "$work/build" -j 6
"$mamba" run -p "$prefix" cmake --install "$work/build"
bash "$root/software/tools/setup_tsduck_native.sh"
"$mamba" env export -p "$prefix" --explicit > "$work/conda-osx-arm64-explicit.txt"
"$prefix/bin/dvbs2-rx" --help > "$work/receiver-help.txt"
printf 'Receiver ready. Add to this shell: export PATH="%s/bin:$PATH"\n' "$prefix"
