#!/usr/bin/env python3
"""Simplify camera EN and omit the unused USB CC protection channels.

Prepare span-preserving review copies by default; --apply requires the reviewed
manifest, closed editors, and unchanged source files. Preserve component layout.
"""
from pathlib import Path
import argparse, copy, hashlib, json, shutil
from edit_camera_buses import CAD, PROJECT, blocks, kind, patch, uid
from edit_sensor_interfaces import power, add_cache
from kicad_sexpr import Atom, parse, one, nodes, dump

OUTPUT = PROJECT / 'build/camera-enable-direct-2026-10-02'
FILES = ['CM5.kicad_sch', 'PigeonCarrier.kicad_sch',
         'Regulators.kicad_sch', 'Camera_Regulators.kicad_sch', 'Service_USB.kicad_sch']
REMOVED = {
    'CM5.kicad_sch': {'6383e37b-cf5f-57da-9a18-09a96d9024b9',
                     '7bfa7f2b-deb1-5d0e-9170-557983783814',
                     'f42fc86c-8724-5bbb-939f-1cd377959d75',
                     '400d437b-cd1c-5790-988b-18ebfe02235a'},
    'PigeonCarrier.kicad_sch': {'a88d8d92-13c6-5a68-a55b-569724f8d6f3'},
    'Regulators.kicad_sch': {'4d91e66c-51fd-4757-a2f2-331d1541db41',
                           'e843fdd1-46fa-4215-81f6-f3be4ccb9477'},
    'Camera_Regulators.kicad_sch': {'a4a1c3a1-8fca-4866-8fdf-511c35007bff'},
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def remove_ports(name, source):
    edits = []; found = set()
    for a, b, text in blocks(source):
        obj = parse(text)
        ids = nodes(obj, 'uuid')
        identifier = ids[0][1] if ids else None
        if identifier in REMOVED[name]:
            found.add(identifier); edits.append((a, b, ''))
        elif kind(text) == 'sheet':
            pins = [(s, e, '') for s, e, child in blocks(text)
                    if kind(child) == 'pin' and parse(child)[1] == '3V3_CAM_EN']
            updated = patch(text, pins, [])
            if name == 'Regulators.kicad_sch' and 'Camera_Regulators.kicad_sch' in text:
                updated = updated.replace('"5V_MAIN -> 3V3_CAM"', '"VBAT -> 3V3_CAM"')
            if updated != text:
                edits.append((a, b, updated))
    assert found == REMOVED[name], (name, 'Unexpected saved objects', found)
    return patch(source, edits, [])


def omit_cc(source):
    edits = []; count = {'labels': 0, 'wires': 0, 'junctions': 0}
    for a, b, text in blocks(source):
        n = parse(text); typ = kind(text); remove = False
        if typ == 'label' and n[1] in ('CC1', 'CC2'):
            remove = True; count['labels'] += 1
        elif typ == 'wire':
            pts = [tuple(float(x) for x in p[1:]) for p in nodes(one(n, 'pts'), 'xy')]
            if all(y in (69.85, 72.39) and 80.01 <= x <= 121.92 for x, y in pts):
                remove = True; count['wires'] += 1
        elif typ == 'junction':
            at = tuple(float(x) for x in one(n, 'at')[1:3])
            if at in ((91.44, 69.85), (116.84, 69.85), (91.44, 72.39), (116.84, 72.39)):
                remove = True; count['junctions'] += 1
        if remove: edits.append((a, b, ''))
    assert count == {'labels': 4, 'wires': 6, 'junctions': 4}, count
    # Exact U12 pin endpoints: D2+4, D2-5, optional through-routing NC7/6.
    added = [f'(no_connect (at {x:g} {y:g}) (uuid "{uid(f"usb-unused-esd-{pin}")}"))'
             for pin, x, y in [(4, 91.44, 69.85), (5, 91.44, 72.39),
                               (7, 116.84, 69.85), (6, 116.84, 72.39)]]
    return patch(source, edits, added)


def stop_regenerating(source):
    start = source.index('    # Camera rail follows module 3V3;')
    end = source.index("    x,y,name=p[('J1',50)]", start)
    source = source[:start] + '    # Camera EN uses a direct 3V3_CM5 power symbol on its regulator sheet.\n' + source[end:]
    source = source.replace("if uid('parent-cm5-3v3-cam-enable') in source:return source",
                            "if uid('parent-cm5-5v-rf-enable') in source:return source")
    source = source.replace("ports=[sheet_pin('3V3_CAM_EN',123.825,92.71,180,'parent-cm5-camera-enable-port'),\n                   sheet_pin('5V_RF_EN',123.825,95.25,180,'parent-cm5-rf-enable-port')]",
                            "ports=[sheet_pin('5V_RF_EN',123.825,95.25,180,'parent-cm5-rf-enable-port')]")
    source = source.replace("wires=[line('wire',89.535,92.71,123.825,92.71,'parent-cm5-3v3-cam-enable'),\n           line('wire',89.535,95.25,123.825,95.25,'parent-cm5-5v-rf-enable')]",
                            "wires=[line('wire',89.535,95.25,123.825,95.25,'parent-cm5-5v-rf-enable')]")
    assert '3V3_CAM_EN' not in source
    return source


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true'); args = p.parse_args()
    tool = Path(__file__).parent / 'edit_cm5_power_controls.py'
    paths = {n: CAD / n for n in FILES} | {tool.name: tool}
    original = {n: path.read_text() for n, path in paths.items()}
    if all('3V3_CAM_EN' not in original[n] for n in FILES[:-1]):
        print('Camera enable already simplified; no files overwritten.'); return
    template = next(t for _, _, t in blocks(original['CM5.kicad_sch'])
                    if kind(t) == 'symbol' and '(property "Reference" "#PWR121001"' in t)
    updated = {n: remove_ports(n, original[n]) for n in FILES[:-1]}
    cache = copy.deepcopy(next(s for s in nodes(one(parse(original['CM5.kicad_sch']), 'lib_symbols'), 'symbol')
                               if s[1] == 'power:+3V3'))
    camera = add_cache(updated['Camera_Regulators.kicad_sch'], [cache])
    symbol = parse(power(template, 113.03, 97.79, 90, '#PWR121001', 'direct-camera-enable'))
    value = next(p for p in nodes(symbol, 'property') if p[1] == 'Value')
    # KiCad normalizes this rotated field to horizontal text; align it away
    # from the EN wire and the nearby pull-down resistor.
    one(one(value, 'effects'), 'justify')[1] = Atom('left')
    one(one(one(symbol, 'instances'), 'project'), 'path')[1] = (
        '/6c8da0f4-34cf-43b9-a25b-857048b8016b/7986dfff-dfed-4d72-b8d7-0f8819bc467b/17555969-2eda-43db-b79c-7f717ad137dd')
    updated['Camera_Regulators.kicad_sch'] = patch(camera, [], [dump(symbol)])
    updated['Service_USB.kicad_sch'] = omit_cc(original['Service_USB.kicad_sch'])
    updated[tool.name] = stop_regenerating(original[tool.name])
    protected = list(CAD.glob('*.kicad_*')) + [PROJECT / 'calculations/design-master.xlsx']
    hashes = {str(path.relative_to(PROJECT)): digest(path.read_bytes()) for path in protected if path.is_file()}
    before = {n: digest(s.encode()) for n, s in original.items()}
    after = {n: digest(s.encode()) for n, s in updated.items()}
    proposal = OUTPUT / 'proposed'; manifest = OUTPUT / 'proposal.json'
    if not args.apply:
        proposal.mkdir(parents=True, exist_ok=True)
        for path in CAD.iterdir():
            if path.suffix in ('.kicad_sch', '.kicad_pro', '.kicad_dru'):
                shutil.copy2(path, proposal / path.name)
        # Review exports need the same libraries as the active project.
        # These diagnostic-only tables are never installed into active CAD.
        for name in ('sym-lib-table', 'fp-lib-table'):
            (proposal / name).write_text((CAD / name).read_text().replace('${KIPRJMOD}', str(CAD)))
        for n, source in updated.items(): (proposal / n).write_text(source)
        manifest.write_text(json.dumps({'before': before, 'after': after, 'protected': hashes}, indent=2) + '\n')
        print(proposal); return
    stored = json.loads(manifest.read_text())
    assert not list(CAD.glob('*.lck')), 'KiCad editor lock present'
    assert before == stored['before'], 'Source files changed since review'
    assert after == stored['after'], 'Generated edits changed since review'
    assert hashes == stored['protected'], 'Protected design files changed since review'
    for n, source in updated.items(): assert (proposal / n).read_text() == source, n
    backup = OUTPUT / 'backups' / before['CM5.kicad_sch'][:12]
    backup.mkdir(parents=True, exist_ok=True)
    for n, source in original.items():
        if not (backup / n).exists(): (backup / n).write_text(source)
    for n, path in paths.items():
        assert path.read_text() == original[n], 'Concurrent edit: ' + n
        path.write_text(updated[n])
    print(json.dumps({'applied': list(paths), 'backup': str(backup)}, indent=2))


if __name__ == '__main__': main()
