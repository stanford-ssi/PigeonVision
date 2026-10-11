#!/usr/bin/env python3
"""Prepare scoped CM5 power/control wiring and clear camera reset net names.

Default prepares review copies. --apply requires the reviewed manifest and
refuses concurrent edits. Physical components, pin names and tables are retained.
"""
from pathlib import Path
import argparse,copy,hashlib,json,shutil
from edit_camera_buses import CAD,PROJECT,blocks,kind,patch,uid,line
from edit_sensor_interfaces import label,sheet_pin,power,add_cache
from edit_cm5_interfaces import endpoints
from kicad_sexpr import parse,one,nodes,walk,dump

OUTPUT=PROJECT/'build/cm5-power-controls-2026-10-02'
CAD_OWNED=['CM5.kicad_sch','Cameras.kicad_sch','PigeonCarrier.kicad_sch','PigeonCarrier.kicad_pro']
TOOL_OWNED=['edit_camera_buses.py','edit_cm5_interfaces.py']
RENAMES={'CAM_GPIO0':'CAM0_RST','CAM_GPIO1':'CAM1_RST'}
MARKER=uid('cm5-power-controls-v1')

def sha(s):return hashlib.sha256(s.encode()).hexdigest()

def rename_objects(source, camera=False):
    edits=[]
    for a,b,text in blocks(source):
        typ=kind(text)
        if typ in ('label','global_label','hierarchical_label'):
            name=parse(text)[1]
            if name in RENAMES:edits.append((a,b,text.replace(json.dumps(name),json.dumps(RENAMES[name]),1)))
            elif camera and name=='CAM_3V3':edits.append((a,b,text.replace('"CAM_3V3"','"3V3_CAM"',1)))
        elif camera and typ=='symbol' and '(property "Value" "CAM_3V3"' in text:
            edits.append((a,b,text.replace('(property "Value" "CAM_3V3"','(property "Value" "3V3_CAM"',1)))
    return patch(source,edits,[])

def cm5(source,sensors,regulators):
    source=rename_objects(source)
    if MARKER in source:return source
    r=parse(source);p=endpoints(source)
    supply=next(text for _,_,text in blocks(source) if kind(text)=='symbol' and '(property "Value" "3V3_CM5"' in text)
    cm5_path=one(one(one(parse(supply),'instances'),'project'),'path')[1]
    ground=next(text for _,_,text in blocks(sensors) if kind(text)=='symbol' and '(lib_id "PCM_SparkFun-PowerSymbol:GND")' in text)
    main=next(text for _,_,text in blocks(regulators) if kind(text)=='symbol' and '(property "Value" "5V_MAIN"' in text)
    cache=[]
    for src,name in [(sensors,'PCM_SparkFun-PowerSymbol:GND'),(regulators,'power:+5V')]:
        cache.append(copy.deepcopy(next(s for s in nodes(one(parse(src),'lib_symbols'),'symbol') if s[1]==name)))
    source=add_cache(source,cache)
    added=[]
    def new_power(template,x,y,angle,ref,key,hide_value=False):
        obj=parse(power(template,x,y,angle,ref,key))
        one(one(one(obj,'instances'),'project'),'path')[1]=cm5_path
        if hide_value:
            val=next(v for v in nodes(obj,'property') if v[1]=='Value')
            if not nodes(val,'hide'):val.append(parse('(hide yes)'))
        return dump(obj)
    grounds={ref:sorted(pin for (rr,pin),(_,_,name) in p.items() if rr==ref and name=='GND') for ref in ['J1','J2']}
    assert sum(map(len,grounds.values()))==51
    for ref,pins in grounds.items():
        midpoint=88.9 if ref=='J1' else 203.2
        for pin in pins:
            x,y,_=p[(ref,pin)];left=x<midpoint;end=x+(-1.27 if left else 1.27)
            key=f'cm5-ground-{ref}-{pin}'
            added.extend([line('wire',x,y,end,y,key+'-wire'),
                          new_power(ground,end,y,270 if left else 90,f'#PWR12{0 if ref=="J1" else 1}{pin:03}',key,True)])
    # One main-power spine reaches all six contacts without touching reset wires.
    inputs=[77,79,81,83,85,87]
    assert all(p[('J1',pin)][2]=='+5v_(Input)' for pin in inputs)
    trunk=130.81;ys=[p[('J1',pin)][1] for pin in inputs]
    added.extend([line('wire',trunk,min(ys)-5.08,trunk,min(ys),'cm5-5v-main-top'),
                  new_power(main,trunk,min(ys)-5.08,0,'#PWR121000','cm5-5v-main-power')])
    for i,pin in enumerate(inputs):
        x,y,_=p[('J1',pin)]
        added.append(line('wire',x,y,trunk,y,f'cm5-5v-main-pin-{pin}'))
        if i<len(inputs)-1:
            added.append(line('wire',trunk,y,trunk,ys[i+1],f'cm5-5v-main-spine-{pin}'))
            added.append(f'(junction (at {trunk:g} {y:g}) (diameter 0) (color 0 0 0 0) (uuid "{uid(f"cm5-5v-main-junction-{pin}")}"))')
    # Camera EN uses a direct 3V3_CM5 power symbol on its regulator sheet.
    x,y,name=p[('J1',50)];assert name=='GPIO17'
    added.extend([line('wire',33.02,y,x,y,'cm5-rf-enable-wire'),
                  label('5V_RF_EN',33.02,y,'cm5-rf-enable-port',True,180),
                  f'(text "RF EN: GPIO17; default off." (at 60.96 176.53 0) (effects (font (size 1.27 1.27)) (justify left top)) (uuid "{MARKER}"))'])
    return patch(source,[],added)

