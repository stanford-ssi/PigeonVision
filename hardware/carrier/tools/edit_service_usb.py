#!/usr/bin/env python3
"""Add the reviewed self-powered USB2 port and CM5 service controls.

Prepare review copies by default; --apply preserves all existing sheet objects
and refuses concurrent edits. Parts are from the existing project libraries.
"""
from pathlib import Path
import argparse, copy, hashlib, json, math, shutil
from edit_camera_buses import CAD, PROJECT, blocks, kind, patch, uid, line
from edit_sensor_interfaces import label, sheet_pin
from kicad_sexpr import Atom, parse, nodes, one, dump

OUT = PROJECT / 'build/service-usb-implementation-2026-10-02'
NEW_FILE = 'Service_USB.kicad_sch'
NEW_SHEET = uid('service-usb-parent-sheet')
ROOT_ID = one(parse((CAD/'PigeonCarrier.kicad_sch').read_text()), 'uuid')[1]
INSTANCE_PATH = '/' + ROOT_ID + '/' + NEW_SHEET
MARKER = uid('service-usb-cm5-usb-p-wire')


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def cached(filename, name):
    root = parse((CAD/filename).read_text())
    return copy.deepcopy(next(x for x in nodes(one(root, 'lib_symbols'), 'symbol') if x[1] == name))


