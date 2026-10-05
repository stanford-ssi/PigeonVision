#!/usr/bin/env python3
"""Refresh only embedded PCB 3D-model blocks; preserve electrical/layout data.

Default writes a proposal and audit under build/model-refresh-2026-10-02.
--apply backs up the current PCB and applies only the reviewed model changes.
The library connector models should be finalized before running this script.
"""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

from edit_camera_buses import blocks, kind, patch
from kicad_sexpr import parse, nodes

ROOT = Path(__file__).resolve().parents[3]
CAD = ROOT / 'hardware/carrier/PigeonCarrier'
LIB = ROOT / 'hardware/libraries/PigeonVision.pretty'
MODELS = ROOT / 'hardware/libraries/3dmodels'
OUT = ROOT / 'build/model-refresh-2026-10-02'
BOARD = CAD / 'PigeonCarrier.kicad_pcb'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def without_models(source):
    tree = parse(source)
    for footprint in nodes(tree, 'footprint'):
        footprint[:] = [x for x in footprint if not (isinstance(x, list) and x and x[0] == 'model')]
    return tree


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    original = BOARD.read_bytes()
    source = original.decode()
    replacements, changes, dependencies = [], [], []
    thirdparty = Path.home() / 'Documents/KiCad/10.0/3rdparty'

    for start, end, block in blocks(source):
        if kind(block) != 'footprint':
            continue
        footprint = parse(block)
        props = {x[1]: x[2] for x in nodes(footprint, 'property')}
        name = footprint[1]
        reference = props.get('Reference')
        previous = [x[1] for x in nodes(footprint, 'model')]
        changed = block
        library_file = None
        if name.startswith('PigeonVision:'):
            library_file = LIB / (name.split(':', 1)[1] + '.kicad_mod')
        elif name == 'Connector_AMASS:AMASS_XT60PW-M_1x02_P7.20mm_Horizontal':
            # The local copy has the same origin and pad geometry; verify before apply.
            library_file = LIB / 'KiCad_Connector_AMASS_AMASS_XT60PW-M_1x02_P7.20mm_Horizontal.kicad_mod'
            local = parse(library_file.read_text())
            def pad_geometry(node):
                return [(p[1:4], nodes(p, 'at'), nodes(p, 'size'), nodes(p, 'drill')) for p in nodes(node, 'pad')]
            board_geometry = pad_geometry(footprint)
            lib_geometry = pad_geometry(local)
            # PCB rotation is embedded in pad orientations. Compare positions, sizes and drills.
            assert len(board_geometry) == len(lib_geometry)
            for a, b in zip(board_geometry, lib_geometry):
                assert a[0] == b[0] and a[2:] == b[2:]
                assert a[1][0][1:3] == b[1][0][1:3]
        if library_file:
            assert library_file.is_file(), library_file
            model_blocks = [text for _, _, text in blocks(library_file.read_text()) if kind(text) == 'model']
            assert model_blocks, library_file
            model_spans = [(a, b, '') for a, b, text in blocks(block) if kind(text) == 'model']
            candidate = patch(block, model_spans, model_blocks)
            # Avoid a whitespace-only edit on already current instances.
            if nodes(parse(candidate), 'model') != nodes(footprint, 'model'):
                changed = candidate
        else:
            # Old KiCad-8 PCM models are installed for KiCad 10. Copy the exact
            # original bytes so this repair changes neither geometry nor licensing.
            for model in nodes(footprint, 'model'):
                raw = model[1]
                if not raw.startswith('${KICAD8_3RD_PARTY}/'):
                    continue
                installed = thirdparty / raw.split('/', 1)[1]
                assert installed.is_file(), installed
                target = MODELS / ('JLCPCB_' + installed.name)
                data = installed.read_bytes()
                if b'CC-BY-SA' in data[:5000]:
                    license_text = 'CC-BY-SA 4.0 with electronic-design exception'
                elif b'GNU General Public License' in data[:5000]:
                    license_text = 'GPL 3 or later with embedded-design exception'
                else:
                    raise AssertionError('Missing model license: ' + str(installed))
                if target.exists():
                    assert target.read_bytes() == data, target
                else:
                    target.write_bytes(data)
                replacement = '${KIPRJMOD}/../../libraries/3dmodels/' + target.name
                changed = changed.replace(json.dumps(raw), json.dumps(replacement))
                entry = {'file': str(target.relative_to(ROOT)), 'source': str(installed),
                         'sha256': digest(data), 'geometry': 'unchanged licensed representative package model',
                         'license': license_text + '; original attribution retained in STEP header'}
                if entry not in dependencies:
                    dependencies.append(entry)
        if changed != block:
            replacements.append((start, end, changed))
            changes.append({'reference': reference, 'value': props.get('Value'), 'footprint': name,
                            'previous_models': previous,
                            'models': [x[1] for x in nodes(parse(changed), 'model')]})

    proposal = patch(source, replacements, [])
    assert without_models(source) == without_models(proposal), 'Non-model PCB data changed'
    parsed = parse(proposal)
    unresolved = []
    stock = Path('/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels')
    for footprint in nodes(parsed, 'footprint'):
        props = {x[1]: x[2] for x in nodes(footprint, 'property')}
        for model in nodes(footprint, 'model'):
            raw = model[1]
            resolved = raw.replace('${KIPRJMOD}', str(CAD)).replace('${KICAD10_3DMODEL_DIR}', str(stock))
            if not Path(resolved).is_file():
                unresolved.append({'reference': props.get('Reference'), 'model': raw})
    assert not unresolved, unresolved
    target = OUT / 'PigeonCarrier-models-proposed.kicad_pcb'
    target.write_text(proposal)
    audit = {'before_sha256': digest(original), 'after_sha256': digest(proposal.encode()),
             'footprint_count': len(nodes(parsed, 'footprint')), 'model_changes': changes,
             'dependencies': dependencies, 'unresolved_models': unresolved,
             'non_model_data_unchanged': True,
             'scope': '3D-model blocks only. Schematic synchronization, pads, nets, placements, board outline, and stackup are untouched.'}
    if args.apply:
        assert BOARD.read_bytes() == original, 'PCB changed while proposal was prepared'
        backup = OUT / 'backups' / digest(original)[:12]
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BOARD, backup / BOARD.name)
        BOARD.write_text(proposal)
        audit['applied'] = True
        audit['backup'] = str(backup.relative_to(ROOT))
    (OUT / 'model-refresh-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    print(json.dumps({'changed_instances': len(changes), 'unresolved_models': unresolved,
                      'non_model_data_unchanged': True, 'applied': args.apply}))


if __name__ == '__main__':
    main()
