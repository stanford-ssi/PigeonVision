#!/usr/bin/env python3
"""Connect existing camera/Ethernet leaf buses through the parent and CM5.

Default prepares span-preserving review copies; --apply requires the reviewed
proposal manifest and refuses concurrent changes. Only CM5/root CAD is owned.
"""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

from edit_camera_buses import CAD, PROJECT, blocks, kind, patch, uid, line
from edit_sensor_interfaces import entry, label, sheet_pin
from kicad_sexpr import parse, one, nodes

OUTPUT = PROJECT / 'build/cm5-interfaces-2026-10-02'
OWNED = ['CM5.kicad_sch', 'PigeonCarrier.kicad_sch']
MARKER = uid('cm5-carrier-interface-v1')
# The carrier uses two 100-pin physical symbols: J2 pad n is CM5 pin n+100.
MIPI = {
    0: {'D0_N':15, 'D0_P':17, 'D1_N':21, 'D1_P':23,
        'CLK_N':27, 'CLK_P':29, 'D2_N':33, 'D2_P':35,
        'D3_N':39, 'D3_P':41},
    1: {'D0_N':75, 'D0_P':77, 'D1_N':81, 'D1_P':83,
        'CLK_N':87, 'CLK_P':89, 'D2_N':93, 'D2_P':95,
        'D3_N':94, 'D3_P':96},
}
ETHERNET = {'ETH0_N':10, 'ETH0_P':12, 'ETH1_N':6, 'ETH1_P':4,
            'ETH2_N':9, 'ETH2_P':11, 'ETH3_N':5, 'ETH3_P':3}
CONTROL = {'CAM0_I2C_SCL':80, 'CAM0_I2C_SDA':82, 'CAM0_RST':97,
           'CAM1_I2C_SCL':35, 'CAM1_I2C_SDA':36, 'CAM1_RST':100,
           'ETH_LED_G':17, 'ETH_LED_K':15}


def sha(source):
    return hashlib.sha256(source.encode()).hexdigest()


def endpoints(source):
    root = parse(source)
    cache = {s[1]:s for s in nodes(one(root, 'lib_symbols'), 'symbol')}
    result = {}
    for sym in nodes(root, 'symbol'):
        ref = next(p[2] for p in nodes(sym, 'property') if p[1] == 'Reference')
        if ref not in ('J1', 'J2'):
            continue
        at = one(sym, 'at')
        assert float(at[3]) == 0 and not nodes(sym, 'mirror')
        lib = cache[one(sym, 'lib_id')[1]]
        pins = [p for sub in nodes(lib, 'symbol') for p in nodes(sub, 'pin')]
        for p in pins:
            pa = one(p, 'at')
            number = int(one(p, 'number')[1])
            result[(ref,number)] = (round(float(at[1])+float(pa[1]), 5),
                                    round(float(at[2])-float(pa[2]), 5),
                                    one(p, 'name')[1])
    return result


def breakout(added, point, name, trunk, direction, key):
    """Mirror the 45-degree descending breakout on either connector side."""
    x, y, _ = point
    assert direction in (-1,1)
    end = trunk + direction*2.54
    assert (x-end)*direction > 0
    added += [entry(trunk, y-2.54, direction*2.54, 2.54, key+'-entry'),
              line('wire', end, y, x, y, key+'-wire'),
              label(name, end if name.startswith('CAM0_I2C_') else
                    end+direction*2.54 if direction == 1 else x+3.81,
                    y, key+'-label')]


def spine(added, alias, x, top, bottom, key, hierarchical=False):
    added.append(line('bus', x, top, x, bottom, key+'-spine'))
    if hierarchical:
        added += [line('bus', x-1.27, top, x, top, key+'-header'),
                  label('{'+alias+'}', x-1.27, top, key+'-port', True, 180)]
    else:
        added.append(label('{'+alias+'}', x, top, key+'-alias'))


