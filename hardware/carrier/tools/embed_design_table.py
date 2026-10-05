#!/usr/bin/env python3
"""Embed a rendered Design Master PNG without changing electrical objects."""
from pathlib import Path
import argparse
import base64
import hashlib
import struct
from edit_camera_buses import blocks, kind, patch, uid


def embedded(source, png, key, x, y, width):
    data = png.read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    pixels = struct.unpack('>I', data[16:20])[0]
    # These workbook-rendered PNGs have no pHYs metadata. KiCad renders them
    # at its default 72 dpi; the exported schematic verifies physical size.
    dpi = 72.0
    offset = 8
    while offset < len(data):
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        if data[offset + 4:offset + 8] == b'pHYs':
            per_meter_x, _, unit = struct.unpack('>IIB', data[offset + 8:offset + 17])
            if unit == 1:
                dpi = per_meter_x * .0254
        offset += length + 12
    scale = width / (pixels * 25.4 / dpi)
    encoded = base64.b64encode(data).decode()
    chunks = '\n'.join('  ' + encoded[i:i + 76] for i in range(0, len(encoded), 76))
    identifier = uid(key)
    block = f'(image (at {x:g} {y:g}) (scale {scale:.10f}) (uuid "{identifier}") (data\n{chunks}\n))'
    edits = [(s, e, block) for s, e, b in blocks(source) if kind(b) == 'image' and identifier in b]
    return patch(source, edits, [] if edits else [block])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('schematic', type=Path)
    ap.add_argument('png', type=Path)
    ap.add_argument('--key', required=True)
    ap.add_argument('--x', type=float, required=True)
    ap.add_argument('--y', type=float, required=True)
    ap.add_argument('--width', type=float, required=True)
    args = ap.parse_args()
    source = args.schematic.read_text()
    changed = embedded(source, args.png, args.key, args.x, args.y, args.width)
    assert args.schematic.read_text() == source, 'Concurrent schematic edit'
    backup = args.png.parent / 'backups' / (hashlib.sha256(source.encode()).hexdigest()[:12] + '-' + args.schematic.name)
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        backup.write_text(source)
    args.schematic.write_text(changed)
    print(f'Embedded {args.png.name} in {args.schematic.name}; backup {backup}')


if __name__ == '__main__':
    main()
