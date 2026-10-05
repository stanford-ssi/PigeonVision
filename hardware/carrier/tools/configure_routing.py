#!/usr/bin/env python3
"""Prepare or apply the documented JLC stackup and automatic netclasses.

Run edit_camera_buses.py first. Default writes review copies; --apply requires
both KiCad editors closed. Routing widths do not establish power ampacity.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import re
from edit_camera_buses import CAD, OUTPUT, PROJECT, blocks, kind, patch, uid

RESEARCH = OUTPUT / 'research'
PLAN = PROJECT / 'hardware/carrier/routing-plan.json'


def selected_results():
    plan = json.loads(PLAN.read_text())
    assert plan['selectedStackup'] == 'JLC04161H-3313', 'Update the physical stackup generator for a different stackup'
    records = plan['selectedResults']
    return {target: next(r for r in records if r['targetOhm'] == target and r['fixedWidthMm'] == width)
            for target, width in [(100, .150), (90, .175), (50, .160)]}


def directive(net, x, y):
    return f'''(directive_label "" (length 2.54) (shape dot) (at {x:g} {y:g} 0)
 (effects (font (size 1.27 1.27)) (justify left bottom))
 (uuid "{uid(net + '-class')}")
 (property "Netclass" "CSI_100R" (at {x - .6985:g} {y - 2.54:g} 0)
  (effects (font (size 0.9 0.9) (italic yes)) (justify right))))'''


def tag_cameras(source):
    additions = []
    for _, _, block in blocks(source):
        if kind(block) != 'label':
            continue
        match = re.match(r'\(label "(MIPI([01])_(?:D[0-3]|CLK)_N)"', block)
        if match and uid(match[1] + '-class') not in source:
            y = float(re.search(r'\(at\s+[-\d.]+\s+([-\d.]+)', block)[1])
            additions.append(directive(match[1], 60.96 + 137.16 * int(match[2]), y))
    return patch(source, [], additions)


def settings(source):
    data = json.loads(source)
    # Existing root sheet edges and bus ports use the 25 mil grid.
    data['schematic']['connection_grid_size'] = 25.0
    net = data['net_settings']
    base = next(x for x in net['classes'] if x['name'] == 'Default')
    chosen = selected_results()
    csi, usb = chosen[100], chosen[90]
    specs = [
        ('CSI_100R', csi['fixedWidthMm'], csi['pairGapMm'], .2),
        ('ETH_100R', csi['fixedWidthMm'], csi['pairGapMm'], .2),
        ('USB_90R', usb['fixedWidthMm'], usb['pairGapMm'], .2),
        ('SE_50R', .160, .127, .2),
        ('Power_Battery', 1.5, .25, .2),
        ('Power_5V', 1.0, .25, .2),
        ('Power_3V3', .5, .25, .2),
        ('I2C', .2, .25, .2),
    ]
    owned = {x[0] for x in specs}
    others = [x for x in net['classes'] if x['name'] not in owned and x['name'] != 'Default']
    classes = []
    for priority, (name, width, gap, clearance) in enumerate(specs):
        item = copy.deepcopy(base)
        item.update(name=name, priority=priority, track_width=width,
                    diff_pair_width=width, diff_pair_gap=gap, clearance=clearance)
        classes.append(item)
    net['classes'] = classes + others + [base]
    patterns = []
    def assign(name, values):
        patterns.extend({'netclass': name, 'pattern': p} for p in values)
    assign('CSI_100R', [f'*MIPI{cam}_*_{pol}' for cam in range(2) for pol in ['P', 'N']])
    assign('ETH_100R', [f'*ETH{pair}_{pol}' for pair in range(4) for pol in ['P', 'N']])
    assign('USB_90R', ['*USB_D_P', '*USB_D_N', '*USB2_D_P', '*USB2_D_N', '*USB2_P', '*USB2_N', '*SERVICE_USB_P', '*SERVICE_USB_N'])
    assign('Power_Battery', ['*VBAT', '*VBAT_RAW'])
    assign('Power_5V', ['*5V_MAIN', '*5V_RF'])
    assign('Power_3V3', ['*3V3_CAM', '*CAM_3V3', '*3V3_CM5', '*CM5_3V3', '+3V3'])
    assign('I2C', ['*CAM0_I2C_SCL', '*CAM0_I2C_SDA', '*CAM1_I2C_SCL',
                   '*CAM1_I2C_SDA', '*SENSOR_I2C_SCL', '*SENSOR_I2C_SDA', '*SCL_0', '*SDA_0'])
    # Preserve unrelated user classes/patterns. SE_50R is intentionally reserved.
    net['netclass_patterns'] = patterns + [x for x in net.get('netclass_patterns', []) if x['netclass'] not in owned]
    design = data['board']['design_settings']
    design['rules']['min_track_width'] = .10
    design['rules']['min_clearance'] = max(.09, design['rules']['min_clearance'])
    return json.dumps(data, indent=2, ensure_ascii=False) + '\n'


def top_sheet(source):
    lines = [
        'Routing / stackup: Design Master, Power budget A74:F87 (2026-10-02)',
        'Full calculator inputs: hardware/carrier/routing-plan.json',
    ]
    identifier = uid('fabrication-top-note')
    note = f'(text {json.dumps(chr(10).join(lines))} (at 20.32 146.05 0) (effects (font (size 1.27 1.27)) (justify left top)) (uuid "{identifier}"))'
    edits = [(s, e, note) for s, e, b in blocks(source) if kind(b) == 'text' and identifier in b]
    return patch(source, edits, [] if edits else [note])


def board(source):
    # KiCad 10 copper layer identifiers: F=0, In1=4, In2=6, B=2.
    edits = []
    for start, end, block in blocks(source):
        if kind(block) == 'layers':
            block = re.sub(r'\(4 "In1.Cu" [^)]+\)\s*', '', block)
            block = re.sub(r'\(6 "In2.Cu" [^)]+\)\s*', '', block)
            block = block.replace('(0 "F.Cu" signal)', '(0 "F.Cu" signal)\n\t\t(4 "In1.Cu" power)\n\t\t(6 "In2.Cu" power)')
            edits.append((start, end, block))
        elif kind(block) == 'general':
            # Nominal drawing copper/dielectric sum 1.5642 mm, plus the
            # calculator's 0.01524 mm over-trace mask model on each side.
            block = re.sub(r'\(thickness\s+[-\d.]+\)', '(thickness 1.59468)', block, count=1)
            edits.append((start, end, block))
        elif kind(block) == 'setup':
            # These are the vendor's nominal stackup numbers. The solver uses
            # finished plated outer copper, separately recorded in its inputs.
            stack = '''(stackup
 (layer "F.SilkS" (type "Top Silk Screen"))
 (layer "F.Paste" (type "Top Solder Paste"))
 (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01524) (epsilon_r 3.8))
 (layer "F.Cu" (type "copper") (thickness 0.035))
 (layer "dielectric 1" (type "prepreg") (thickness 0.0994) (material "3313 RC57%") (epsilon_r 4.1))
 (layer "In1.Cu" (type "copper") (thickness 0.0152))
 (layer "dielectric 2" (type "core") (thickness 1.265) (material "NP-155F") (epsilon_r 4.42))
 (layer "In2.Cu" (type "copper") (thickness 0.0152))
 (layer "dielectric 3" (type "prepreg") (thickness 0.0994) (material "3313 RC57%") (epsilon_r 4.1))
 (layer "B.Cu" (type "copper") (thickness 0.035))
 (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01524) (epsilon_r 3.8))
 (layer "B.Paste" (type "Bottom Solder Paste"))
 (layer "B.SilkS" (type "Bottom Silk Screen"))
 (copper_finish "ENIG") (dielectric_constraints yes))'''
            # Parse setup as a temporary root to preserve its other objects.
            existing = [(s, e, b) for s, e, b in blocks(block) if kind(b) == 'stackup']
            if len(existing) == 1 and existing[0][2] == stack:
                continue
            old = [(s, e, '') for s, e, b in existing]
            block = patch(block, old, [stack])
            edits.append((start, end, block))
    return patch(source, edits, [])


def drc_rules(source):
    marker = '# BEGIN GENERATED CARRIER IMPEDANCE RULES'
    if marker in source:
        source = re.sub(r'# BEGIN GENERATED CARRIER IMPEDANCE RULES.*?# END GENERATED CARRIER IMPEDANCE RULES\n?', '', source, flags=re.S).rstrip() + '\n'
    if not source.strip():
        source = '(version 1)\n'
    # No cross-lane matching: within_diff_pairs checks each P/N pair.
    return source + '''\n# BEGIN GENERATED CARRIER IMPEDANCE RULES
# Router widths/gaps come from netclasses. Connector escapes and nearby
# surface copper require layout review of the noncoplanar model.
(rule "CSI within-pair matching"
 (condition "A.hasNetclass('CSI_100R')")
 (constraint skew (max 0.15mm) (within_diff_pairs)))
(rule "Ethernet within-pair matching"
 (condition "A.hasNetclass('ETH_100R')")
 (constraint skew (max 0.15mm) (within_diff_pairs)))
(rule "USB within-pair matching"
 (condition "A.hasNetclass('USB_90R')")
 (constraint skew (max 0.15mm) (within_diff_pairs)))
# This is the spacing policy for the noncoplanar model, not an impedance
# simulation. Future connector escape regions need separate layout review.
(rule "Controlled impedance spacing to unrelated copper"
 (condition "(A.hasNetclass('CSI_100R') || A.hasNetclass('ETH_100R') || A.hasNetclass('USB_90R') || A.hasNetclass('SE_50R')) && !AB.isCoupledDiffPair() && !(A.Type == 'Pad' && B.Type == 'Pad' && A.Parent == B.Parent)")
 (constraint clearance (min 0.5mm)))
# END GENERATED CARRIER IMPEDANCE RULES
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    src = CAD if args.apply else OUTPUT / 'proposed'
    names = ['Cameras.kicad_sch', 'PigeonCarrier.kicad_sch', 'PigeonCarrier.kicad_pro', 'PigeonCarrier.kicad_pcb', 'PigeonCarrier.kicad_dru']
    original = {}
    for name in names:
        path = src / name if (src / name).exists() else CAD / name
        original[name] = path.read_text() if path.exists() else ''
    changed = dict(zip(names, [tag_cameras(original[names[0]]), top_sheet(original[names[1]]),
                               settings(original[names[2]]), board(original[names[3]]), drc_rules(original[names[4]])]))
    digest = hashlib.sha256(''.join(original.values()).encode()).hexdigest()[:12]
    dest = OUTPUT / ('backups/' + digest if args.apply else 'proposed')
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        if args.apply:
            current = (CAD / name).read_text() if (CAD / name).exists() else ''
            assert current == original[name], f'Concurrent edit of {name}; refusing overwrite'
            if not (dest / name).exists():
                (dest / name).write_text(original[name])
            if changed[name] != original[name]:
                (CAD / name).write_text(changed[name])
        else:
            (dest / name).write_text(changed[name])
    print(json.dumps({'applied': args.apply, 'backup_or_proposal': str(dest), 'files': names}, indent=2))


if __name__ == '__main__':
    main()