def cm5(source):
    if MARKER in source:
        return source
    p = endpoints(source)
    added = []
    # Ethernet pairs lie on both sides of J1. Keep the trunks above all sensors.
    for side, trunk in [('left',27.94),('right',148.59)]:
        direction = 1 if side == 'left' else -1
        selected = {name:pin for name,pin in ETHERNET.items()
                    if (p[('J1',pin)][0] < 88.9) == (side == 'left')}
        ys = [p[('J1',pin)][1] for pin in selected.values()]
        spine(added, 'ETH_DATA', trunk, min(ys)-5.08, max(ys),
              'cm5-eth-'+side, hierarchical=side == 'left')
        for name,pin in selected.items():
            assert p[('J1',pin)][2].startswith('Ethernet_Pair')
            breakout(added, p[('J1',pin)], name, trunk, direction, 'cm5-'+name)
    # Dedicated service LED signals, matching the carrier's physical color nets.
    for name in ('ETH_LED_K','ETH_LED_G'):
        x,y,_ = p[('J1',CONTROL[name])]
        added += [line('wire', x,y,142.24,y,'cm5-'+name+'-wire'),
                  label(name,142.24,y,'cm5-'+name+'-port',True)]
    # MIPI pairs retain exact manufacturer pad names; C_N/P map to CLK_N/P.
    for cam,mapping in MIPI.items():
        right = {n:pin for n,pin in mapping.items() if p[('J2',pin)][0] > 203.2}
        ys = [p[('J2',pin)][1] for pin in right.values()]
        spine(added, f'CAM{cam}',269.24,min(ys)-5.08,max(ys),
              f'cm5-cam{cam}-mipi',hierarchical=True)
        for suffix,pin in right.items():
            expected = f'MIPI{cam}_'+suffix.replace('CLK','C')
            assert p[('J2',pin)][2] == expected
            breakout(added,p[('J2',pin)],f'MIPI{cam}_{suffix}',269.24,-1,
                      f'cm5-MIPI{cam}_{suffix}')
        left = {n:pin for n,pin in mapping.items() if p[('J2',pin)][0] < 203.2}
        if left:
            ys = [p[('J2',pin)][1] for pin in left.values()]
            spine(added,f'CAM{cam}',151.13,min(ys)-5.08,max(ys),
                  f'cm5-cam{cam}-mipi-left')
            for suffix,pin in left.items():
                assert p[('J2',pin)][2] == f'MIPI{cam}_{suffix}'
                breakout(added,p[('J2',pin)],f'MIPI{cam}_{suffix}',151.13,1,
                          f'cm5-MIPI{cam}_{suffix}')
    # The same local alias joins I2C/reset breakouts to each MIPI bus port.
    # Crossings of CAM0 I2C with the preserved 3V3 trunk have no junction.
    groups = [(0,27.94,1,['CAM0_I2C_SCL','CAM0_I2C_SDA']),
              (0,146.05,-1,['CAM0_RST']),
              (1,27.94,1,['CAM1_I2C_SDA']),
              (1,148.59,-1,['CAM1_I2C_SCL']),
              (1,27.94,1,['CAM1_RST'])]
    for cam,trunk,direction,names in groups:
        ys = [p[('J1',CONTROL[n])][1] for n in names]
        key = 'cm5-cam-control-'+names[0]
        # Extra vertical separation from the CAM1 D3 bus on J2's left side.
        top = min(ys)-12.7 if names == ['CAM0_RST'] else min(ys)-5.08
        spine(added,f'CAM{cam}',trunk,top,max(ys),key)
        for name in names:
            expected = {'CAM0_I2C_SCL':'SCL0','CAM0_I2C_SDA':'SDA0',
                        'CAM1_I2C_SCL':'ID_SC','CAM1_I2C_SDA':'ID_SD',
                        'CAM0_RST':'CAM_GPIO0','CAM1_RST':'CAM_GPIO1'}.get(name,name)
            assert p[('J1',CONTROL[name])][2] == expected
            breakout(added,p[('J1',CONTROL[name])],name,trunk,direction,
                      'cm5-'+name)
    added.append(f'(text "CM5 pin numbers: J1 = 1-100; J2 pad n = pin n + 100." (at 60.96 171.45 0) (effects (font (size 1.27 1.27)) (justify left top)) (uuid "{MARKER}"))')
    return patch(source,[],added)


