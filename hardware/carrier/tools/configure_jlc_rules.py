#!/usr/bin/env python3
"""Apply JLC 4-layer/1 oz fabrication limits without changing placements.

Source: https://jlcpcb.com/capabilities/pcb-capabilities/ (2026-10-04).
Use standard green soldermask and ENIG. Prepare and inspect a proposal first;
--apply backs up the three owned files and rejects concurrent changes.
"""
import argparse
import hashlib
import json
import re
import shutil

from edit_camera_buses import CAD, PROJECT, blocks, kind, patch
from kicad_sexpr import parse, nodes, one

OUT = PROJECT / 'build/jlc-rules-2026-10-04'
FILES = ('PigeonCarrier.kicad_pro', 'PigeonCarrier.kicad_pcb', 'PigeonCarrier.kicad_dru')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project(source):
    data = json.loads(source)
    design = data['board']['design_settings']
    rules = design['rules']
    # 0.10 is deliberately above JLC's 0.09 multilayer absolute capability.
    # The existing impedance netclass widths and gaps remain the routing defaults.
    limits = {
        'min_track_width': .10, 'min_clearance': .10,
        'min_copper_edge_clearance': .50,  # retain margin above routed-edge 0.20
        'min_hole_clearance': .20, 'min_hole_to_hole': .25,
        'min_through_hole_diameter': .30, 'min_via_diameter': .50,
        'min_via_annular_width': .15,
        'min_silk_clearance': .15, 'min_text_height': 1.0,
        'min_text_thickness': .15, 'solder_mask_to_copper_clearance': .09,
    }
    for name, value in limits.items():
        # Hole clearance can be selected per item below; other lower bounds
        # retain any stricter user setting already present.
        rules[name] = value if name == 'min_hole_clearance' else max(value, rules.get(name, 0))
    defaults = design['defaults']
    defaults['silk_line_width'] = max(.15, defaults['silk_line_width'])
    defaults['silk_text_size_h'] = max(1., defaults['silk_text_size_h'])
    defaults['silk_text_size_v'] = max(1., defaults['silk_text_size_v'])
    defaults['silk_text_thickness'] = max(.15, defaults['silk_text_thickness'])
    # Keep 0.6/0.3 mm standard routing vias; annular ring = 0.15 mm.
    # Micro/blind/buried vias are excluded by the standard-process rule below.
    return json.dumps(data, indent=2, ensure_ascii=False) + '\n'


def board(source):
    start, end, setup = next(x for x in blocks(source) if kind(x[2]) == 'setup')
    edits = [(s, e, '') for s, e, b in blocks(setup)
             if kind(b) in ('pad_to_mask_clearance', 'solder_mask_min_width')]
    # Existing per-footprint mask expansion overrides remain intentional.
    setup = patch(setup, edits, ['(pad_to_mask_clearance 0)', '(solder_mask_min_width 0.10)'])
    return patch(source, [(start, end, setup)], [])


