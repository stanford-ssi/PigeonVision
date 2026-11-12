#!/usr/bin/env python3
"""Generate six-layer carrier settings from routing-plan.json; preserve CAD objects."""
from pathlib import Path
import argparse, copy, hashlib, json, re, shutil
from edit_camera_buses import CAD, PROJECT, blocks, kind, patch
from kicad_sexpr import parse, nodes, one
import configure_routing

CLASSES={'CSI_100R':100,'ETH_100R':100,'USB_90R':90,'SE_50R':50}
PLAN=PROJECT/'hardware/carrier/routing-plan.json'
OUT=PROJECT/'build/carrier-six-layer-2026-11-07'
FILES=('PigeonCarrier.kicad_pcb','PigeonCarrier.kicad_pro','PigeonCarrier.kicad_dru')

def selected(plan,inner=False):
    return {r['targetOhm']:r for r in plan['innerSelectedResults' if inner else 'selectedResults']}

def configure_signal_vias(data, plan):
    fab = plan['fabricationRules']
    diameter, drill = fab['signalViaDiameterDrillMm']
    for netclass in data['net_settings']['classes']:
        if netclass['name'] in CLASSES:
            netclass.update(via_diameter=diameter, via_drill=drill)

    design = data['board']['design_settings']
    min_drill = fab['drillMinMm']
    design['rules'].update(
        min_through_hole_diameter=min_drill,
        min_via_diameter=diameter,
        min_via_annular_width=fab['viaAnnularRingMinMm'],
    )
    presets = [size for size in design['via_dimensions'] if size['drill'] == 0 or size['drill'] >= min_drill]
    for land, hole in reversed(fab.get('compactViaSizesMm', [(diameter, drill)])):
        preset = {'diameter': land, 'drill': hole}
        if preset not in presets:
            presets.insert(1, preset)
    design['via_dimensions'] = presets


def via_annulus_rules(plan):
    fab = plan['fabricationRules']
    general_ring = fab.get('generalViaAnnularRingMinMm')
    if general_ring is None:
        return ''

    rules = [
        '(rule "General via annular ring"\n'
        ' (condition "A.Type == \'Via\'")\n'
        f' (constraint annular_width (min {general_ring:g}mm)))\n',
        '# These exact through-via sizes override the general annular-ring rule.\n',
    ]
    for land, drill in fab.get('compactViaSizesMm', [fab['signalViaDiameterDrillMm']]):
        ring = (land - drill) / 2
        rules.append(
            f'(rule "{land:g}/{drill:g} mm through-via annular ring"\n'
            f' (condition "A.Type == \'Via\' && A.Diameter == {land:g}mm && A.Hole == {drill:g}mm")\n'
            f' (constraint annular_width (min {ring:g}mm)))\n'
        )
    return ''.join(rules)


def board(source,plan):
    # Existing copper must not silently become a different reference/routing layer.
    b=parse(source)
    already_six=len([r for r in one(b,'layers')[1:] if isinstance(r,list) and str(r[1]).endswith('.Cu')])==6
    if not already_six:
        for name in ('segment','arc','via','zone'):
            for obj in nodes(b,name):
                for field in ('layer','layers'):
                    assert not any(str(v).startswith('In') for n in nodes(obj,field) for v in n[1:]), 'Existing inner-layer copper needs explicit migration.'
    edits=[]
    for start,end,block in blocks(source):
        if kind(block)=='general':
            block=re.sub(r'\(thickness\s+[-\d.]+\)',f"(thickness {plan['CAD_total_with_mask_model_mm']:.8f})",block,count=1)
        elif kind(block)=='layers':
            block=re.sub(r'\([0-9]+ "In[0-9]+.Cu" [^)]+\)\s*','',block)
            inner='\n'.join(f'({2+2*i} "In{i}.Cu" {"signal" if i==2 else "power"})' for i in range(1,5))
            block=block.replace('(0 "F.Cu" signal)','(0 "F.Cu" signal)\n'+inner)
        elif kind(block)=='setup':
            stack=['(stackup','(layer "F.SilkS" (type "Top Silk Screen"))','(layer "F.Paste" (type "Top Solder Paste"))','(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01524) (epsilon_r 3.8))']
            for item in plan['nominal_physical_stackup_mm']:
                name=item['layer']; name=re.sub(r'dielectric([0-9]+)',r'dielectric \1',name)
                if name.endswith('.Cu'):stack.append(f'(layer "{name}" (type "copper") (thickness {item["thickness"]:g}))')
                else:stack.append(f'(layer "{name}" (type "{item["type"]}") (thickness {item["thickness"]:g}) (material "{item["material"]}") (epsilon_r {item["epsilon_r"]:g}) (loss_tangent 0.02))')
            stack+=['(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01524) (epsilon_r 3.8))','(layer "B.Paste" (type "Bottom Solder Paste"))','(layer "B.SilkS" (type "Bottom Silk Screen"))','(copper_finish "ENIG") (dielectric_constraints yes))']
            old=[(s,e,'\n'.join(stack)) for s,e,n in blocks(block) if kind(n)=='stackup']
            assert len(old)==1
            block=patch(block,old,[])
        else:continue
        edits.append((start,end,block))
    return patch(source,edits,[])