def parent(source):
    marker = uid('parent-CAM0-interface-bus')
    if marker in source:
        return source
    edits,added = [],[]
    for start,end,b in blocks(source):
        if kind(b) != 'sheet':
            continue
        if '(property "Sheetfile" "Cameras.kicad_sch"' in b:
            # Move only the two existing pin expressions; leave table/sheet bytes.
            pin_edits = []
            for ps,pe,pin_block in blocks(b):
                if kind(pin_block) != 'pin':
                    continue
                pin = parse(pin_block)
                if pin[1] not in ('{CAM0}','{CAM1}'):
                    continue
                y = 120.65 if pin[1] == '{CAM0}' else 128.27
                old_at = f'(at 55.245 {y:g} 180)'
                assert old_at in pin_block
                changed = pin_block.replace(old_at,f'(at 89.535 {y:g} 0)').replace('(justify left)','(justify right)')
                pin_edits.append((ps,pe,changed))
            assert len(pin_edits) == 2
            b = patch(b,pin_edits,[])
            edits.append((start,end,b))
        elif '(property "Sheetfile" "CM5.kicad_sch"' in b:
            pins = [sheet_pin('{CAM0}',123.825,120.65,180,'cm5-CAM0-parent-port'),
                    sheet_pin('{CAM1}',123.825,128.27,180,'cm5-CAM1-parent-port'),
                    sheet_pin('{ETH_DATA}',179.07,97.79,0,'cm5-ETH_DATA-parent-port'),
                    sheet_pin('ETH_LED_K',179.07,88.9,0,'cm5-ETH_LED_K-parent-port'),
                    sheet_pin('ETH_LED_G',179.07,92.71,0,'cm5-ETH_LED_G-parent-port')]
            edits.append((start,end,b[:-1]+'\n'+'\n'.join(pins)+'\n)'))
    assert len(edits)==2
    for cam,y in [(0,120.65),(1,128.27)]:
        added.append(line('bus',89.535,y,123.825,y,f'parent-CAM{cam}-interface-bus'))
    added.append(line('bus',179.07,97.79,196.215,97.79,'parent-ETH_DATA-interface-bus'))
    for name,y in [('ETH_LED_K',88.9),('ETH_LED_G',92.71)]:
        added.append(line('wire',179.07,y,196.215,y,'parent-'+name+'-interface-wire'))
    return patch(source,edits,added)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply',action='store_true')
    args=ap.parse_args()
    original={n:(CAD/n).read_text() for n in OWNED}
    updated={OWNED[0]:cm5(original[OWNED[0]]),OWNED[1]:parent(original[OWNED[1]])}
    if updated == original:
        print(json.dumps({'applied':False,'already_connected':True,'files':OWNED},indent=2))
        return
    proposal=OUTPUT/'proposed';manifest=OUTPUT/'proposal.json'
    if args.apply:
        stored=json.loads(manifest.read_text())
        for n in OWNED:
            assert sha(original[n])==stored['before'][n], 'Concurrent edit: '+n
            assert sha(updated[n])==stored['after'][n], 'Proposal changed: '+n
            assert (proposal/n).read_text()==updated[n], 'Review copy changed: '+n
        backup=OUTPUT/'backups'/stored['before'][OWNED[0]][:12]
        backup.mkdir(parents=True,exist_ok=True)
        for n in OWNED:
            assert (CAD/n).read_text()==original[n], 'Concurrent edit: '+n
            if not (backup/n).exists():(backup/n).write_text(original[n])
        for n in OWNED:
            assert (CAD/n).read_text()==original[n], 'Concurrent edit: '+n
            (CAD/n).write_text(updated[n])
        print(json.dumps({'applied':True,'backup':str(backup),'files':OWNED},indent=2))
    else:
        proposal.mkdir(parents=True,exist_ok=True)
        for file in CAD.iterdir():
            if file.suffix in ('.kicad_sch','.kicad_pro','.kicad_dru'):
                shutil.copy2(file,proposal/file.name)
        for n in OWNED:(proposal/n).write_text(updated[n])
        data={'before':{n:sha(v) for n,v in original.items()},
              'after':{n:sha(v) for n,v in updated.items()},
              'owned':OWNED,'j1_signals':CONTROL|ETHERNET,'j2_signals':MIPI}
        manifest.write_text(json.dumps(data,indent=2)+'\n')
        print(json.dumps({'applied':False,'proposal':str(proposal),'manifest':str(manifest)},indent=2))

if __name__=='__main__':main()
