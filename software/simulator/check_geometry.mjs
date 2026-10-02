import assert from 'node:assert/strict';
import fs from 'node:fs';
import {ORK, STATIONS, radiusAt, nominalProfile, finOutline} from '../../docs/rocket.js';
import {bodyToWorld,worldToBody,finSurfaces,recoveryPose} from '../../docs/scene.js';
const source=JSON.parse(fs.readFileSync(new URL('../../docs/assets/rocket.json',import.meta.url)));
const flight=JSON.parse(fs.readFileSync(new URL('../../docs/assets/launch.json',import.meta.url)));
const near=(a,b,e=1e-9)=>assert.ok(Math.abs(a-b)<e,`${a} != ${b}`);
near(STATIONS.tip-STATIONS.tail,source.length_m);
near(ORK.radius,source.external[0].aftradius);
near(ORK.nose.length,source.external[0].length_m);
for(const [key,field] of [['root','rootchord'],['tip','tipchord'],['sweep','sweeplength'],['span','height'],['thickness','thickness'],['count','fincount']])near(ORK.fins[key],source.fins[field]);
const tail=source.external.at(-1),R=ORK.radius,a=tail.aftradius,L=tail.length_m;
// Independently solve OpenRocket's clipping equation numerically.
let lo=0,hi=10;
const ellipse=(x,r,l)=>r*Math.sqrt(2*x/l-(x/l)**2);
for(let i=0;i<80;i++){const c=(lo+hi)/2;if(ellipse(c,R,L+c)<a)lo=c;else hi=c;}
for(let i=0;i<=100;i++){
 const x=L*i/100;
 near(radiusAt(STATIONS.mainBottom-x),ellipse(hi+L-x,R,hi+L),1e-8);
}
const prof=nominalProfile();let maxError=0;
for(let i=0;i<prof.length-1;i++){
 const [z0,r0]=prof[i],[z1,r1]=prof[i+1];assert.ok(z0>z1);
 for(let j=0;j<=20;j++){const f=j/20,z=z0+(z1-z0)*f;maxError=Math.max(maxError,Math.abs(r0+(r1-r0)*f-radiusAt(z)));}
}
assert.ok(maxError<0.0001,`Profile discretisation ${maxError} m`);
const fin=finOutline(R);
near(fin[0][1],STATIONS.mainBottom-source.fins.axial_offset_m+source.fins.rootchord);
near(fin[3][1],STATIONS.mainBottom-source.fins.axial_offset_m);
const surfaces=finSurfaces(R);
near(Math.hypot(...surfaces[0][0].map((v,i)=>v-surfaces[1][0][i])),source.fins.thickness);
for(const state of [{roll:32,tilt:17,heading:90},{roll:-28,tilt:163,heading:43}]){
 const v=[.087,1.2,-.4],q=worldToBody(bodyToWorld(v,state),state);
 q.forEach((val,i)=>near(val,v[i]));
}
const pose=recoveryPose({tau:3,diameter:156.718,tilt:170,roll:15,heading:34});
for(const [a,b] of pose.lines.slice(-6))near(Math.hypot(...a.map((v,i)=>v-b[i])),.3);
near(flight.summary.apogee_altitude_m,3191.207);
near(flight.summary.burnout_time_s-flight.summary.ignition_time_s,3.616);
assert.equal(flight.trajectory.length,flight.summary.frames);
assert.ok(flight.trajectory.every((r,i)=>Math.abs(r.t_s-i/30)<1e-6));
assert.ok(flight.trajectory.some(r=>Math.abs(r.east_m)>1));
console.log(`OpenRocket dimensions, clipped ellipse, fins, shrouds, transforms and saved flight checked. Maximum profile error ${(maxError*1000).toFixed(3)} mm.`);