def parent(source):
    if uid('parent-cm5-5v-rf-enable') in source:return source
    edits=[];found=0
    for a,b,text in blocks(source):
        if kind(text)=='sheet' and '(property "Sheetfile" "CM5.kicad_sch"' in text:
            found+=1
            ports=[sheet_pin('5V_RF_EN',123.825,95.25,180,'parent-cm5-rf-enable-port')]
            ports=[z.replace(' bidirectional ',' output ') for z in ports]
            edits.append((a,b,text[:-1]+'\n'+'\n'.join(ports)+'\n)'))
    assert found==1
    wires=[line('wire',89.535,95.25,123.825,95.25,'parent-cm5-5v-rf-enable')]
    return patch(source,edits,wires)

def tools_update(name,source):
    if name=='edit_camera_buses.py':
        # Retain legacy source-label support while emitting the reviewed names.
        source=source.replace("'ID_SC': 'CAM1_I2C_SCL', 'ID_SD': 'CAM1_I2C_SDA'}", "'ID_SC': 'CAM1_I2C_SCL', 'ID_SD': 'CAM1_I2C_SDA',\n           'CAM_GPIO0':'CAM0_RST', 'CAM_GPIO1':'CAM1_RST'}")
        source=source.replace("clear in ('CAM_GPIO0', 'CAM_GPIO1')","clear in ('CAM0_RST', 'CAM1_RST')")
        source=source.replace("f'CAM_GPIO{cam}'","f'CAM{cam}_RST'")
    else:
        if "'CAM0_RST':'CAM_GPIO0'" in source:return source
        for before,after in RENAMES.items():source=source.replace(before,after)
        source=source.replace("'CAM1_I2C_SDA':'ID_SD'}.get(name,name)","'CAM1_I2C_SDA':'ID_SD',\n                        'CAM0_RST':'CAM_GPIO0','CAM1_RST':'CAM_GPIO1'}.get(name,name)")
    return source

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    paths={n:CAD/n for n in CAD_OWNED}|{n:Path(__file__).parent/n for n in TOOL_OWNED}
    original={n:p.read_text() for n,p in paths.items()}
    settings=original['PigeonCarrier.kicad_pro']
    for old,new in RENAMES.items():settings=settings.replace(json.dumps(old),json.dumps(new))
    updated={
      'CM5.kicad_sch':cm5(original['CM5.kicad_sch'],(CAD/'Sensors.kicad_sch').read_text(),(CAD/'Regulators.kicad_sch').read_text()),
      'Cameras.kicad_sch':rename_objects(original['Cameras.kicad_sch'],True),
      'PigeonCarrier.kicad_sch':parent(original['PigeonCarrier.kicad_sch']),
      'PigeonCarrier.kicad_pro':settings,
    }|{n:tools_update(n,original[n]) for n in TOOL_OWNED}
    if updated==original:print('Already applied');return
    proposal=OUTPUT/'proposed';manifest=OUTPUT/'proposal.json'
    if args.apply:
        stored=json.loads(manifest.read_text())
        for n in paths:
            assert sha(original[n])==stored['before'][n], 'Concurrent edit: '+n
            assert sha(updated[n])==stored['after'][n], 'Proposal changed: '+n
            assert (proposal/n).read_text()==updated[n], 'Review copy changed: '+n
        backup=OUTPUT/'backups'/stored['before']['CM5.kicad_sch'][:12];backup.mkdir(parents=True,exist_ok=True)
        for n,p in paths.items():
            assert p.read_text()==original[n], 'Concurrent edit: '+n
            if not (backup/n).exists():(backup/n).write_text(original[n])
        for n,p in paths.items():
            assert p.read_text()==original[n], 'Concurrent edit: '+n
            p.write_text(updated[n])
        print(json.dumps({'applied':True,'backup':str(backup),'files':list(paths)},indent=2))
    else:
        proposal.mkdir(parents=True,exist_ok=True)
        for file in CAD.iterdir():
            if file.suffix in ('.kicad_sch','.kicad_pro','.kicad_dru'):shutil.copy2(file,proposal/file.name)
        for n,s in updated.items():(proposal/n).write_text(s)
        manifest.write_text(json.dumps({'before':{n:sha(s) for n,s in original.items()},'after':{n:sha(s) for n,s in updated.items()},'owned':list(paths)},indent=2)+'\n')
        print(json.dumps({'applied':False,'proposal':str(proposal)},indent=2))

if __name__=='__main__':main()
