#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install -d /etc/pigeonvision
install -m 0644 "$base/pv-flight.service" /etc/systemd/system/pv-flight.service
# Preserve deployed pin/device mappings. Installation neither enables nor starts capture.
if [ ! -e /etc/pigeonvision/flight.json ]; then
  install -m 0644 "$base/flight.json" /etc/pigeonvision/flight.json
fi
systemctl daemon-reload