class Drawing:
    def __init__(self):
        self.cache, self.objects, self.parts = {}, [], {}
        lib = parse((PROJECT/'hardware/libraries/PigeonVision.kicad_sym').read_text())
        self.library = {'PigeonVision:'+s[1]:s for s in nodes(lib, 'symbol')}
        rpath = Path.home()/'Documents/KiCad/10.0/3rdparty/symbols/com_github_CDFER_JLCPCB-Kicad-Library/JLCPCB-Resistors.kicad_sym'
        resistors = parse(rpath.read_text())
        for name in ('0402,5.1kΩ', '0402,1kΩ', '0402,10kΩ'):
            s = copy.deepcopy(next(s for s in nodes(resistors, 'symbol') if s[1] == name))
            s[1] = 'PCM_JLCPCB-Resistors:'+name
            self.library[s[1]] = s
        for fn, name in [('Sensors.kicad_sch','PCM_JLCPCB-Capacitors:0402,100nF,(2)'),
                         ('Sensors.kicad_sch','PCM_SparkFun-PowerSymbol:GND'),
                         ('CM5.kicad_sch','power:+3V3')]:
            self.library[name] = cached(fn, name)
        power_lib = parse(Path('/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/power.kicad_sym').read_text())
        for name in ('VBUS','PWR_FLAG'):
            s = copy.deepcopy(next(s for s in nodes(power_lib,'symbol') if s[1] == name))
            s[1] = 'power:'+name
            self.library[s[1]] = s
        self.n = 0

    def add(self, text):
        self.objects.append(text)

    def part(self, libid, ref, x, y, angle=0, value=None, fields=None, hide_value=False):
        lib = copy.deepcopy(self.library[libid]); lib[1] = libid
        self.cache[libid] = lib
        obj = parse(f'(symbol (lib_id {json.dumps(libid)}) (at {x:g} {y:g} {angle}) (unit 1) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (uuid "{uid("service-part-"+ref)}"))')
        power = ref.startswith('#')
        for original in nodes(lib,'property'):
            if original[1].startswith('ki_'): continue
            p = copy.deepcopy(original)
            a = one(p,'at'); a[1:4] = [Atom(f'{x:g}'),Atom(f'{y:g}'),Atom('0')]
            p[:] = [v for v in p if not (isinstance(v,list) and v[0] in ('hide','do_not_autoplace','show_name'))]
            eff = one(p,'effects')
            eff[:] = [v for v in eff if not (isinstance(v,list) and v[0] in ('hide','justify'))]
            if p[1]=='Reference':
                p[2] = ref
                a[1:3] = [Atom(f'{x:g}'),Atom(f'{y-12.7:g}')]
                if power:p.append(parse('(hide yes)'))
            elif p[1]=='Value':
                if value is not None:p[2] = value
                a[1:3] = [Atom(f'{x:g}'),Atom(f'{y-10.16:g}')]
                if hide_value:p.append(parse('(hide yes)'))
            else:p.append(parse('(hide yes)'))
            if fields and p[1] in fields:
                fx, fy, fa = fields[p[1]]
                a[1:4] = [Atom(f'{fx:g}'),Atom(f'{fy:g}'),Atom(str(fa))]
                if p[1]=='Reference' and libid.startswith('PCM_JLCPCB-Resistors:'):
                    eff.append(parse('(justify left)'))
            obj.append(p)
        pin_positions = {}
        rad = math.radians(angle)
        for sub in nodes(lib,'symbol'):
            for p in nodes(sub,'pin'):
                num = one(p,'number')[1]; pa = one(p,'at')
                lx,ly = map(float,pa[1:3])
                pin_positions[num] = (round(x+lx*math.cos(rad)-ly*math.sin(rad),5),
                                      round(y-lx*math.sin(rad)-ly*math.cos(rad),5))
                obj.append(parse(f'(pin {json.dumps(num)} (uuid "{uid("service-pin-"+ref+"-"+num)}"))'))
        obj.append(parse(f'(instances (project "PigeonCarrier" (path "{INSTANCE_PATH}" (reference {json.dumps(ref)}) (unit 1))))'))
        self.add(dump(obj)); self.parts[ref] = pin_positions
        return pin_positions

    def wire(self,x1,y1,x2,y2,key):
        assert x1!=x2 or y1!=y2
        self.add(line('wire',x1,y1,x2,y2,'service-usb-'+key))

    def junction(self,x,y,key):
        self.add(f'(junction (at {x:g} {y:g}) (diameter 0) (color 0 0 0 0) (uuid "{uid("service-junction-"+key)}"))')

    def local(self,name,x,y,key,angle=0):
        self.add(label(name,x,y,'service-label-'+key,False,angle))

    def port(self,name,x,y,key,angle=0,shape='bidirectional'):
        s=label(name,x,y,'service-port-'+key,True,angle)
        self.add(s.replace('(shape bidirectional)',f'(shape {shape})'))

    def nc(self,x,y,key):
        self.add(f'(no_connect (at {x:g} {y:g}) (uuid "{uid("service-nc-"+key)}"))')

    def supply(self,x,y,kind_='3V3_CM5',angle=0):
        self.n+=1
        libid = 'power:+3V3' if kind_=='3V3_CM5' else 'power:VBUS' if kind_=='HOST_USB_VBUS' else 'PCM_SparkFun-PowerSymbol:GND'
        a = (x,y+3.81,0) if kind_=='GND' else (x,y-3.81,0)
        if angle:a = (x-3.81,y,90)
        self.part(libid,f'#PWR070{self.n:02}',x,y,angle,value=kind_,fields={'Value':a},hide_value=kind_=='GND')

    def text(self,text,x,y,key,size=2):
        self.add(f'(text {json.dumps(text)} (at {x:g} {y:g} 0) (effects (font (size {size:g} {size:g})) (justify left)) (uuid "{uid("service-text-"+key)}"))')

    def resistor(self,ref,value,x,y,angle=0,reference_below=False):
        fields={'Reference':(x+2.54,y-1.27,0),'Value':(x,y,90)} if angle==0 else {'Reference':(x,y-3.81,90),'Value':(x,y,90)}
        if reference_below:fields['Reference']=(x,y+2.54,90)
        return self.part('PCM_JLCPCB-Resistors:0402,'+value,ref,x,y,angle,fields=fields)

    def render(self):
        return '(kicad_sch\n (version 20250114)\n (generator "eeschema")\n (generator_version "10.0")\n (uuid "'+uid('service-usb-file')+'")\n (paper "A4")\n (lib_symbols\n'+'\n'.join(dump(s) for s in self.cache.values())+'\n )\n'+'\n'.join(self.objects)+'\n (embedded_fonts no)\n)\n'


