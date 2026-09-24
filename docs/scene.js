// Illustrative geometry only. Airbrake outlines are supplied separately from CAD.
// Coordinates are metres, with the camera ring at body z=0.
export const AIRFRAME = { bottom: -3, shoulder: 0.5, noseLength: 0.8, finSpan: 0.27 };
export const add = (a, b) => a.map((v, i) => v + b[i]);
export const mul = (a, k) => a.map((v) => v * k);
export function bodyToWorld(v, s) {
  const r = (s.roll || 0) * Math.PI / 180, t = (s.tilt || 0) * Math.PI / 180;
  const x = Math.cos(r)*v[0]-Math.sin(r)*v[1], y = Math.sin(r)*v[0]+Math.cos(r)*v[1];
  return [Math.cos(t)*x+Math.sin(t)*v[2], y, -Math.sin(t)*x+Math.cos(t)*v[2]];
}
export function worldToBody(v, s) {
  const r = -(s.roll || 0) * Math.PI / 180, t = -(s.tilt || 0) * Math.PI / 180;
  const x = Math.cos(t)*v[0]+Math.sin(t)*v[2], z = -Math.sin(t)*v[0]+Math.cos(t)*v[2];
  return [Math.cos(r)*x-Math.sin(r)*v[1], Math.sin(r)*x+Math.cos(r)*v[1], z];
}
export function finFaces(radius) {
  return Array.from({length:4}, (_, i) => {
    const a = i*Math.PI/2+Math.PI/4;
    return [[radius,AIRFRAME.bottom+.77],[radius+AIRFRAME.finSpan,AIRFRAME.bottom+.18],[radius+AIRFRAME.finSpan,AIRFRAME.bottom+.04],[radius,AIRFRAME.bottom+.04]]
      .map(([r,z]) => [Math.cos(a)*r, Math.sin(a)*r,z]);
  });
}
export function cameraBoxes(s) {
  const r=s.diameter/2000, outer=r+s.stand/1000-.001, inner=r-.012;
  return [1,-1].map(sign=>({center:[sign*(outer+inner)/2,0,0], half:[(outer-inner)/2,.022,.019]}));
}
export function boxFaces({center:c,half:h}) {
  const v=Array.from({length:8},(_,i)=>c.map((x,k)=>x+((i>>k)&1?1:-1)*h[k]));
  return [[0,2,3,1],[4,5,7,6],[0,1,5,4],[2,6,7,3],[0,4,6,2],[1,3,7,5]].map(ids=>ids.map(i=>v[i]));
}
export function recoveryPose(s) {
  const open=Math.max(0,Math.min(1,s.chuteOpen||0)), sep=Math.min(2.35,Math.max(0,s.noseOffset||0));
  const time=s.time||0, anchor=bodyToWorld([0,0,AIRFRAME.shoulder],s);
  // Canopy stays world-up: rocket roll is not canopy rotation.
  const sway=open*open;
  const canopy=add(anchor,[sway*.8*Math.sin(time*.77),sway*.55*Math.sin(time*.61+.8),2.7+3.2*open]);
  const radius=2*Math.sqrt(open), depth=Math.max(.08,radius*.55);
  const junction=add(canopy,[0,0,-1.7-1.15*open]);
  const noseBase=add(bodyToWorld([0,0,AIRFRAME.shoulder+sep],s),[sep*.32*Math.sin(time*.83),sep*.25*Math.cos(time*.64),0]);
  const axis0=add(bodyToWorld([0,0,1],s),[sep*.2*Math.sin(time*.83),sep*.16*Math.cos(time*.64),0]);
  const noseAxis=mul(axis0,1/Math.hypot(...axis0));
  const lines=[];
  if(sep>.02) lines.push([anchor,noseBase]);
  if(open>.01) {
    const bow=add(mul(add(anchor,junction),.5),[.12*Math.sin(time),.08*Math.cos(time),-.1*(1-open)]);
    lines.push([anchor,bow],[bow,junction]);
    const rimR=radius*Math.sqrt(1-.18*.18);
    for(let i=0;i<12;i++) {
      const a=i*Math.PI/6;
      lines.push([junction,add(canopy,[rimR*Math.cos(a),rimR*Math.sin(a),-.18*depth])]);
    }
  }
  return {open,sep,anchor,canopy,radius,depth,junction,noseBase,noseAxis,lines};
}
export function noseFaces(radius, pose) {
  const axis=pose.noseAxis, seed=Math.abs(axis[2])>.9?[1,0,0]:[0,0,1];
  const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
  let u=cross(axis,seed);u=mul(u,1/Math.hypot(...u));const v=cross(axis,u);
  const ring=Array.from({length:32},(_,i)=>add(pose.noseBase,add(mul(u,radius*Math.cos(i*Math.PI/16)),mul(v,radius*Math.sin(i*Math.PI/16)))));
  const tip=add(pose.noseBase,mul(axis,AIRFRAME.noseLength));
  return ring.map((p,i)=>[p,ring[(i+1)%ring.length],tip]);
}
export function canopyFaces(pose) {
  if(pose.open<=.01)return [];
  const faces=[], end=Math.acos(-.18);
  const point=(a,t)=>add(pose.canopy,[pose.radius*Math.sin(t)*Math.cos(a),pose.radius*Math.sin(t)*Math.sin(a),pose.depth*Math.cos(t)]);
  for(let i=0;i<24;i++)for(let j=0;j<8;j++) {
    const a=i*Math.PI/12,b=(i+1)*Math.PI/12,t=j*end/8,u=(j+1)*end/8;
    faces.push({points:[point(a,t),point(b,t),point(b,u),point(a,u)],color:i%2===0?'#d7472e':'#efe9d5'});
  }
  return faces;
}

export function opticalState(t, row, summary, params, preset, field, mesh, outline) {
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x)),
    deploy =
      t < 10
        ? 0
        : t < 12
          ? (t - 10) / 2
          : t < summary.apogee_time_s - 3
            ? 1
            : Math.max(0, (summary.apogee_time_s - t) / 3);
  return {
    ...params,
    time: t,
    preset,
    mesh,
    outline,
    field,
    altitude: row.altitude_m - AIRFRAME.bottom,
    roll: row.roll_deg,
    tilt: row.tilt_deg,
    speed: row.roll_rate_rps,
    deploy,
    noseOffset: Math.max(0, t - summary.deployment_time_s) * 1.3,
    chuteOpen: clamp((t - summary.deployment_time_s - 0.3) / 1.3, 0, 1),
  };
}
