#!/usr/bin/env python3
"""Add the CM5IO fan circuit to CM5, preserving existing sheet objects.

Default prepares a complete review copy. --apply requires that proposal and
unchanged source hashes; it backs up CM5 and changes only that active sheet.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import math
import shutil

from edit_camera_buses import CAD, PROJECT, blocks, kind, patch, uid, line
from edit_sensor_interfaces import add_cache, label
from edit_cm5_interfaces import endpoints
from kicad_sexpr import Atom, parse, nodes, one, dump

OUT = PROJECT / 'build/fan-implementation-2026-10-04'
MARKER = uid('cm5-fan-J8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def symbol_library():
    result = {}
    for filename in ('CM5.kicad_sch', 'Service_USB.kicad_sch'):
        root = parse((CAD / filename).read_text())
        result.update({s[1]: copy.deepcopy(s) for s in nodes(one(root, 'lib_symbols'), 'symbol')})
    local = parse((PROJECT / 'hardware/libraries/PigeonVision.kicad_sym').read_text())
    s = copy.deepcopy(next(s for s in nodes(local, 'symbol') if s[1] == 'CM5_Fan_BM04B_SRSS_TB'))
    s[1] = 'PigeonVision:' + s[1]
    result[s[1]] = s
    cap_library = Path.home() / 'Documents/KiCad/10.0/3rdparty/symbols/com_github_CDFER_JLCPCB-Kicad-Library/JLCPCB-Capacitors.kicad_sym'
    s = copy.deepcopy(next(s for s in nodes(parse(cap_library.read_text()), 'symbol') if s[1] == '0805,1uF'))
    s[1] = 'PCM_JLCPCB-Capacitors:' + s[1]
    result[s[1]] = s
    return result


class Fan:
    def __init__(self, source):
        root = parse(source)
        j1 = next(s for s in nodes(root, 'symbol') if any(p[1:3] == ['Reference', 'J1'] for p in nodes(s, 'property')))
        project = one(one(j1, 'instances'), 'project')
        self.project_name = project[1]
        self.instance_path = one(project, 'path')[1]
        self.library = symbol_library()
        self.cache, self.objects = {}, []

    def part(self, libid, ref, x, y, angle=0, value=None, fields=None):
        lib = copy.deepcopy(self.library[libid])
        self.cache[libid] = lib
        obj = parse(f'(symbol (lib_id {json.dumps(libid)}) (at {x:g} {y:g} {angle}) (unit 1) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (uuid "{uid("cm5-fan-" + ref)}"))')
        power = ref.startswith('#')
        for original in nodes(lib, 'property'):
            if original[1].startswith('ki_'):
                continue
            p = copy.deepcopy(original)
            p[:] = [v for v in p if not (isinstance(v, list) and v[0] in ('hide', 'do_not_autoplace', 'show_name'))]
            a = one(p, 'at')
            a[1:4] = [Atom(f'{x:g}'), Atom(f'{y:g}'), Atom('0')]
            eff = one(p, 'effects')
            eff[:] = [v for v in eff if not (isinstance(v, list) and v[0] in ('hide', 'justify'))]
            if p[1] == 'Reference':
                p[2] = ref
            elif p[1] == 'Value' and value is not None:
                p[2] = value
            if fields and p[1] in fields:
                fx, fy, fa = fields[p[1]]
                a[1:4] = [Atom(f'{fx:g}'), Atom(f'{fy:g}'), Atom(str(fa))]
                if libid.startswith('PCM_JLCPCB-Capacitors:'):
                    eff.append(parse('(justify left)'))
            else:
                p.append(parse('(hide yes)'))
            if power and p[1] == 'Reference':
                p.append(parse('(hide yes)'))
            obj.append(p)
        rad = math.radians(angle)
        positions = {}
        for sub in nodes(lib, 'symbol'):
            for pin in nodes(sub, 'pin'):
                num = one(pin, 'number')[1]
                px, py = map(float, one(pin, 'at')[1:3])
                positions[num] = (round(x + px * math.cos(rad) - py * math.sin(rad), 5),
                                  round(y - px * math.sin(rad) - py * math.cos(rad), 5))
                obj.append(parse(f'(pin {json.dumps(num)} (uuid "{uid("cm5-fan-pin-" + ref + "-" + num)}"))'))
        obj.append(parse(f'(instances (project {json.dumps(self.project_name)} (path {json.dumps(self.instance_path)} (reference {json.dumps(ref)}) (unit 1))))'))
        self.objects.append(dump(obj))
        return positions

    def wire(self, x1, y1, x2, y2, key):
        assert (x1, y1) != (x2, y2)
        self.objects.append(line('wire', x1, y1, x2, y2, 'cm5-fan-' + key))

    def local(self, name, x, y, key):
        self.objects.append(label(name, x, y, 'cm5-fan-label-' + key))

    def power(self, name, ref, x, y, angle=0):
        libid = {'5V_MAIN': 'power:+5V', '3V3_CM5': 'power:+3V3', 'GND': 'PCM_SparkFun-PowerSymbol:GND'}[name]
        fields = {'Value': (x, y - 3.81, 0)} if name != 'GND' else None
        self.part(libid, ref, x, y, angle, name, fields)

    def junction(self, x, y, key):
        self.objects.append(f'(junction (at {x:g} {y:g}) (diameter 0) (color 0 0 0 0) (uuid "{uid("cm5-fan-junction-" + key)}"))')


def edit(source):
    if MARKER in source:
        return source
    all_refs = []
    for file in CAD.glob('*.kicad_sch'):
        for symbol in nodes(parse(file.read_text()), 'symbol'):
            all_refs.extend(p[2] for p in nodes(symbol, 'property') if p[1] == 'Reference')
    reserved = {'J8', 'R36', 'C63', '#PWR1401', '#PWR1402', '#PWR1403', '#PWR1404'}
    assert not (reserved & set(all_refs)), reserved & set(all_refs)
    assert not ({'FAN_PWM', 'FAN_TACH'} & {s[1] for s in nodes(parse(source), 'label')})
    native = endpoints(source)
    assert native[('J1', 16)] == (58.42, 52.07, 'FAN_TACHO')
    assert native[('J1', 19)] == (119.38, 57.15, 'FAN_PWM')
    d = Fan(source)
    header = d.part('PigeonVision:CM5_Fan_BM04B_SRSS_TB', 'J8', 154.94, 185.42,
                    fields={'Reference': (154.94, 175.26, 0), 'Value': (154.94, 177.8, 0)})
    assert header == {'1': (144.78, 180.34), '2': (144.78, 182.88), '3': (144.78, 185.42), '4': (144.78, 187.96)}
    cap = d.part('PCM_JLCPCB-Capacitors:0805,1uF', 'C63', 128.27, 184.15,
                 fields={'Reference': (129.54, 182.88, 0), 'Value': (130.81, 185.42, 0)})
    assert cap == {'1': (128.27, 180.34), '2': (128.27, 187.96)}
    d.wire(128.27, 180.34, 144.78, 180.34, 'header-5v')
    d.wire(128.27, 180.34, 128.27, 177.8, '5v-symbol')
    d.junction(128.27, 180.34, '5v')
    d.power('5V_MAIN', '#PWR1401', 128.27, 177.8)
    d.power('GND', '#PWR1402', 128.27, 187.96)
    d.wire(144.78, 185.42, 139.7, 185.42, 'header-ground')
    d.power('GND', '#PWR1403', 139.7, 185.42, 270)
    d.wire(134.62, 182.88, 144.78, 182.88, 'header-pwm')
    d.local('FAN_PWM', 134.62, 182.88, 'header-pwm')
    d.wire(134.62, 187.96, 144.78, 187.96, 'header-tach')
    d.local('FAN_TACH', 134.62, 187.96, 'header-tach')
    resistor = d.part('PCM_JLCPCB-Resistors:0402,10kΩ', 'R36', 105.41, 182.88, 90,
                      fields={'Reference': (105.41, 179.07, 90), 'Value': (105.41, 182.88, 90)})
    assert resistor == {'1': (101.6, 182.88), '2': (109.22, 182.88)}
    d.wire(101.6, 182.88, 97.79, 182.88, 'pullup-3v3')
    d.wire(97.79, 182.88, 97.79, 180.34, '3v3-symbol')
    d.power('3V3_CM5', '#PWR1404', 97.79, 180.34)
    d.wire(109.22, 182.88, 111.76, 182.88, 'pullup-pwm')
    d.local('FAN_PWM', 111.76, 182.88, 'pullup-pwm')
    d.wire(46.99, 52.07, 58.42, 52.07, 'native-tach')
    d.local('FAN_TACH', 46.99, 52.07, 'native-tach')
    d.wire(119.38, 57.15, 132.08, 57.15, 'native-pwm')
    d.local('FAN_PWM', 132.08, 57.15, 'native-pwm')
    return patch(add_cache(source, list(d.cache.values())), [], d.objects)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    proposal = OUT / 'proposal'
    manifest_file = OUT / 'proposal-manifest.json'
    if args.apply:
        manifest = json.loads(manifest_file.read_text())
        for name, digest in manifest['source'].items():
            assert sha(CAD / name) == digest, 'Concurrent source edit: ' + name
        candidate = proposal / 'CM5.kicad_sch'
        assert sha(candidate) == manifest['proposal_cm5'], 'Proposal changed; regenerate/review first'
        backup = OUT / 'backup'
        backup.mkdir(exist_ok=True)
        old = backup / 'CM5.kicad_sch'
        assert not old.exists(), 'Backup already exists; inspect before another application'
        shutil.copy2(CAD / 'CM5.kicad_sch', old)
        shutil.copy2(candidate, CAD / 'CM5.kicad_sch')
        print('Applied fan circuit to CM5.kicad_sch; PCB unchanged.')
        return
    source_files = list(CAD.glob('*.kicad_sch')) + [CAD / 'PigeonCarrier.kicad_pro', CAD / 'sym-lib-table', CAD / 'fp-lib-table']
    hashes = {p.name: sha(p) for p in source_files}
    proposal.mkdir(exist_ok=True)
    for p in source_files:
        shutil.copy2(p, proposal / p.name)
    # Review copies are at a different depth; retain access to the real libraries.
    for name in ('sym-lib-table', 'fp-lib-table'):
        table = proposal / name
        table.write_text(table.read_text().replace('${KIPRJMOD}', str(CAD)))
    candidate = edit((CAD / 'CM5.kicad_sch').read_text())
    (proposal / 'CM5.kicad_sch').write_text(candidate)
    manifest_file.write_text(json.dumps({'source': hashes, 'proposal_cm5': sha(proposal / 'CM5.kicad_sch')}, indent=2) + '\n')
    print('Prepared fan proposal:', proposal)


if __name__ == '__main__':
    main()
