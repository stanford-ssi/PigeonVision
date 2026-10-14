#!/usr/bin/env bash
# Build native TSDuck in the existing isolated E200 dependency environment.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work="$root/output/e200-native"
prefix="$work/env"
mamba="$work/bootstrap/bin/micromamba"
version=3.45-4798
src="$work/tsduck-$version"
[[ $(uname -s) == Darwin && $(uname -m) == arm64 && -x $mamba ]] || {
    echo 'Run setup_e200_native.sh first on native Apple Silicon macOS.' >&2
    exit 2
}
[[ -x $prefix/bin/make && -x $prefix/bin/sed ]] || {
    echo 'Install the checked-in E200 dependency lock with setup_e200_native.sh first.' >&2
    exit 2
}
if [[ ! -d $src ]]; then
    curl -fL "https://github.com/tsduck/tsduck/archive/refs/tags/v$version.tar.gz" \
        -o "$work/tsduck.tar.gz"
    printf '%s  %s\n' a35845430fff1385cf1cda9645bbfd0ec887ed440137fc6c26863c624c24eb63 \
        "$work/tsduck.tar.gz" | shasum -a 256 -c -
    tar -xzf "$work/tsduck.tar.gz" -C "$work"
fi
# Keep HTTP/TLS enabled: this release's disabled-feature stubs fail to compile.
# Radio hardware plugins and optional SRT/RIST are unnecessary for file -> UDP.
options=(NODEKTEC=1 NODTAPI=1 NOHIDES=1 NOVATEK=1 NOPCSC=1 NOSRT=1 NORIST=1
    NOJAVA=1 NOPYTHON=1 NOEDITLINE=1 NOHWACCEL=1 NOGITHUB=1 NOTEST=1 NODOC=1
    "SYSPREFIX=$prefix" STRIP=/usr/bin/true)
"$mamba" run -p "$prefix" make -C "$src" -j 6 "${options[@]}"
"$mamba" run -p "$prefix" make -C "$src" "${options[@]}" install
"$prefix/bin/tsp" --version
"$prefix/bin/tsp" --list=output | /usr/bin/grep -E '^[[:space:]]*ip:'