def service():
    d=Drawing()
    d.text('USB-C Data',30.48,33.02,'connector-title')
    d.text('Data Switch',127,33.02,'switch-title')
    d.text('Boot & Power',205.74,33.02,'controls-title')
    d.text('ESD',30.48,97.79,'esd-title')
    d.text('VBUS Detect',93.98,88.9,'detector-title')
    d.part('PigeonVision:USB4105_GF_A','J6',43.18,66.04,
           fields={'Reference':(43.18,41.91,0),'Value':(43.18,44.45,0)})
    d.wire(58.42,50.8,66.04,50.8,'vbus')
    d.wire(66.04,50.8,66.04,48.26,'vbus-power')
    d.supply(66.04,48.26,'HOST_USB_VBUS')
    d.wire(66.04,50.8,71.12,50.8,'vbus-flag')
    d.junction(66.04,50.8,'vbus')
    d.part('power:PWR_FLAG','#FLG0701',71.12,50.8,hide_value=True)
    d.resistor('R31','5.1kΩ',78.74,55.88,90)
    d.resistor('R32','5.1kΩ',78.74,60.96,90,reference_below=True)
    d.wire(58.42,55.88,74.93,55.88,'cc1-rd')
    d.wire(58.42,58.42,71.12,58.42,'cc2-rd-a')
    d.wire(71.12,58.42,71.12,60.96,'cc2-rd-b')
    d.wire(71.12,60.96,74.93,60.96,'cc2-rd-c')
    d.local('CC1',60.96,55.88,'cc1'); d.local('CC2',60.96,58.42,'cc2')
    d.wire(82.55,55.88,87.63,55.88,'cc1-ground')
    d.wire(82.55,60.96,87.63,60.96,'cc2-ground')
    d.supply(87.63,55.88,'GND',90); d.supply(87.63,60.96,'GND',90)
    d.wire(58.42,68.58,62.23,68.58,'dp-a')
    d.wire(58.42,71.12,62.23,71.12,'dp-b')
    d.wire(62.23,71.12,62.23,68.58,'dp-join')
    d.wire(62.23,68.58,66.04,68.58,'dp-label')
    d.junction(62.23,68.58,'dp');d.local('USB_D_P',66.04,68.58,'connector-dp')
    d.wire(58.42,63.5,64.77,63.5,'dn-a')
    d.wire(58.42,66.04,64.77,66.04,'dn-b')
    d.wire(64.77,63.5,64.77,66.04,'dn-join')
    d.wire(64.77,66.04,66.04,66.04,'dn-label')
    d.junction(64.77,66.04,'dn');d.local('USB_D_N',66.04,66.04,'connector-dn')
    for y in (78.74,81.28):d.nc(58.42,y,'sbu-'+str(y))
    d.wire(35.56,88.9,43.18,88.9,'shell-ground')
    d.wire(43.18,88.9,43.18,91.44,'connector-ground')
    d.junction(43.18,88.9,'connector-ground');d.supply(43.18,91.44,'GND')

    d.part('PigeonVision:TS3USB221DRCR','U11',147.32,68.58,
           fields={'Reference':(147.32,39.37,0),'Value':(147.32,41.91,0)})
    d.wire(147.32,58.42,147.32,50.8,'vcc')
    d.wire(147.32,50.8,147.32,48.26,'vcc-symbol');d.supply(147.32,48.26)
    d.part('PCM_JLCPCB-Capacitors:0402,100nF,(2)','C62',160.02,54.61,
           fields={'Reference':(163.83,53.34,0),'Value':(163.83,55.88,0)})
    d.wire(147.32,50.8,160.02,50.8,'bypass');d.junction(147.32,50.8,'vcc')
    d.supply(160.02,58.42,'GND')
    for name,y in [('USB_D_P',63.5),('USB_D_N',66.04),('USB_DATA_OE',73.66)]:
        start=118.11 if name=='USB_DATA_OE' else 127
        d.wire(start,y,137.16,y,'switch-'+name);d.local(name,start,y,'switch-'+name)
    d.wire(137.16,71.12,134.62,71.12,'select')
    d.supply(134.62,71.12,'GND',270)
    for name,y in [('USB2_P',63.5),('USB2_N',66.04)]:
        d.wire(157.48,y,165.1,y,'module-'+name);d.port(name,165.1,y,'module-'+name)
    d.nc(157.48,71.12,'mux-path2-p');d.nc(157.48,73.66,'mux-path2-n')
    d.wire(147.32,78.74,147.32,81.28,'switch-ground');d.supply(147.32,81.28,'GND')

    d.part('PigeonVision:TPD4E05U06DQAR','U12',53.34,119.38,
           fields={'Reference':(53.34,102.87,0),'Value':(53.34,105.41,0)})
    for name,y in [('USB_D_P',111.76),('USB_D_N',114.3),('CC1',119.38),('CC2',121.92)]:
        d.wire(29.21,y,40.64,y,'esd-'+name);d.local(name,29.21,y,'esd-'+name)
        # The opposite NC pad has no internal connection. Explicit external
        # copper joins it to the protected pad, matching the Ethernet sheet.
        d.wire(40.64,y,66.04,y,'esd-pass-'+name)
        d.wire(66.04,y,71.12,y,'esd-out-'+name)
        d.junction(40.64,y,'esd-in-'+name);d.junction(66.04,y,'esd-out-'+name)
        d.local(name,71.12,y,'esd-out-'+name)
    d.wire(50.8,129.54,55.88,129.54,'esd-grounds')
    d.wire(55.88,129.54,55.88,132.08,'esd-ground');d.junction(55.88,129.54,'esd-ground');d.supply(55.88,132.08,'GND')

    d.part('PigeonVision:BSS138BK,215','Q3',147.32,111.76,
           fields={'Reference':(153.67,109.22,0),'Value':(158.75,114.3,0)})
    d.resistor('R33','1kΩ',114.3,111.76,90)
    d.resistor('R34','10kΩ',132.08,118.11)
    d.resistor('R35','10kΩ',166.37,99.06)
    d.wire(118.11,111.76,132.08,111.76,'gate-a')
    d.wire(132.08,111.76,142.24,111.76,'gate-b')
    d.wire(132.08,111.76,132.08,114.3,'gate-pulldown');d.junction(132.08,111.76,'gate')
    d.wire(132.08,121.92,132.08,124.46,'gate-ground');d.supply(132.08,124.46,'GND')
    d.wire(149.86,116.84,149.86,119.38,'fet-ground');d.supply(149.86,119.38,'GND')
    d.wire(149.86,106.68,149.86,102.87,'oe-vertical')
    d.wire(149.86,102.87,166.37,102.87,'oe-pullup')
    d.local('USB_DATA_OE',151.13,102.87,'detector-oe')
    d.wire(166.37,95.25,166.37,92.71,'oe-supply');d.supply(166.37,92.71)
    d.wire(93.98,111.76,110.49,111.76,'vbus-detector')
    d.supply(93.98,111.76,'HOST_USB_VBUS')
    d.part('PigeonVision:TPD1E05U06DYAR','U13',93.98,118.11,270,
           fields={'Reference':(101.6,116.84,90),'Value':(105.41,120.65,90)})
    d.wire(93.98,111.76,93.98,114.3,'vbus-tvs')
    d.wire(93.98,121.92,93.98,124.46,'vbus-tvs-ground');d.supply(93.98,124.46,'GND')

    d.part('PigeonVision:Samtec_TSM-102-01-L-SV-P-TR','J7',223.52,58.42,
           fields={'Reference':(223.52,45.72,0),'Value':(223.52,48.26,0)})
    d.wire(210.82,58.42,218.44,58.42,'boot');d.port('nRPIBOOT',210.82,58.42,'boot',180,'output')
    d.wire(218.44,60.96,218.44,66.04,'boot-ground');d.supply(218.44,66.04,'GND')
    d.text('Fit before power-up',205.74,73.66,'boot-note',1.27)
    d.part('PigeonVision:PTS636_SK25_SMTR_LFS','SW1',223.52,93.98,
           fields={'Reference':(223.52,81.28,0),'Value':(223.52,83.82,0)})
    d.wire(210.82,93.98,218.44,93.98,'button');d.port('PWR_BUT',210.82,93.98,'button',180,'output')
    d.wire(228.6,93.98,233.68,93.98,'button-a')
    d.wire(233.68,93.98,233.68,99.06,'button-ground');d.supply(233.68,99.06,'GND')
    return d.render()


