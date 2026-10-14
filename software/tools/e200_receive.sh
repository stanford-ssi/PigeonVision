#!/usr/bin/env bash
# Select the radio Python environment; process ownership lives in the supervisor.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_python=$(cd -- "$script_dir/../python" && pwd)
export PYTHONPATH="$project_python${PYTHONPATH:+:$PYTHONPATH}"
exec "${E200_PYTHON:-python3}" -m pigeonvision.radio.receive "$@"
