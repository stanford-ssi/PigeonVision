#!/usr/bin/env python3
"""Apply known receiver patches without resetting or replacing local files."""
from pathlib import Path
import subprocess
import sys

PLATFORM = Path(__file__).resolve().parents[1] / 'platform'
OLD_CALL = "+            buffer_samples=self.plutosdr['buffer_size'])\n"
NEW_CALL = ("+            buffer_samples=self.plutosdr['buffer_size'],\n"
            "+            frequency=int(self.freq))\n")


def git_apply(checkout, patch, *options):
    return subprocess.run(['git', '-C', str(checkout), 'apply', *options, '-'],
                          input=patch, text=True, capture_output=True)


def apply_patches(checkout):
    # These two patches touch different files. Validate the whole plan before
    # changing either file, including when one patch has already been applied.
    plan = []
    for name in ('gr-dvbs2rx-arm64.patch', 'gr-dvbs2rx-iio-prefetch.patch'):
        patch = (PLATFORM / name).read_text()
        if git_apply(checkout, patch, '--check').returncode == 0:
            plan.append(patch)
        elif git_apply(checkout, patch, '--reverse', '--check').returncode == 0:
            continue
        elif name == 'gr-dvbs2rx-iio-prefetch.patch' and patch.count(NEW_CALL) == 1:
            # Accept only the known predecessor's full set of hunks. Recount
            # because removing the added frequency changes one hunk's size.
            previous = patch.replace(NEW_CALL, OLD_CALL)
            upgrade = (PLATFORM / 'gr-dvbs2rx-frequency-upgrade.patch').read_text()
            if (git_apply(checkout, previous, '--recount', '--reverse', '--check').returncode == 0
                    and git_apply(checkout, upgrade, '--check').returncode == 0):
                plan.append(upgrade)
                continue
            raise RuntimeError(f'{name} conflicts with local changes; checkout preserved')
        else:
            raise RuntimeError(f'{name} conflicts with local changes; checkout preserved')
    for patch in plan:
        result = git_apply(checkout, patch)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: e200_apply_receiver_patches.py RECEIVER_CHECKOUT')
    try:
        apply_patches(Path(sys.argv[1]))
    except (OSError, RuntimeError) as exc:
        raise SystemExit(str(exc))
