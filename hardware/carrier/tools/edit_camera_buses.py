#!/usr/bin/env python3
"""Apply the camera bus style without rewriting unrelated KiCad objects.

Default: produce review copies. --apply: back up and update the active files.
Run with the schematic editor closed. Existing generated buses are left alone.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import uuid

PROJECT = Path(__file__).resolve().parents[3]
CAD = PROJECT / 'hardware/carrier/PigeonCarrier'
OUTPUT = PROJECT / 'build/camera-bus-style-2026-10-02'
NAMESPACE = uuid.UUID('e86b0432-df34-426b-916d-791639e31bf9')
RENAMES = {'SCL0': 'CAM0_I2C_SCL', 'SDA0': 'CAM0_I2C_SDA',
           'ID_SC': 'CAM1_I2C_SCL', 'ID_SD': 'CAM1_I2C_SDA',
           'CAM_GPIO0':'CAM0_RST', 'CAM_GPIO1':'CAM1_RST'}
PIN_SIGNALS = {2: 'D0_N', 3: 'D0_P', 5: 'D1_N', 6: 'D1_P',
               8: 'CLK_N', 9: 'CLK_P', 11: 'D2_N', 12: 'D2_P',
               14: 'D3_N', 15: 'D3_P'}


def uid(name):
    return str(uuid.uuid5(NAMESPACE, name))


def blocks(source):
    """Return the original byte spans of top-level S-expression objects."""
    depth = 0
    quoted = escaped = False
    start = None
    for i, char in enumerate(source):
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == '(':
            if depth == 1:
                start = i
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 1:
                yield start, i + 1, source[start:i + 1]
    assert depth == 0 and not quoted


def kind(block):
    return re.match(r'\(([^\s()]+)', block)[1]


def position(block):
    match = re.search(r'\(at\s+([-\d.]+)\s+([-\d.]+)', block)
    return tuple(float(x) for x in match.groups())


def object_id(block):
    return re.search(r'\(uuid\s+"([^"]+)"', block)[1]


def patch(source, replacements, additions):
    for start, end, content in reversed(replacements):
        source = source[:start] + content + source[end:]
    end = source.rfind(')')
    return source[:end] + ''.join('\n\t' + x + '\n' for x in additions) + source[end:]


def line(typ, x1, y1, x2, y2, name):
    return f'({typ} (pts (xy {x1:g} {y1:g}) (xy {x2:g} {y2:g})) (stroke (width 0) (type default)) (uuid "{uid(name)}"))'


def edit_cameras(source):
    if uid('CAM0-trunk') in source:
        return source
    edits, added = [], []
    total = [0, 0]
    for start, end, block in blocks(source):
        if kind(block) == 'global_label':
            label = json.loads(re.match(r'\(global_label\s+("(?:\\.|[^"\\])*")', block)[1])
            clear = RENAMES.get(label, label)
            x, y = position(block)
            cam = 0 if abs(x - 71.12) < .001 else 1 if abs(x - 208.28) < .001 else None
            signal = clear.startswith(('MIPI0_', 'MIPI1_', 'CAM0_I2C_', 'CAM1_I2C_')) or clear in ('CAM0_RST', 'CAM1_RST')
            if cam is not None and signal:
                trunk = 33.02 + 137.16 * cam
                edits.append((start, end, f'(label {json.dumps(clear)} (at {trunk + 5.08:g} {y:g} 0) (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "{object_id(block)}"))'))
                added.append(f'(bus_entry (at {trunk:g} {y - 2.54:g}) (size 2.54 2.54) (stroke (width 0) (type default)) (uuid "{uid(clear + "-entry")}"))')
                total[cam] += 1
            elif clear != label:
                edits.append((start, end, block.replace(json.dumps(label), json.dumps(clear), 1)))
        elif kind(block) == 'wire':
            points = re.findall(r'\(xy\s+([-\d.]+)\s+([-\d.]+)\)', block)
            if len(points) != 2:
                continue
            (x1, y1), (x2, y2) = [tuple(map(float, p)) for p in points]
            if abs(y1 - y2) < .001 and (x1, x2) in [(71.12, 76.2), (208.28, 213.36)]:
                # Leave ground, CAM_3V3, and bypass wiring unchanged.
                if round(y1, 2) in [45.72, 48.26, 53.34, 55.88, 60.96, 63.5,
                                    68.58, 71.12, 76.2, 78.74, 83.82, 91.44, 93.98]:
                    trunk = 33.02 if x1 < 100 else 170.18
                    changed = re.sub(r'\(xy\s+[-\d.]+\s+[-\d.]+\)', f'(xy {trunk + 2.54:g} {y1:g})', block, count=1)
                    edits.append((start, end, changed))
        elif kind(block) == 'text':
            if 'Pin 18 reserved; sensor uses onboard clock.' in block:
                x, _ = position(block)
                left = 33.02 if x < 100 else 170.18
                changed = re.sub(r'\(at\s+[-\d.]+\s+[-\d.]+\s+0\)', f'(at {left:g} 113.03 0)', block, count=1)
                edits.append((start, end, changed))
            elif 'SCL0 / SDA0 has CM5 internal 1.8k pull-ups.' in block:
                changed = block.replace('SCL0 / SDA0 has CM5 internal 1.8k pull-ups.', 'CAM0 I2C: CM5 internal 1.8k pull-ups.')
                changed = re.sub(r'\(at\s+[-\d.]+\s+[-\d.]+\s+0\)', '(at 49.53 128.016 0)', changed, count=1)
                edits.append((start, end, changed))
    assert total == [13, 13], f'Expected 13 connector signals per camera; found {total}'
    for cam in range(2):
        x = 33.02 + 137.16 * cam
        added += [line('bus', x - 1.27, 36.83, x, 36.83, f'CAM{cam}-header'),
                  line('bus', x, 36.83, x, 95.25, f'CAM{cam}-trunk'),
                  f'(hierarchical_label "{{CAM{cam}}}" (shape bidirectional) (at {x - 1.27:g} 36.83 180) (effects (font (size 1.27 1.27)) (justify right)) (uuid "{uid(f"CAM{cam}-hierarchical")}"))']
    return patch(source, edits, added)


def edit_parent(source):
    edits = []
    for start, end, block in blocks(source):
        if kind(block) == 'sheet' and '(property "Sheetfile" "Cameras.kicad_sch"' in block:
            x, y = position(block)
            pins = []
            for cam, offset in [(0, 5.08), (1, 12.7)]:
                if f'(pin "{{CAM{cam}}}"' not in block:
                    pins.append(f'(pin "{{CAM{cam}}}" bidirectional (at {x:g} {y + offset:g} 180) (uuid "{uid(f"CAM{cam}-parent-pin")}") (effects (font (size 1.27 1.27)) (justify left)))')
            if pins:
                at = block.rfind(')')
                edits.append((start, end, block[:at] + '\n\t' + '\n\t'.join(pins) + '\n' + block[at:]))
    return patch(source, edits, [])


def aliases(settings):
    target = settings['schematic'].setdefault('bus_aliases', {})
    for cam in range(2):
        target[f'CAM{cam}'] = [f'MIPI{cam}_{name}' for name in PIN_SIGNALS.values()] + [f'CAM{cam}_I2C_SCL', f'CAM{cam}_I2C_SDA', f'CAM{cam}_RST']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    files = ['Cameras.kicad_sch', 'PigeonCarrier.kicad_sch', 'PigeonCarrier.kicad_pro']
    original = {name: (CAD / name).read_text() for name in files}
    settings = json.loads(original[files[2]])
    aliases(settings)
    updated = {files[0]: edit_cameras(original[files[0]]), files[1]: edit_parent(original[files[1]]), files[2]: json.dumps(settings, indent=2, ensure_ascii=False) + '\n'}
    digest = hashlib.sha256(''.join(original.values()).encode()).hexdigest()[:12]
    dest = OUTPUT / ('backups/' + digest if args.apply else 'proposed')
    dest.mkdir(parents=True, exist_ok=True)
    for name in files:
        if args.apply:
            assert (CAD / name).read_text() == original[name], f'Concurrent edit of {name}; refusing overwrite'
            if not (dest / name).exists():
                (dest / name).write_text(original[name])
            if updated[name] != original[name]:
                (CAD / name).write_text(updated[name])
        else:
            (dest / name).write_text(updated[name])
    print(json.dumps({'applied': args.apply, 'backup_or_proposal': str(dest), 'files': files}, indent=2))


if __name__ == '__main__':
    main()