def project(source,plan):
    data=json.loads(source);net=data['net_settings'];outer=selected(plan)
    for c in net['classes']:
        if c['name'] in CLASSES:
            r=outer[CLASSES[c['name']]]
            c.update(track_width=r['fixedWidthMm'],diff_pair_width=r['fixedWidthMm'])
            if r['pairGapMm'] is not None:c['diff_pair_gap']=r['pairGapMm']
    for name in plan['rules']['singleEnded50OhmAutomaticNets']:
        row={'netclass':'SE_50R','pattern':name}
        if row not in net['netclass_patterns']:net['netclass_patterns'].append(row)
    configure_signal_vias(data, plan)
    design=data['board']['design_settings']
    for r in plan['innerSelectedResults']:
        if r['fixedWidthMm'] not in design['track_widths']:design['track_widths'].append(r['fixedWidthMm'])
        if r['pairGapMm'] is not None:
            dim={'gap':r['pairGapMm'],'via_gap':.25,'width':r['fixedWidthMm']}
            if dim not in design['diff_pair_dimensions']:design['diff_pair_dimensions'].append(dim)
    return json.dumps(data,indent=2,ensure_ascii=False)+'\n'

def rules(source,plan):
    # Keep the established fabrication constraints and reviewed footprint launches.
    text=configure_routing.drc_rules(source)
    chunks=[]
    for layer,records in [('outer',selected(plan)),('"In2.Cu"',selected(plan,True))]:
        for target in (100,90,50):
            names=[name for name,value in CLASSES.items() if value==target]
            cond=' || '.join("A.hasNetclass('%s')"%name for name in names)
            r=records[target];w=r['fixedWidthMm'];g=r['pairGapMm']
            label='outer' if layer=='outer' else 'L3'
            chunks.append(f'(rule "{label} {target} ohm trace geometry" (layer {layer}) (condition "{cond}") (constraint track_width (min {w:g}mm) (opt {w:g}mm) (max {w:g}mm))'+(f' (constraint diff_pair_gap (min {g:g}mm) (opt {g:g}mm) (max {g:g}mm))' if g is not None else '')+')')
    for layer in ('In1.Cu','In3.Cu','In4.Cu'):
        chunks.append(f'(rule "{layer} ground reference" (layer "{layer}") (condition "A.NetName != \'GND\'") (constraint disallow track zone))')
    text=text.replace('# Router widths/gaps come from netclasses.', '# Six-layer JLC06161H-3313; checked 2026-11-07.\n'+'\n'.join(chunks)+'\n# Outer defaults come from netclasses; L3 geometry comes from layer rules.')
    # Single-ended SPI needs the same short connector launch allowance.
    start=text.index('(rule "Controlled impedance footprint escape"')
    stop=text.index('# 0.40 mm CM5',start)
    both=' || '.join(f"{o}.hasNetclass('{c}')" for o in ('A','B') for c in CLASSES)
    refs=('J3','J5','J7','J8','J9','J10','U11','U13','U5','U6','J13','R42')
    footprints=' || '.join(f"(B.memberOfFootprint('{r}') && A.intersectsFrontCourtyard('{r}'))" for r in refs)
    launch=f'(rule "Controlled impedance footprint escape" (layer "F.Cu") (condition "({both}) && !AB.isCoupledDiffPair() && B.Type == \'Pad\' && (A.Type == \'Track\' || A.Type == \'Arc\' || A.Type == \'Via\') && ({footprints})") (constraint clearance (min 0.20mm)))\n'
    text=text[:start]+launch+text[stop:]
    # Local F.Cu fanout is an impedance discontinuity, not the main route model.
    marker='# High-current switch-node stubs.'
    exceptions=[]
    for name in ('CSI_100R','ETH_100R','USB_90R'):
        r=selected(plan)[CLASSES[name]];w=r['fixedWidthMm']
        launch_refs={'CSI_100R':('J5','J7','J8'),'ETH_100R':('J3','J9','U5','U6'),'USB_90R':('J5','J10','U11','U13')}[name]
        cond=f"A.hasNetclass('{name}') && ("+' || '.join(f"A.intersectsFrontCourtyard('{x}')" for x in launch_refs)+')'
        extra=f' (constraint diff_pair_gap (min 0.15mm) (opt {0.225 if name=="USB_90R" else .25:g}mm) (max {r["pairGapMm"]:g}mm))' if r['pairGapMm'] is not None else ''
        exceptions.append(f'(rule "{name} short F.Cu launch" (layer "F.Cu") (condition "{cond}"){extra})')
    text=text.replace(marker,'\n'.join(exceptions)+'\n'+marker)
    text=text.replace('# 4-layer, 1 oz outer copper, standard green mask, ENIG; checked 2026-10-04.','# 6-layer, 1 oz outer / 0.5 oz inner, ENIG and epoxy-filled/capped through vias; checked 2026-11-07.')
    marker = '# END GENERATED CARRIER IMPEDANCE RULES'
    text = text.replace(marker, via_annulus_rules(plan) + marker)
    return text

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--plan',type=Path,default=PLAN)
    ap.add_argument('--output',type=Path,default=OUT/'proposal/hardware/carrier/PigeonCarrier')
    ap.add_argument('--apply',action='store_true')
    args=ap.parse_args();plan=json.loads(args.plan.read_text())
    assert plan['selectedStackup']=='JLC06161H-3313'
    original={n:(CAD/n).read_text() for n in FILES}
    changed=dict(zip(FILES,[board(original[FILES[0]],plan),project(original[FILES[1]],plan),rules(original[FILES[2]],plan)]))
    args.output.mkdir(parents=True,exist_ok=True)
    for name,value in changed.items():(args.output/name).write_text(value)
    for p in CAD.glob('*.kicad_sch'):shutil.copy2(p,args.output/p.name)
    for name in ('sym-lib-table','fp-lib-table'):(args.output/name).write_text((CAD/name).read_text())
    libraries=args.output.parents[1]/'libraries'
    if not libraries.exists():libraries.symlink_to(PROJECT/'hardware/libraries',target_is_directory=True)
    for name in ('JLCImport_Auto.pretty','JLCImport_Auto.3dshapes','JLCImport_Auto.kicad_sym'):
        p=args.output/name
        if not p.exists():p.symlink_to(CAD/name,target_is_directory=(CAD/name).is_dir())
    hashes={'source':{n:hashlib.sha256(v.encode()).hexdigest() for n,v in original.items()},'proposal':{n:hashlib.sha256(v.encode()).hexdigest() for n,v in changed.items()}}
    (OUT/'cad-hashes.json').write_text(json.dumps(hashes,indent=2)+'\n')
    if args.apply:
        assert not list(CAD.glob('~*.lck')),'Editors must be closed'
        for name in FILES:assert (CAD/name).read_text()==original[name],'Concurrent edit'
        backup=OUT/'backups'/hashlib.sha256(''.join(original.values()).encode()).hexdigest()[:12]
        backup.mkdir(parents=True,exist_ok=True)
        for name,value in original.items():
            if not (backup/name).exists():(backup/name).write_text(value)
        for name,value in changed.items():(CAD/name).write_text(value)
    print(json.dumps({'applied':args.apply,'output':str(args.output.relative_to(PROJECT)),'stackup':plan['selectedStackup']},indent=2))
if __name__=='__main__':main()
