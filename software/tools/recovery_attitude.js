// Deterministic demonstration motion. These angles are not measured attitude.
const ease = x => { x = Math.max(0, Math.min(1, x)); return x*x*(3-2*x); };
const mix = (a, b, t) => a + (b-a)*t;
export function assumedAttitude(t, row, summary) {
  const tau = t-summary.deployment_time_s;
  if (tau <= 0) return { tilt: row.tilt_deg, roll: row.roll_deg, speed: row.roll_rate_rps, tau, landed: false, mainInflation: 0 };
  const base = summary.deployment_attitude || row;
  const recovery = elapsed => {
    const damping=.32, omega=2.1, wd=omega*Math.sqrt(1-damping*damping);
    const step=Math.exp(-damping*omega*elapsed)*(Math.cos(wd*elapsed)+damping/Math.sqrt(1-damping*damping)*Math.sin(wd*elapsed));
    const swing=(9*Math.sin(1.1*elapsed+.4)+5*Math.sin(.37*elapsed))*ease(elapsed/3);
    const mainTau=summary.main_deployment_time_s == null ? -1 : elapsed+summary.deployment_time_s-summary.main_deployment_time_s;
    const mainShake=mainTau>0 ? 16*Math.sin(2.7*mainTau)*Math.exp(-mainTau/5) : 0;
    return { tilt: 180-(180-base.tilt_deg)*step-swing-mainShake,
      roll: base.roll_deg+11*elapsed+15*Math.sin(.53*elapsed)+mainShake*.5 };
  };
  const landingTau=t-summary.landing_time_s;
  let pose=recovery(Math.min(t, summary.landing_time_s)-summary.deployment_time_s);
  if (landingTau >= 0) {
    const progress=ease(landingTau/1.4);
    pose={ tilt: mix(pose.tilt, 90, progress), roll: mix(pose.roll, pose.roll+12, progress) };
  }
  const radians=pose.tilt*Math.PI/180;
  // Camera section rests on its nose, then its side. Extra 30 mm clears housings.
  const cameraClearance=.108359*Math.abs(Math.sin(radians))+
    Math.max(.9024*Math.max(0,-Math.cos(radians)), .0508*Math.max(0,Math.cos(radians)));
  const mainTau=summary.main_deployment_time_s == null ? -1 : t-summary.main_deployment_time_s;
  return { ...pose, speed: 0, tau, cameraClearance, landed: landingTau>=0,
    landingTau: Math.max(0,landingTau), mainInflation: mainTau>0 ? ease(mainTau/1.2) : 0 };
}

export function groundRecoveryPose(s, airframe, recovery, toWorld) {
  const blend=ease(s.landingTau/1.4), ground=-s.altitude;
  const attach=toWorld([0,0,airframe.joint],s);
  const swivel=[1.9,1.5,mix(2.6,ground+.02,blend)];
  const booster={ top:[-1.1,2.3,ground+.33], x:[1,0,0], y:[0,0,-1], z:[0,1,0] };
  const canopy=[mix(1.9,3.7,blend),1.5,mix(3.8,ground+.035,blend)];
  const radius=mix(1.0668*.8,1.0668,blend), depth=mix(radius*.65,.025,blend);
  const axis=[0,0,1], ex=[1,0,0], ey=[0,1,0], lines=[[attach,swivel],[swivel,booster.top]];
  for(let i=0;i<recovery.lineCount;i++) {
    const angle=i*2*Math.PI/recovery.lineCount;
    lines.push([swivel,[canopy[0]+radius*Math.cos(angle),canopy[1]+radius*Math.sin(angle),canopy[2]-.15*depth]]);
  }
  return { separated:true, tau:s.tau, attach, swivel, booster, canopy, axis, ex, ey,
    radius, depth, inflation:1-blend, lines };
}
