const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const moduleURL = 'data:text/javascript;base64,'+fs.readFileSync(path.join(__dirname,'../tools/recovery_attitude.js')).toString('base64');
const summary={deployment_time_s:27, main_deployment_time_s:111, landing_time_s:144,
  deployment_attitude:{tilt_deg:37,roll_deg:0}};
const row={tilt_deg:37,roll_deg:0,roll_rate_rps:.2};
test('flight pose is continuous into explicitly assumed swing and main response',async()=>{
  const {assumedAttitude}=await import(moduleURL);
  assert.deepEqual(assumedAttitude(26,row,summary),{tilt:37,roll:0,speed:.2,tau:-1,landed:false,mainInflation:0});
  assert.ok(Math.abs(assumedAttitude(27+1e-5,row,summary).tilt-37)<1e-6);
  const a=assumedAttitude(47,row,summary), b=assumedAttitude(48,row,summary);
  assert.ok(Math.abs(a.tilt-b.tilt)>1,'recovery swing must not disappear after the short original clip');
  assert.ok(b.roll!==a.roll);
  assert.equal(assumedAttitude(111,row,summary).mainInflation,0);
  assert.equal(assumedAttitude(113,row,summary).mainInflation,1);
});
test('grounded camera stops rotating and canopy collapses above the same plane',async()=>{
  const {assumedAttitude,groundRecoveryPose}=await import(moduleURL);
  const a=assumedAttitude(146,row,summary), b=assumedAttitude(147,row,summary);
  assert.equal(a.tilt,90); assert.equal(a.roll,b.roll); assert.equal(a.cameraClearance,b.cameraClearance);
  assert.equal(a.speed,0); assert.equal(a.landed,true);
  const state={...a,altitude:a.cameraClearance};
  const toWorld=v=>[v[2],v[1],-v[0]];
  const pose=groundRecoveryPose(state,{joint:-.0508},{lineCount:6},toWorld);
  assert.equal(pose.inflation,0);
  assert.ok(pose.canopy[2]-.15*pose.depth+state.altitude>0);
  assert.ok(pose.booster.top[2]+state.altitude>.32);
  assert.equal(pose.lines.length,8);
  assert.deepEqual(groundRecoveryPose(state,{joint:-.0508},{lineCount:6},toWorld),pose);
});
