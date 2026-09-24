"""Extract the supplied OpenRocket geometry and saved flight. No flight solver.

Usage: python3 software/simulator/import_openrocket.py path/to/rocket.ork
"""
import argparse
import bisect
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser()
p.add_argument('model', type=Path)
a = p.parse_args()
with zipfile.ZipFile(a.model) as z:
    xml = z.read('rocket.ork')
r = ET.fromstring(xml)
def num(e, key):
    return float(e.findtext(key).split()[-1])
components = r.find('./rocket/subcomponents/stage/subcomponents')
external = []
station = 0.0
for e in components:
    if e.tag not in ('nosecone', 'bodytube', 'transition'):
        continue
    length = num(e, 'length')
    item = dict(name=e.findtext('name'), type=e.tag, from_tip_m=station, length_m=length)
    for key in ('radius','foreradius','aftradius','thickness','shapeparameter'):
        if e.find(key) is not None: item[key] = num(e,key)
    for key in ('shape','shapeclipped'):
        if e.find(key) is not None: item[key] = e.findtext(key)
    external.append(item)
    station += length
f = r.find('.//trapezoidfinset')
fin = {k:num(f,k) for k in ('rootchord','tipchord','sweeplength','height','thickness','fincount','rotation','cant','filletradius')}
fin['axial_offset_m'] = num(f,'axialoffset')
fin['axial_method'] = f.find('axialoffset').get('method')
chutes = [{**{k:num(c,k) for k in ('diameter','linelength','linecount','deployaltitude','deploydelay')}, 'name':c.findtext('name'),'event':c.findtext('deployevent')} for c in r.findall('.//rocket//parachute')]
geometry = dict(source=a.model.name,sha256=hashlib.sha256(a.model.read_bytes()).hexdigest(),length_m=station,external=external,fins=fin,parachutes=chutes)
(ROOT/'docs/assets/rocket.json').write_text(json.dumps(geometry,indent=2)+'\n')
sims = [s for s in r.findall('./simulations/simulation') if s.get('status')=='uptodate']
assert len(sims)==1, 'Select a saved up-to-date simulation explicitly'
s = sims[0]; branch=s.find('flightdata/databranch')
keys=branch.get('types').split(',')
rows=[dict(zip(keys,map(float,d.text.split(',')))) for d in branch.findall('datapoint')]
# Duplicate event samples occur at the same time. Use the last sample.
rows=list({v['Time']:v for v in rows}.values()); rows.sort(key=lambda v:v['Time'])
times=[v['Time'] for v in rows]
events=[dict(e.attrib) for e in branch.findall('event')]
def event(name):return next(float(e['time']) for e in events if e['type']==name)
pad=3.0; apogee=event('apogee'); deploy=event('recoverydevicedeployment'); burnout=event('burnout')
duration=math.ceil((pad+deploy+6)*30)/30
# Integrate exported roll rate (radians/second), starting from arbitrary zero azimuth.
roll=0.0
for i,v in enumerate(rows):
    if i:
        prev=rows[i-1]; w0=prev['Roll rate']; w1=v['Roll rate']
        if math.isfinite(w0) and math.isfinite(w1):roll+=(w0+w1)*.5*(v['Time']-prev['Time'])
    v['Integrated roll']=roll

def sample(t):
    t=max(0,t); j=min(max(bisect.bisect_right(times,t),1),len(rows)-1)
    lo,hi=rows[j-1],rows[j]; f=min(1,max(0,(t-lo['Time'])/(hi['Time']-lo['Time'])))
    return {k:lo[k]+(hi[k]-lo[k])*f if math.isfinite(lo[k]) and math.isfinite(hi[k]) else lo[k] for k in lo}
trajectory=[]
for i in range(round(duration*30)+1):
    t=i/30;v=sample(t-pad)
    x,y,h=v['Position East of launch'],v['Position North of launch'],v['Altitude']
    distance=math.sqrt((x-100)**2+y*y+h*h)
    fspl=92.45+20*math.log10(1.28)+20*math.log10(max(distance,1)/1000)
    received=10*math.log10(500)+15-7-fspl
    noise=-174+10*math.log10(9.6e6)+3
    row=dict(t_s=t,altitude_m=h,east_m=x,north_m=y,velocity_mps=v['Vertical velocity'],total_velocity_mps=v['Total velocity'],acceleration_mps2=v['Vertical acceleration'],mass_kg=v['Mass'],thrust_n=v['Thrust'],roll_deg=math.degrees(v['Integrated roll']),roll_rate_rps=v['Roll rate']/(2*math.pi) if math.isfinite(v['Roll rate']) else 0,tilt_deg=90-math.degrees(v['Vertical orientation (zenith)']),heading_deg=90-math.degrees(v['Lateral orientation (azimuth)']),slant_range_m=distance,fspl_db=fspl,received_dbm=received,link_margin_after_10db_reserve_db=received-noise-8-10,stage='pad' if t<pad else 'boost' if t<pad+burnout else 'coast' if t<pad+apogee else 'recovery')
    trajectory.append({k:round(val,7) if isinstance(val,float) else val for k,val in row.items()})
result=dict(purpose='Saved OpenRocket flight, resampled at 30 Hz; 3 s pad hold added.',source=dict(file=a.model.name,sha256=geometry['sha256'],simulation=s.findtext('name'),motor='M2400T',saved_status=s.get('status')),inputs=dict(target_apogee_m=3048,pad_duration_s=pad,ground_horizontal_offset_m=100,body_diameter_m=0.156718),model=dict(translation='Saved OpenRocket RK4 simulation. No altitude scaling.',attitude='Exported zenith/azimuth and integrated roll rate before deployment. Recovery pose is an assumed animation.',terrain='Procedural desert; saved atmospheric conditions are not changed.'),summary=dict(sample_rate_hz=30,duration_s=duration,ignition_time_s=pad,burnout_time_s=pad+burnout,apogee_time_s=pad+apogee,apogee_altitude_m=float(s.find('flightdata').get('maxaltitude')),deployment_time_s=pad+deploy,frames=len(trajectory)),trajectory=trajectory)
(ROOT/'docs/assets/launch.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
print(json.dumps(result['summary'],indent=2))