def drc_rules(source):
    source = re.sub(r'\n?# BEGIN GENERATED JLC FABRICATION RULES.*?# END GENERATED JLC FABRICATION RULES\n?', '', source, flags=re.S)
    old = "(A.hasNetclass('CSI_100R') || A.hasNetclass('ETH_100R') || A.hasNetclass('USB_90R') || A.hasNetclass('SE_50R')) && !AB.isCoupledDiffPair()"
    # Parent is the actual parent UUID, so reannotation and duplicated refs
    # cannot accidentally treat pads from two instances as one package.
    new = old + " && !(A.Type == 'Pad' && B.Type == 'Pad' && A.Parent == B.Parent)"
    if old in source and new not in source:
        source = source.replace(old, new)
    assert new in source, 'Expected controlled-impedance rule missing'
    return source.rstrip() + '''

# BEGIN GENERATED JLC FABRICATION RULES
# 4-layer, 1 oz outer copper, standard green mask, ENIG; checked 2026-10-04.
# Fixed same-package pads are exempt only from the 0.5 mm impedance-model
# spacing above. Routing, vias and pads in other footprints retain it.
# JLC minimum different-net SMD land spacing applies to all SMD pad pairs.
(rule "JLC SMD pad spacing"
 (condition "A.Type == 'Pad' && B.Type == 'Pad' && A.Pad_Type == 'SMD' && B.Pad_Type == 'SMD' && A.Parent == B.Parent")
 (constraint clearance (min 0.15mm)))
(rule "JLC separate SMD footprint pad spacing"
 (condition "A.Type == 'Pad' && B.Type == 'Pad' && A.Pad_Type == 'SMD' && B.Pad_Type == 'SMD' && A.Parent != B.Parent && !(A.hasNetclass('CSI_100R') || B.hasNetclass('CSI_100R') || A.hasNetclass('ETH_100R') || B.hasNetclass('ETH_100R') || A.hasNetclass('USB_90R') || B.hasNetclass('USB_90R') || A.hasNetclass('SE_50R') || B.hasNetclass('SE_50R'))")
 (constraint clearance (min 0.15mm)))
(rule "JLC PTH annular ring"
 (condition "A.Type == 'Pad' && A.Pad_Type == 'Through-hole'")
 (constraint annular_width (min 0.15mm)))
(rule "JLC PTH hole to unrelated copper"
 (condition "A.Type == 'Pad' && A.Pad_Type == 'Through-hole'")
 (constraint hole_clearance (min 0.28mm)))
(rule "JLC inner PTH hole to unrelated copper"
 (layer inner)
 (condition "A.Type == 'Pad' && A.Pad_Type == 'Through-hole'")
 (constraint hole_clearance (min 0.30mm)))
(rule "JLC drilled pad hole separation"
 (condition "A.Type == 'Pad' || B.Type == 'Pad'")
 (constraint hole_to_hole (min 0.45mm)))
(rule "JLC round NPTH minimum"
 (condition "A.Type == 'Pad' && A.Pad_Type == 'NPTH, mechanical'")
 (constraint hole_size (min 0.50mm)))
(rule "Standard through-via process"
 (constraint disallow micro_via blind_via buried_via))
# END GENERATED JLC FABRICATION RULES
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    proposal = OUT / 'proposal'
    manifest_path = OUT / 'manifest.json'
    if args.apply:
        manifest = json.loads(manifest_path.read_text())
        backup = OUT / ('backup-' + manifest['source'][FILES[0]][:12])
        for name in FILES:
            assert sha(CAD / name) == manifest['source'][name], 'Concurrent edit: ' + name
            assert sha(proposal / name) == manifest['proposal'][name], 'Proposal changed: ' + name
        backup.mkdir(exist_ok=True)
        for name in FILES:
            assert not (backup / name).exists(), 'Existing backup; inspect before reapplying'
            shutil.copy2(CAD / name, backup / name)
            shutil.copy2(proposal / name, CAD / name)
        print('Applied JLC rules; no schematic, footprint or placement edits.')
        return
    proposal.mkdir(exist_ok=True)
    changed = dict(zip(FILES, [project((CAD / FILES[0]).read_text()),
                                board((CAD / FILES[1]).read_text()),
                                drc_rules((CAD / FILES[2]).read_text())]))
    for name, content in changed.items():
        (proposal / name).write_text(content)
    # DRC resolves cached footprints; absolute table paths retain library checks.
    for name in ('fp-lib-table', 'sym-lib-table'):
        p = proposal / name
        p.write_text((CAD / name).read_text().replace('${KIPRJMOD}', str(CAD)))
    manifest_path.write_text(json.dumps({'source': {n: sha(CAD / n) for n in FILES},
                                        'proposal': {n: sha(proposal / n) for n in FILES}}, indent=2) + '\n')
    print('Prepared:', proposal)


if __name__ == '__main__':
    main()