def cm5(source):
    if MARKER in source:return source
    added=[]
    from edit_cm5_interfaces import endpoints
    p=endpoints(source)
    for ref,pin,name,shape,direction,length in [('J2',5,'USB2_P','bidirectional',1,10.16),
             ('J2',3,'USB2_N','bidirectional',1,10.16),
             ('J1',92,'PWR_BUT','input',-1,12.7),('J1',93,'nRPIBOOT','input',1,10.16)]:
        x,y,pinname=p[(ref,pin)];assert pinname==name
        end=x+direction*length
        key='service-usb-cm5-'+ ('usb-p' if name=='USB2_P' else name)
        added.append(line('wire',x,y,end,y,key+'-wire'))
        added.append(label(name,end,y,key+'-port',True,0 if direction==1 else 180).replace('(shape bidirectional)',f'(shape {shape})'))
    for ref,pin in [('J2',1),('J1',94),('J1',96),('J1',99),('J1',20)]:
        x,y,name=p[(ref,pin)]
        existing=[one(parse(b),'at')[1:3] for _,_,b in blocks(source) if kind(b)=='no_connect']
        if not any(tuple(map(float,pos))==(x,y) for pos in existing):
            added.append(f'(no_connect (at {x:g} {y:g}) (uuid "{uid("service-cm5-nc-"+name)}"))')
    return patch(source,[],added)


