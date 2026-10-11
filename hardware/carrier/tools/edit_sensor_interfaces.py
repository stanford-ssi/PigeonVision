#!/usr/bin/env python3
"""Finish the reviewed polling sensor interface; preserve user component layout.

Default prepares review copies. --apply backs up and edits active CAD.
Close KiCad first. Manufacturer pin-mode evidence is in Design Master.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import re
from edit_camera_buses import CAD, PROJECT, blocks, kind, patch, uid, line, object_id
from kicad_sexpr import Atom, parse, nodes, one, walk, dump

OUTPUT = PROJECT / 'build/sensors-style-2026-10-02'
SIGNALS = ['SENSOR_I2C_SCL', 'SENSOR_I2C_SDA']


def position(block):
    return tuple(float(v) for v in one(parse(block), 'at')[1:3])


def label(name, x, y, key, hierarchical=False, angle=0):
    typ = 'hierarchical_label' if hierarchical else 'label'
    shape = '(shape bidirectional)' if hierarchical else ''
    justify = 'right' if hierarchical and angle == 180 else 'left' if hierarchical else 'left bottom'
    return f'({typ} {json.dumps(name)} {shape} (at {x:g} {y:g} {angle}) (effects (font (size 1.27 1.27)) (justify {justify})) (uuid "{uid(key)}"))'


def entry(x, y, dx, dy, key):
    return f'(bus_entry (at {x:g} {y:g}) (size {dx:g} {dy:g}) (stroke (width 0) (type default)) (uuid "{uid(key)}"))'


def power(template, x, y, angle, reference, key):
    symbol = copy.deepcopy(parse(template))
    old = one(symbol, 'at')
    dx, dy = x - float(old[1]), y - float(old[2])
    for item in walk(symbol):
        if item[0] == 'at':
            item[1:3] = [Atom(f'{float(item[1]) + dx:g}'), Atom(f'{float(item[2]) + dy:g}')]
        elif item[0] == 'uuid':
            item[1] = uid(key + '-' + item[1])
        elif item[0] == 'reference':
            item[1] = reference
    one(symbol, 'at')[3] = Atom(str(angle))
    for p in nodes(symbol, 'property'):
        if p[1] == 'Reference':
            p[2] = reference
        elif p[1] == 'Value':
            if angle == 0 and p[2] == 'GND':
                p[:] = [z for z in p if not (isinstance(z, list) and z[0] == 'hide')]
            a = one(p, 'at')
            a[1:4] = [Atom(f'{x - 3.81:g}' if angle in (90, 270) else f'{x:g}'),
                       Atom(f'{y:g}' if angle in (90, 270) else f'{y - 3.81 if p[2] != "GND" else y + 3.81:g}'), Atom('90' if angle in (90, 270) else '0')]
            effects = one(p, 'effects')
            effects[:] = [z for z in effects if not (isinstance(z, list) and z[0] == 'justify')]
            if angle in (90, 270):
                effects.append(parse('(justify right)'))
    return dump(symbol)


def templates(source):
    syms = [b for _, _, b in blocks(source) if kind(b) == 'symbol']
    supply = next(b for b in syms if '(lib_id "power:+3V3")' in b)
    ground = next(b for b in syms if '(lib_id "PCM_SparkFun-PowerSymbol:GND")' in b)
    return supply, ground


def sensors(source):
    if uid('sensor-bmi-bus') in source:
        return source
    edits, added = [], []
    supply, ground = templates(source)
    flag_lib = next(x for x in nodes(parse(Path('/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/power.kicad_sym').read_text()), 'symbol') if x[1] == 'PWR_FLAG')
    flag_lib[1] = 'power:PWR_FLAG'
    source = add_cache(source, [flag_lib])
    supply, ground = templates(source)
    power_count = 0
    irq_y = {77.47, 82.55, 87.63, 92.71}
    for start, end, block in blocks(source):
        typ = kind(block)
        if typ == 'global_label':
            name = parse(block)[1]
            x, y = position(block)
            if name in ('SENSOR_SCL', 'SENSOR_SDA'):
                trunk = 35.56 if x < 100 else 185.42
                clear = SIGNALS[0 if name.endswith('SCL') else 1]
                edits.append((start, end, label(clear, trunk + 5.08, y, clear + '-label-' + str(x))))
                added.append(entry(trunk, y - 2.54, 2.54, 2.54, clear + '-entry-' + str(x)))
            elif name in ('CM5_3V3', 'GND'):
                power_count += 1
                template = supply if name == 'CM5_3V3' else ground
                angle = 0 if name == 'CM5_3V3' or x > 70 else 270
                if name == 'CM5_3V3':
                    added.append(line('wire', 50.8, y, x, y, 'sensor-mode-supply-' + str(power_count)))
                    x = 50.8
                edits.append((start, end, power(template, x, y, angle, f'#PWR09{power_count:02}', 'sensor-power-' + str(power_count))))
            else:
                raise ValueError('Unreviewed sensor global label: ' + name)
        elif typ == 'label' and parse(block)[1].startswith(('IMU_INT', 'BMP_INT', 'BMP_VDDIO')):
            # Interrupts are unused. BMP CSB is wired directly to filtered VDDIO.
            edits.append((start, end, ''))
        elif typ == 'wire':
            pts = [tuple(float(v) for v in p[1:]) for p in nodes(one(parse(block), 'pts'), 'xy')]
            if pts in [[(88.9, y), (93.98, y)] for y in irq_y]:
                edits.append((start, end, ''))
            elif pts == [(240.03, 90.17), (243.84, 90.17)]:
                edits.append((start, end, ''))
            elif pts[0][1] == pts[1][1] and pts[0][0] in (63.5, 218.44) and pts[0][1] in ((82.55, 85.09) if pts[0][0] == 63.5 else (85.09, 87.63)):
                trunk = 35.56 if pts[0][0] == 63.5 else 185.42
                edits.append((start, end, line('wire', trunk + 2.54, pts[0][1], pts[0][0], pts[0][1], object_id(block))))
        elif typ == 'junction' and position(block) == (240.03, 90.17):
            # R28 and INT share one continuous wire endpoint, no branch remains.
            edits.append((start, end, ''))
        elif typ == 'text' and parse(block)[1].startswith(('BMP581: 0x46', 'BMI088: accelerometer', '10R limit')):
            edits.append((start, end, ''))
    for y in sorted(irq_y):
        added.append(f'(no_connect (at 88.9 {y:g}) (uuid "{uid("sensor-irq-nc-" + str(y))}"))')
    added += [line('bus', 34.29, 74.93, 35.56, 74.93, 'sensor-bmi-header'),
              line('bus', 35.56, 74.93, 35.56, 85.09, 'sensor-bmi-bus'),
              label('{SENSOR_I2C}', 34.29, 74.93, 'sensor-interface', True, 180),
              line('bus', 185.42, 77.47, 185.42, 87.63, 'sensor-bmp-bus'),
              label('{SENSOR_I2C}', 185.42, 77.47, 'sensor-bmp-bus-label'),
              line('wire', 243.84, 85.09, 243.84, 78.74, 'bmp-csb-vertical'),
              line('wire', 243.84, 78.74, 231.14, 78.74, 'bmp-csb-horizontal'),
              line('wire', 231.14, 78.74, 231.14, 76.2, 'bmp-csb-supply'),
              line('wire', 226.06, 76.2, 226.06, 78.74, 'bmp-vdd-flag-vertical'),
              line('wire', 203.2, 78.74, 226.06, 78.74, 'bmp-vdd-flag-horizontal'),
              line('wire', 243.84, 78.74, 251.46, 78.74, 'bmp-vddio-flag-horizontal'),
              label('BMP_VDD', 207.01, 78.74, 'bmp-vdd-label'),
              label('BMP_VDDIO', 243.84, 85.09, 'bmp-vddio-label'),
              f'(junction (at 243.84 78.74) (diameter 0) (color 0 0 0 0) (uuid "{uid("bmp-vddio-flag-junction")}"))',
              f'(text "Settings / calculations: Design Master, Power budget A91:F103" (at 35.56 116.84 0) (effects (font (size 1.27 1.27)) (justify left top)) (uuid "{uid("sensor-master-reference")}"))']
    for x, ref, key in [(203.2, '#FLG0901', 'bmp-vdd-flag'), (251.46, '#FLG0902', 'bmp-vddio-flag')]:
        flag = parse(power(supply, x, 78.74, 0, ref, key))
        one(flag, 'lib_id')[1] = 'power:PWR_FLAG'
        value = next(p for p in nodes(flag, 'property') if p[1] == 'Value')
        value[2] = 'PWR_FLAG'
        value.append(parse('(hide yes)'))
        added.append(dump(flag))
    assert power_count == 5
    return patch(source, edits, added)


def add_cache(source, cache):
    root = parse(source)
    lib = one(root, 'lib_symbols')
    names = {x[1] for x in nodes(lib, 'symbol')}
    missing = [x for x in cache if x[1] not in names]
    if not missing:
        return source
    span = next((s, e, b) for s, e, b in blocks(source) if kind(b) == 'lib_symbols')
    content = span[2][:-1] + '\n' + '\n'.join(dump(x) for x in missing) + '\n)'
    return patch(source, [(span[0], span[1], content)], [])


def cm5(source, supply, cache):
    if uid('cm5-sensor-bus') in source:
        return source
    source = add_cache(source, cache)
    added = []
    for name, y in [(SIGNALS[0], 102.87), (SIGNALS[1], 105.41)]:
        added += [line('wire', 30.48, y, 58.42, y, name + '-cm5-wire'),
                  entry(27.94, y - 2.54, 2.54, 2.54, name + '-cm5-entry'),
                  label(name, 33.02, y, name + '-cm5-label')]
    added += [line('bus', 26.67, 97.79, 27.94, 97.79, 'cm5-sensor-header'),
              line('bus', 27.94, 97.79, 27.94, 105.41, 'cm5-sensor-bus'),
              label('{SENSOR_I2C}', 26.67, 97.79, 'cm5-sensor-port', True, 180)]
    for y, pin in [(130.81, 78), (138.43, 84), (140.97, 86)]:
        added.append(line('wire', 45.72, y, 58.42, y, 'cm5-3v3-pin-' + str(pin)))
    added += [line('wire', 45.72, 128.27, 45.72, 130.81, 'cm5-3v3-vref-top'),
              line('wire', 45.72, 130.81, 45.72, 138.43, 'cm5-3v3-vref'),
              line('wire', 45.72, 138.43, 45.72, 140.97, 'cm5-3v3-pins'),
              power(supply, 45.72, 128.27, 0, '#PWR0906', 'cm5-3v3-power')]
    for y in (130.81, 138.43):
        added.append(f'(junction (at 45.72 {y:g}) (diameter 0) (color 0 0 0 0) (uuid "{uid("cm5-3v3-junction-" + str(y))}"))')
    return patch(source, [], added)


def regulators(source):
    edits = []
    for start, end, block in blocks(source):
        if kind(block) == 'hierarchical_label':
            old = parse(block)[1]
            clear = {'SCL_0': SIGNALS[0], 'SDA_0': SIGNALS[1]}.get(old)
            if clear:
                edits.append((start, end, block.replace(json.dumps(old), json.dumps(clear), 1)))
        elif kind(block) == 'symbol' and '(lib_id "power:+3V3")' in block and position(block) == (158.75, 31.75):
            edits.append((start, end, block.replace('(property "Value" "+3V3"', '(property "Value" "3V3_CM5"', 1)))
    return patch(source, edits, [])


def sheet_pin(name, x, y, angle, key):
    justify = 'right' if angle in (0, 90) else 'left'
    return f'(pin {json.dumps(name)} bidirectional (at {x:g} {y:g} {angle}) (uuid "{uid(key)}") (effects (font (size 1.27 1.27)) (justify {justify})))'


def parent(source):
    if uid('parent-sensor-bus') in source:
        return source
    edits = []
    for start, end, block in blocks(source):
        if kind(block) != 'sheet':
            continue
        if '(property "Sheetfile" "Sensors.kicad_sch"' in block:
            pin = sheet_pin('{SENSOR_I2C}', 149.225, 64.77, 270, 'sensor-parent-pin')
            block = block[:-1] + '\n' + pin + '\n)'
        elif '(property "Sheetfile" "CM5.kicad_sch"' in block:
            pin = sheet_pin('{SENSOR_I2C}', 149.225, 85.09, 90, 'cm5-sensor-parent-pin')
            block = block[:-1] + '\n' + pin + '\n)'
        elif '(property "Sheetfile" "Regulators.kicad_sch"' in block:
            block = block.replace('(pin "SCL_0"', '(pin "SENSOR_I2C_SCL"').replace('(pin "SDA_0"', '(pin "SENSOR_I2C_SDA"')
        else:
            continue
        edits.append((start, end, block))
    added = [line('bus', 149.225, 64.77, 149.225, 73.66, 'parent-sensor-bus'),
             line('bus', 149.225, 73.66, 149.225, 85.09, 'parent-sensor-bus-cm5'),
             line('bus', 109.22, 73.66, 149.225, 73.66, 'parent-sensor-bus-ina'),
             line('bus', 109.22, 73.66, 109.22, 92.71, 'parent-ina-trunk'),
             f'(junction (at 149.225 73.66) (diameter 0) (color 0 0 0 0) (uuid "{uid("parent-sensor-bus-junction")}"))']
    for name, y in [(SIGNALS[1], 92.71), (SIGNALS[0], 95.25)]:
        added += [line('wire', 89.535, y, 106.68, y, name + '-parent-wire'),
                  entry(106.68, y, 2.54, -2.54, name + '-parent-entry'),
                  label(name, 90.805, y, name + '-parent-label')]
    return patch(source, edits, added)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    files = ['Sensors.kicad_sch', 'CM5.kicad_sch', 'Regulators.kicad_sch', 'PigeonCarrier.kicad_sch', 'PigeonCarrier.kicad_pro']
    original = {name: (CAD / name).read_text() for name in files}
    supply, _ = templates(original[files[0]])
    cache = [x for x in one(parse(original[files[0]]), 'lib_symbols')[1:] if isinstance(x, list) and x[0] == 'symbol' and x[1] == 'power:+3V3']
    settings = json.loads(original[files[4]])
    settings['schematic'].setdefault('bus_aliases', {})['SENSOR_I2C'] = SIGNALS
    changed = dict(zip(files, [sensors(original[files[0]]), cm5(original[files[1]], supply, cache), regulators(original[files[2]]), parent(original[files[3]]), json.dumps(settings, indent=2, ensure_ascii=False) + '\n']))
    digest = hashlib.sha256(''.join(original.values()).encode()).hexdigest()[:12]
    dest = OUTPUT / ('backups/' + digest if args.apply else 'proposed')
    dest.mkdir(parents=True, exist_ok=True)
    for name in files:
        assert (CAD / name).read_text() == original[name], 'Concurrent edit: ' + name
        if args.apply:
            if not (dest / name).exists():
                (dest / name).write_text(original[name])
            if changed[name] != original[name]:
                (CAD / name).write_text(changed[name])
        else:
            (dest / name).write_text(changed[name])
    print(json.dumps({'applied': args.apply, 'backup_or_proposal': str(dest), 'files': files}, indent=2))


if __name__ == '__main__':
    main()
