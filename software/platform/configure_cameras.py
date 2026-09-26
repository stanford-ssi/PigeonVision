#!/usr/bin/env python3
"""Idempotently configure two IMX900s on the *official CM5 IO board*."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re

START = '# BEGIN PIGEONVISION CM5IO CAMERAS'
END = '# END PIGEONVISION CM5IO CAMERAS'
BLOCK = f'''{START}
[all]
camera_auto_detect=0
dtparam=i2c_arm=on
dtoverlay=fr_imx900,cam0
dtoverlay=fr_imx900,cam1-compute
{END}
'''


def render(original: str) -> str:
    if original.count(START) != original.count(END) or original.count(START) > 1:
        raise ValueError('Malformed PigeonVision managed camera block')
    match = re.search(r'^' + re.escape(START) + r'\n.*?^' + re.escape(END) + r'(?:\n|$)', original, flags=re.S | re.M)
    if START in original and match is None:
        raise ValueError('Malformed PigeonVision managed camera block')

    def supersede(text: str) -> str:
        lines = []
        for line in text.splitlines(keepends=True):
            active = line.strip()
            if re.match(r'dtoverlay=fr_imx900(?:,|$)', active) or re.match(r'camera_auto_detect\s*=', active):
                line = '# superseded by PigeonVision: ' + line
            lines.append(line)
        return ''.join(lines)

    if match:
        # Replacing in place preserves the [all] scope of settings following
        # the block; removing/reappending it would move them into a prior filter.
        return supersede(original[:match.start()]) + BLOCK + supersede(original[match.end():])
    return supersede(original).rstrip() + '\n\n' + BLOCK


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    original = args.config.read_text()
    updated = render(original)
    if args.dry_run:
        print(updated, end='')
    elif updated != original:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        args.config.with_name(args.config.name + '.pigeonvision-' + stamp + '.bak').write_text(original)
        args.config.write_text(updated)


if __name__ == '__main__':
    main()