def parent(source):
    if NEW_SHEET in source:return source
    signals=[('USB2_P','bidirectional',145.415),('USB2_N','bidirectional',153.035),
             ('nRPIBOOT','input',160.655),('PWR_BUT','input',168.275)]
    edits=[]
    for a,b,text in blocks(source):
        if kind(text)=='sheet' and '(property "Sheetfile" "CM5.kicad_sch"' in text:
            ports=[sheet_pin(n,x,139.065,270,'service-parent-cm5-'+n).replace(' bidirectional ',f' {shape} ') for n,shape,x in signals]
            edits.append((a,b,text[:-1]+'\n'+'\n'.join(ports)+'\n)'))
    assert len(edits)==1
    obj=parse(f'(sheet (at 123.825 156.21) (size 50.8 25.4) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0)) (uuid "{NEW_SHEET}") (property "Sheetname" "USB & Service" (at 123.825 155.4984 0) (effects (font (size 1.27 1.27)) (justify left bottom))) (property "Sheetfile" "{NEW_FILE}" (at 123.825 182.1946 0) (effects (font (size 1.27 1.27)) (justify left top))) (instances (project "PigeonCarrier" (path "/{ROOT_ID}" (page "11")))))')
    added=[]
    for n,shape,x in signals:
        typ='bidirectional' if shape=='bidirectional' else 'output'
        obj.append(parse(sheet_pin(n,x,156.21,90,'service-parent-new-'+n).replace(' bidirectional ',f' {typ} ')))
        added.append(line('wire',x,139.065,x,156.21,'service-parent-wire-'+n))
    added.append(dump(obj))
    return patch(source,edits,added)


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    originals={n:(CAD/n).read_text() for n in ['CM5.kicad_sch','PigeonCarrier.kicad_sch']}
    updated={'CM5.kicad_sch':cm5(originals['CM5.kicad_sch']),
             'PigeonCarrier.kicad_sch':parent(originals['PigeonCarrier.kicad_sch']),NEW_FILE:service()}
    OUT.mkdir(parents=True,exist_ok=True)
    proposal=OUT/'proposed';manifest=OUT/'proposal.json'
    if args.apply:
        proof=json.loads(manifest.read_text())
        assert not (CAD/NEW_FILE).exists(), 'New sheet already exists'
        assert not list(CAD.glob('*.lck')), 'KiCad editor is open'
        for name,source in originals.items():assert sha(source)==proof['before'][name], 'Concurrent edit: '+name
        for name,source in updated.items():assert sha(source)==proof['after'][name], 'Proposal changed: '+name
        backup=OUT/'backups'/proof['before']['PigeonCarrier.kicad_sch'][:12];backup.mkdir(parents=True,exist_ok=True)
        for name,source in originals.items():
            assert (CAD/name).read_text()==source
            (backup/name).write_text(source)
        for name,source in updated.items():(CAD/name).write_text(source)
        print(json.dumps({'applied':True,'files':list(updated),'backup':str(backup)}))
    else:
        proposal.mkdir(parents=True,exist_ok=True)
        for p in CAD.iterdir():
            if p.suffix in ('.kicad_sch','.kicad_pro','.kicad_dru'):shutil.copy2(p,proposal/p.name)
            elif p.name.endswith('-lib-table'):
                (proposal/p.name).write_text(p.read_text().replace('${KIPRJMOD}/../../libraries',str(PROJECT/'hardware/libraries')).replace('${KIPRJMOD}/',str(CAD)+'/'))
        for name,source in updated.items():(proposal/name).write_text(source)
        manifest.write_text(json.dumps({'before':{n:sha(s) for n,s in originals.items()},'after':{n:sha(s) for n,s in updated.items()},'files':list(updated)},indent=2)+'\n')
        print(json.dumps({'applied':False,'proposal':str(proposal)}))


if __name__=='__main__':main()
