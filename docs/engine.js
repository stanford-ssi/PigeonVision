import { TERRAIN, terrainMaps, TERRAIN_GLSL } from "./terrain.js";
import { SCENARIO } from "./scenario.js";
import { STATIONS, ORK, RAIL_BUTTONS, PROFILE_POINTS } from "./rocket.js";
import { finFaces, cameraBoxes, recoveryPose, airframeProfile, bodyRadius, LAUNCHER } from "./scene.js";
// The renderer owns pixels only. Flight state and the interface live in app.js.
export const PRESETS = {
  900: {
    w: SCENARIO.camera.crop_px,
    h: SCENARIO.camera.crop_px,
    sensorW: SCENARIO.camera.sensor_width,
    sensorH: SCENARIO.camera.sensor_height,
    pitch: SCENARIO.camera.pixel_pitch_mm,
    circle: SCENARIO.camera.image_circle_mm,
    field: SCENARIO.camera.field_deg,
    focal: SCENARIO.camera.efl_mm,
    efl: SCENARIO.camera.efl_mm,
    distortionB: SCENARIO.camera.distortion_b,
    b: SCENARIO.camera.distortion_b,
    global: true,
    name: "IMX900 · CIL212",
  },
  676: {
    w: 3552,
    h: 3552,
    pitch: 0.002,
    circle: 6.8,
    field: 215,
    global: false,
    name: "IMX676 · CIL220",
  },
  matched: {
    w: 3552,
    h: 3552,
    pitch: 0.002,
    circle: 7.07,
    field: 185,
    global: false,
    name: "IMX676 · L185A",
  },
};
const f1 = (v) => v.toFixed(5);
const PROFILE_MAX = PROFILE_POINTS;
const VERTEX = `#version 300 es
in vec2 position;out vec2 uv;void main(){uv=position*.5+.5;gl_Position=vec4(position,0.,1.);}`;
const COMMON = `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
in vec2 uv;out vec4 frag;
uniform vec2 screen,sensor;
uniform float circlePx,halfField,roll,tilt,heading,bodyR,stand,height,leafR,deploy,altitude,speed,readout,skew,exposure;
uniform int meshReady,samples;
uniform vec2 worldXY;
uniform vec2 leafOutline[24];
uniform float flightTime,pixelPitch,lensF,lensB,pixelAngle;
uniform vec3 profile[${PROFILE_MAX}];
uniform int profileCount;
uniform float profileMaxR;
uniform vec3 finVertices[12],housingCenter[2],housingHalf[2];
uniform int separated;
uniform vec3 boosterTop,boosterX,boosterY,boosterZ;
uniform vec3 canopyCenter,canopyAxis,canopyEx,canopyEy,recoveryLines[20];
uniform float canopyRadius,canopyDepth;
uniform int recoveryLineCount;
const float PI=3.14159265359;
const float Z_TIP=${f1(STATIONS.tip)},Z_NOSE=${f1(STATIONS.noseBase)},Z_SWTOP=${f1(STATIONS.switchTop)},Z_JOINT=${f1(STATIONS.switchBottom)},Z_MAIN=${f1(STATIONS.mainBottom)},Z_TAIL=${f1(STATIONS.tail)};
vec3 rz(vec3 v,float a){float c=cos(a),s=sin(a);return vec3(c*v.x-s*v.y,s*v.x+c*v.y,v.z);}
vec3 ry(vec3 v,float a){float c=cos(a),s=sin(a);return vec3(c*v.x+s*v.z,v.y,-s*v.x+c*v.z);}
vec3 toWorld(vec3 d,float tm){return rz(ry(rz(d,roll+tm*speed*2.*PI),tilt),heading);}
${TERRAIN_GLSL}
// ------------------------------------------------------------ atmosphere
// Single scattering in an exponential Rayleigh + desert-aerosol atmosphere.
const vec3 BR=vec3(5.8e-6,13.5e-6,33.1e-6);
const float BM=1.6e-5,HR=8000.,HM=1300.,G=.76;
vec2 densityPath(float z0,float dz,float t){
 vec2 e0=exp(-vec2(z0/HR,z0/HM));
 if(abs(dz)<1e-4)return e0*t;
 vec2 e1=exp(-vec2((z0+dz*t)/HR,(z0+dz*t)/HM));
 return vec2(HR,HM)*(e0-e1)/dz;
}
vec2 densitySky(float z0,float dz){
 vec2 e0=exp(-vec2(z0/HR,z0/HM)),c=vec2(.026,.011);
 float up=max(dz,0.);
 return vec2(HR,HM)*e0/sqrt(up*up+c*c);
}
vec3 sunLight(float z){
 vec2 m=densitySky(z,SUN.z);return exp(-(BR*m.x+BM*m.y))*vec3(1.,.985,.96);
}
// Returns in-scattered light; transmittance through 'trans'.
vec3 scatter(vec3 d,vec2 path,float z,out vec3 trans){
 vec3 ext=BR*path.x+BM*path.y;trans=exp(-ext);
 float mu=dot(d,SUN);
 float pr=3./(16.*PI)*(1.+mu*mu);
 float pm=(1.-G*G)/(4.*PI*pow(1.+G*G-2.*G*mu,1.5));
 vec3 single=(BR*path.x*pr+BM*path.y*pm)/max(ext,vec3(1e-7));
 // Crude multiple scattering: an isotropic share of the sky.
 vec3 multi=(BR*path.x*.045+BM*path.y*.028)/max(ext,vec3(1e-7));
 return (single*sunLight(z+1500.)+multi*vec3(.8,.9,1.))*(1.-trans)*1.45;
}
vec3 skyRadiance(vec3 d,float z){
 vec3 tr;vec3 c=scatter(d,densitySky(z,d.z),z,tr);
 float mu=dot(d,SUN);
 c+=sunLight(z)*tr*smoothstep(.99998,.999995,mu)*60.;
 return c;
}
const vec3 SKY_E=vec3(.36,.47,.66); // sky irradiance relative to the sun
vec3 tonemap(vec3 x){
 x*=2.7;
 x=clamp((x*(2.51*x+.03))/(x*(2.43*x+.59)+.14),0.,1.);
 return pow(x,vec3(1./2.2));
}
// ------------------------------------------------------------ geometry
struct Hit{float t;vec3 n;vec3 albedo;float gloss;int kind;};
// Stack of cone frustums from the OpenRocket profile (z, r, dr/dz).
float bodyZ;
void revolved(vec3 o,vec3 d,float zLo,float zHi,inout Hit h){
 float a=dot(d.xy,d.xy),b=dot(o.xy,d.xy),oo=dot(o.xy,o.xy);
 if(a>1e-12){if(b*b-a*(oo-profileMaxR*profileMaxR)<0.)return;}else if(oo>profileMaxR*profileMaxR)return;
 for(int i=0;i<${PROFILE_MAX - 1};i++){
  if(i>=profileCount-1)break;
  vec3 p0=profile[i],p1=profile[i+1];
  if(p1.x>=zHi||p0.x<=zLo)continue;
  float zt=min(p0.x,zHi),zb=max(p1.x,zLo);
  float k=(p1.y-p0.y)/(p1.x-p0.x),r0=p0.y+k*(o.z-p0.x);
  float A=a-k*k*d.z*d.z,B=b-k*d.z*r0,C=oo-r0*r0;
  if(abs(A)<1e-12)continue;
  float disc=B*B-A*C;if(disc<0.)continue;
  float sq=sqrt(disc);
  for(int s=0;s<2;s++){
   float t=(-B+(s==0?-sq:sq))/A;
   if(t<=1e-4||t>=h.t)continue;
   float z=o.z+t*d.z;
   if(z>zt||z<zb||r0+k*t*d.z<0.)continue;
   vec3 p=o+t*d;float slope=mix(p0.z,p1.z,(z-p0.x)/(p1.x-p0.x));
   h.t=t;h.n=normalize(vec3(normalize(p.xy),-slope));h.kind=1;bodyZ=z;
  }
 }
}
void disc(vec3 o,vec3 d,float z,float r,vec3 albedo,inout Hit h){
 if(abs(d.z)<1e-9)return;float t=(z-o.z)/d.z;
 if(t>1e-4&&t<h.t&&length((o+t*d).xy)<r){h.t=t;h.n=vec3(0,0,-sign(d.z));h.albedo=albedo;h.gloss=.1;h.kind=3;}
}
bool triangle(vec3 o,vec3 d,vec3 a,vec3 b,vec3 c,inout Hit h){
 vec3 e=b-a,f=c-a,p=cross(d,f);float det=dot(e,p);if(abs(det)<1e-10)return false;
 vec3 q=o-a;float u=dot(q,p)/det;vec3 v=cross(q,e);float w=dot(d,v)/det,t=dot(f,v)/det;
 if(u<0.||w<0.||u+w>1.||t<=1e-4||t>=h.t)return false;
 vec3 n=normalize(cross(e,f));h.t=t;h.n=dot(n,d)>0.?-n:n;h.kind=3;return true;
}
void fins(vec3 o,vec3 d,inout Hit h){
 for(int i=0;i<3;i++){
  vec3 q[4];for(int j=0;j<4;j++)q[j]=finVertices[i*4+j];
  vec3 n=normalize(cross(q[1]-q[0],q[3]-q[0]))*${(ORK.fins.thickness/2).toFixed(8)};
  float before=h.t;
  for(int side=0;side<2;side++){
   vec3 off=side==0?n:-n;
   triangle(o,d,q[0]+off,q[1]+off,q[2]+off,h);
   triangle(o,d,q[0]+off,q[2]+off,q[3]+off,h);
  }
  for(int j=0;j<4;j++){
   vec3 a=q[j],b=q[(j+1)%4];
   triangle(o,d,a+n,b+n,b-n,h);triangle(o,d,a+n,b-n,a-n,h);
  }
  if(h.t<before){h.albedo=vec3(.62,.62,.6);h.gloss=.35;}
 }
}
void box(vec3 o,vec3 d,vec3 center,vec3 halfSize,vec3 albedo,inout Hit h){
 vec3 sd=vec3(d.x<0.?-1.:1.,d.y<0.?-1.:1.,d.z<0.?-1.:1.)*max(abs(d),vec3(1e-9));
 vec3 a=(center-halfSize-o)/sd,b=(center+halfSize-o)/sd,lo=min(a,b),hi=max(a,b);
 float n0=max(max(lo.x,lo.y),lo.z),f0=min(min(hi.x,hi.y),hi.z),t=n0>1e-4?n0:f0;
 if(n0<=f0&&t>1e-4&&t<h.t){vec3 q=(o+t*d-center)/halfSize,m=abs(q);
  h.t=t;h.n=m.x>m.y&&m.x>m.z?vec3(sign(q.x),0,0):m.y>m.z?vec3(0,sign(q.y),0):vec3(0,0,sign(q.z));h.albedo=albedo;h.gloss=.25;h.kind=3;}
}
void tube(vec3 o,vec3 d,vec3 a,vec3 b,float radius,vec3 albedo,inout Hit h){
 vec3 ba=b-a,oa=o-a;float baba=dot(ba,ba),bard=dot(ba,d),baoa=dot(ba,oa),rdoa=dot(d,oa),oaoa=dot(oa,oa);
 float aa=baba-bard*bard,bb=baba*rdoa-baoa*bard,cc=baba*oaoa-baoa*baoa-radius*radius*baba,disc=bb*bb-aa*cc;
 if(disc<0.||abs(aa)<1e-12)return;
 float t=(-bb-sqrt(disc))/aa,y=baoa+t*bard;
 if(y>0.&&y<baba&&t>1e-4&&t<h.t){vec3 p=o+t*d,c=a+ba*(y/baba);h.t=t;h.n=normalize(p-c);h.albedo=albedo;h.gloss=.05;h.kind=3;}
}
// Illustrative livery: white paint forward and aft, a dark switchband
// (avionics bay and camera ring) and a green vinyl main airframe.
vec3 livery(float z,out float gloss){
 gloss=.45;
 vec3 c=vec3(.68,.68,.65);
 if(z<Z_SWTOP&&z>Z_JOINT){c=vec3(.028,.03,.03);gloss=.3;}
 else if(z<=Z_JOINT&&z>Z_MAIN){c=vec3(.035,.075,.055);gloss=.55;}
 float seam=min(min(abs(z-Z_NOSE),abs(z-Z_SWTOP)),min(abs(z-Z_JOINT),abs(z-Z_MAIN)));
 return c*mix(.25,1.,smoothstep(.0008,.002,seam));
}
void railButtons(vec3 o,vec3 d,inout Hit h){
 const float angle=${LAUNCHER.railAzimuth.toFixed(10)};
 vec3 radial=vec3(cos(angle),sin(angle),0.);
 for(int j=0;j<2;j++){
  float z=j==0?${RAIL_BUTTONS.z[0].toFixed(8)}:${RAIL_BUTTONS.z[1].toFixed(8)};
  vec3 a=vec3(0,0,z)+radial*bodyR;
  tube(o,d,a,a+radial*.002,.00485,vec3(.03),h);
  tube(o,d,a+radial*.002,a+radial*.0077,.004,vec3(.03),h);
  tube(o,d,a+radial*.0077,a+radial*.0097,.00485,vec3(.03),h);
 }
}
void airframe(vec3 o,vec3 d,bool lower,bool upper,inout Hit h){
 float lo=lower?Z_TAIL:Z_JOINT,hi=upper?Z_TIP:Z_JOINT;
 float before=h.t;revolved(o,d,lo,hi,h);
 if(h.t<before&&h.kind==1){h.albedo=livery(bodyZ,h.gloss);}
 if(lower){disc(o,d,Z_TAIL,.058,vec3(.03),h);if(lower&&!upper){disc(o,d,Z_JOINT+.002,bodyR*.985,vec3(.012),h);}}
 if(upper&&!lower)disc(o,d,Z_JOINT,bodyR,vec3(.09,.09,.085),h);
 if(lower){fins(o,d,h);railButtons(o,d,h);}
}
void airbrakes(vec3 o,vec3 d,inout Hit h){
 if(abs(d.z)<1e-6)return;
 float t=(-height-o.z)/d.z;if(t<=1e-4||t>=h.t)return;
 vec2 hit=(o+t*d).xy;
 if(meshReady==1){
  if(length(hit)>.2||length(hit)<bodyR*.9)return;
  for(int j=0;j<3;j++){
   vec2 local=rz(vec3(hit,0.),-float(j)*2.*PI/3.).xy-vec2(.0575,0.);
   local=rz(vec3(local,0.),radians(108.879142)*deploy).xy+vec2(.0575,0.);
   bool inside=false;vec2 prev=leafOutline[23];
   for(int k=0;k<24;k++){vec2 cur=leafOutline[k];if((cur.y>local.y)!=(prev.y>local.y))if(local.x<(prev.x-cur.x)*(local.y-cur.y)/(prev.y-cur.y)+cur.x)inside=!inside;prev=cur;}
   if(inside){h.t=t;h.n=vec3(0,0,-sign(d.z));h.albedo=vec3(.5,.51,.52)*(1.-step(.985,fract(local.x*190.))*.2);h.gloss=.6;h.kind=3;}
  }
 } else if(length(hit)>bodyR&&length(hit)<mix(bodyR,leafR,deploy)){h.t=t;h.n=vec3(0,0,-sign(d.z));h.albedo=vec3(.5);h.gloss=.5;h.kind=3;}
}
float fabricT; // fabric transmittance of the last canopy hit
void drogue(vec3 o,vec3 d,inout Hit h){
 vec3 q=o-canopyCenter;
 vec3 lo=vec3(dot(q,canopyEx),dot(q,canopyEy),dot(q,canopyAxis)),ld=vec3(dot(d,canopyEx),dot(d,canopyEy),dot(d,canopyAxis));
 vec3 radii=vec3(canopyRadius,canopyRadius,canopyDepth),oc=lo/radii,rd=ld/radii;
 float a=dot(rd,rd),b=dot(oc,rd),disc=b*b-a*(dot(oc,oc)-1.);if(disc<0.)return;
 for(int s=0;s<2;s++){
  float t=(-b+(s==0?-1.:1.)*sqrt(disc))/a;vec3 p=lo+t*ld;
  if(t<=1e-4||t>=h.t)continue;
  float ang=atan(p.y,p.x),gf=ang/(2.*PI)*6.+6.,gore=fract(gf),edge=min(gore,1.-gore);
  float hem=-.15*canopyDepth;
  if(p.z<hem)continue;
  float rn=length(p.xy)/canopyRadius;
  vec3 n=normalize(p/(radii*radii));
  // Each gore billows between its radial seams; tilt the normal across it.
  vec3 tang=normalize(vec3(-p.y,p.x,0.)+vec3(1e-6,0,0));
  n=normalize(n+tang*(gore-.5)*1.6*smoothstep(.12,.35,rn));
  // Soft radial wrinkles near the loaded hem.
  n=normalize(n+tang*.06*sin(ang*54.+rn*9.)*smoothstep(.75,1.,rn));
  bool orange=mod(floor(gf),2.)<.5;
  vec3 c=orange?vec3(.5,.12,.03):vec3(.02,.02,.022);
  float tape=smoothstep(.012,.028,edge);
  float hemBand=smoothstep(hem+.02*canopyDepth,hem+.035*canopyDepth,p.z);
  float reinforce=min(tape,hemBand);
  c=mix(vec3(.035,.035,.034),c,reinforce);
  h.t=t;h.n=n.x*canopyEx+n.y*canopyEy+n.z*canopyAxis;h.albedo=c;h.gloss=.12;h.kind=2;
  // Ripstop transmits in proportion to dye; tapes and black gores barely.
  fabricT=(orange?.45:.05)*mix(.25,1.,reinforce);
 }
}
// Pad hardware in pad coordinates: a 1515 rail on a stand, blast plate.
uniform vec3 railFoot;uniform float railAngle;
void launcher(vec3 p,vec3 d,inout Hit h){
 if(altitude>900.)return;
 vec3 lo=rz(p-railFoot,-railAngle),ld=rz(d,-railAngle);
 box(lo,ld,vec3(0,0,3.2),vec3(.019,.019,2.75),vec3(.22,.22,.22),h);
 box(lo,ld,vec3(0,0,.3),vec3(.06,.06,.12),vec3(.05,.05,.05),h);
 for(int i=0;i<3;i++){float a=float(i)*2.1+.9;tube(lo,ld,vec3(0,0,.55),vec3(1.5*cos(a),1.5*sin(a),0.),.022,vec3(.12,.12,.11),h);}
 disc(p,d,.02,.42,vec3(.16,.155,.15),h);
}
// ------------------------------------------------------------ terrain
float footprintAt(float t){return pixelAngle*t;}
float rocketShadow(vec3 p){
 // Pad coordinates. Rocket as a capsule; only matters near the ground.
 if(altitude>350.)return 1.;
 vec3 a=vec3(worldXY,altitude)+toWorld(vec3(0,0,Z_TAIL),0.),b=vec3(worldXY,altitude)+toWorld(vec3(0,0,Z_TIP-.25),0.);
 vec3 u=b-a,w=p-a;float uu=dot(u,u),us=dot(u,SUN),ws=dot(w,SUN),uw=dot(u,w);
 float den=uu-us*us,s=clamp((uw-us*ws)/max(den,1e-9),0.,1.),tt=max(0.,dot(a+u*s-p,SUN));
 float dist=length(p+SUN*tt-(a+u*s));
 float shade=smoothstep(bodyR*.7,bodyR*1.6,dist);
 if(altitude<60.){vec3 r0=railFoot+vec3(0,0,.45),r1=railFoot+vec3(0,0,5.95);u=r1-r0;w=p-r0;uu=dot(u,u);us=dot(u,SUN);ws=dot(w,SUN);uw=dot(u,w);
  s=clamp((uw-us*ws)/max(uu-us*us,1e-9),0.,1.);tt=max(0.,dot(r0+u*s-p,SUN));shade*=smoothstep(.012,.04,length(p+SUN*tt-(r0+u*s)));}
 return mix(1.,shade,1.-smoothstep(150.,350.,altitude));
}
// Pattern of wavelength lambda fades to its mean once a pixel covers it.
float resolved(float fp,float lambda){return 1.-smoothstep(.2*lambda,.7*lambda,fp);}
float nz(vec2 q,int c){return textureLod(detailNoise,q/32.,0.)[c];} // unit-period noise
// Smooth fall-line direction from the undetailed height field.
vec2 fallLine(vec2 p,int g,out float slope){
 float span=g==0?FINE_SPAN:FAR_SPAN,e=g==0?48.:320.;
 vec2 u=p/span+.5,k=vec2(e/span,0.);
 vec2 gr=g==0?vec2(textureLod(fineHeight,u+k,0.).r-textureLod(fineHeight,u-k,0.).r,textureLod(fineHeight,u+k.yx,0.).r-textureLod(fineHeight,u-k.yx,0.).r)
            :vec2(textureLod(farHeight,u+k,0.).r-textureLod(farHeight,u-k,0.).r,textureLod(farHeight,u+k.yx,0.).r-textureLod(farHeight,u-k.yx,0.).r);
 gr/=2.*e;slope=length(gr);return slope>1e-5?-gr/slope:vec2(0,1);
}
// Noise stretched along the fall line without coordinate swirl: four fixed
// orientations blended by alignment with the local downslope direction.
// mode 0: value; 1: thin threads where the noise crosses its mean.
float flowNoise(vec2 p,vec2 down,float aniso,vec2 scale,int c,int mode){
 float sum=0.,wsum=0.;
 for(int i=0;i<4;i++){
  float a=float(i)*PI*.25;vec2 dir=vec2(cos(a),sin(a));
  // Where the fall line is ill-defined (flats, knobs) all orientations blend.
  float al=dot(dir,down),w=max(0.,2.*al*al-1.);w=mix(1.,w*w,aniso);
  if(w<1e-3)continue;
  vec2 q=vec2(dot(p,vec2(-dir.y,dir.x))/scale.x,dot(p,dir)/scale.y)+float(i)*vec2(.37,.61);
  float n=nz(q,c);
  if(mode==1)n=pow(1.-abs(2.*n-1.),5.)+.5*pow(1.-abs(2.*nz(q*vec2(2.3,1.7)+.41,(c+2)&3)-1.),8.);
  sum+=w*n;wsum+=w;
 }
 return sum/max(wsum,1e-4);
}
vec3 shadeTerrain(vec3 p,vec3 d,float t,int g){
 float fp=footprintAt(t);
 float span=g==0?FINE_SPAN:FAR_SPAN;vec2 uv=p.xy/span+.5;
 float lod=log2(max(fp/(span/GRID_N),1e-3));
 vec3 alb=g==0?textureLod(fineColour,uv,max(0.,lod)).rgb:textureLod(farColour,uv,max(0.,lod)).rgb;
 vec4 pr=g==0?textureLod(fineProps,uv,0.):textureLod(farProps,uv,0.);
 alb*=alb; // stored as sqrt(linear)
 float e=max(1.5,fp*1.5);
 float hx=surfaceH(p.xy+vec2(e,0),g,fp)-surfaceH(p.xy-vec2(e,0),g,fp),hy=surfaceH(p.xy+vec2(0,e),g,fp)-surfaceH(p.xy-vec2(0,e),g,fp);
 vec3 n=normalize(vec3(-hx,-hy,2.*e));
 // Surface colour detail below the texel: gravel mottling, varnish, washes, shrubs.
 vec4 m1=texture(detailNoise,p.xy/1900.),m2=texture(detailNoise,p.xy/310.+.37),m3=texture(detailNoise,p.xy/47.+.71);
 float rock=pr.b,wash=pr.a;
 alb*=1.+.22*(m1.r-.5)+.16*(m2.g-.5)*(1.-smoothstep(60.,400.,fp))+.14*(m3.b-.5)*(1.-smoothstep(4.,40.,fp));
 float sc=g==0?1.:5.;
 float slope;vec2 down=fallLine(p.xy,g,slope),across=vec2(-down.y,down.x);
 float aniso=smoothstep(.004,.02,slope);
 float fan=(1.-rock)*smoothstep(.003,.012,slope)*(1.-smoothstep(.16,.3,slope));
 // Alluvial surfaces of different ages: older bars carry dark reddish desert
 // varnish on packed pavement; younger surfaces are grey, loose and paler.
 // Surfaces are lobes elongated down-fan, cut by the younger channels.
 vec2 pw=p.xy+70.*sc*(vec2(nz(p.xy/(520.*sc),3),nz(p.xy/(520.*sc)+.5,0))-.5);
 float age=smoothstep(.46,.6,flowNoise(pw+180.*sc*(vec2(nz(p.xy/(2100.*sc),2),nz(p.xy/(2100.*sc)+.5,1))-.5),down,aniso,vec2(700.,2400.)*sc,1,0)+.2*(m2.b-.5)+.1*(nz(p.xy/(170.*sc),2)-.5)*resolved(fp,170.*sc));
 age*=1.-smoothstep(.2,.5,wash);
 alb*=mix(vec3(1.03,1.02,1.01),vec3(.87,.81,.75),age*(1.-rock));
 // Braided distributary threads running down the fans (pale sand in the
 // channels, darker bars between), aligned with the local fall line.
 float thread=flowNoise(pw,down,aniso,vec2(46.,380.)*sc,0,1);
 float braid=fan*(.35+.65*smoothstep(.15,.6,wash))*(1.-.6*age)*resolved(fp,46.*sc);
 alb=mix(alb*mix(1.,.93,braid),vec3(.46,.42,.35),clamp(thread,0.,1.)*braid*.55);
 // Main wash beds: sinuous pale ribbons with shrub-lined banks.
 float bed=smoothstep(.45,.75,wash+.35*(m2.r-.5));
 alb=mix(alb,vec3(.31,.26,.195),bed*.5*(1.-rock));
 alb*=1.-.18*smoothstep(.3,.45,wash)*(1.-bed)*(1.-rock);
 // Bedrock: gently dipping sedimentary beds of alternating resistance,
 // dark varnish streaks down steep faces, and pale talus in hollows.
 if(rock>.02){
  float layer=(p.z+dot(p.xy,vec2(.07,-.05)))/(19.*sc)+.9*(m1.b-.5);
  float beds=.5+.5*sin(6.2832*layer)*.65+.35*sin(6.2832*layer*2.63+1.7);
  float bedFade=resolved(fp,19.*sc);
  alb*=mix(1.,mix(.78,1.16,beds),bedFade*rock*smoothstep(.25,.6,slope));
   float streak=smoothstep(.55,.85,flowNoise(p.xy,down,aniso,vec2(9.,70.)*sc,1,0))*resolved(fp,9.*sc);
  alb*=1.-.3*streak*rock*smoothstep(.45,.8,slope);
  float scree=rock*smoothstep(.35,.55,slope)*(1.-smoothstep(.7,.95,slope))*smoothstep(.45,.65,m2.a);
  alb=mix(alb,alb*vec3(1.14,1.11,1.07),scree*.6);
 }
 // Creosote bush speckle; resolved as individual shrubs close to the camera.
 float shrubField=(1.-rock)*(1.-smoothstep(.7,.95,bed))*smoothstep(.2,.6,m1.g+.25);
 if(fp<6.){
  vec2 cell=floor(p.xy/5.5),f=fract(p.xy/5.5);
  float hsh=fract(sin(dot(cell,vec2(127.1,311.7)))*43758.5453),hsh2=fract(hsh*47.13);
  vec2 c=vec2(.25+.5*hsh,.25+.5*hsh2);float rad=(.05+.09*fract(hsh*91.7))*step(.3,hsh2*shrubField+.15);
  // Irregular crown: offset the distance by coarse noise.
  float lobes=.25*(texture(detailNoise,p.xy/3.1).a-.5);
  float dist=length(f-c)*(1.+lobes),sh=length(f-c+SUN.xy/max(SUN.z,.2)*.045)*(1.+lobes);
  float fade=1.-smoothstep(.25,1.5,fp);
  alb=mix(alb,alb*.62,fade*(1.-smoothstep(rad*.8,rad*1.5,sh))*.7);
  alb=mix(alb,vec3(.075,.078,.055),fade*.85*(1.-smoothstep(rad*.55,rad*1.05,dist)));
 }
 alb=mix(alb,alb*vec3(.72,.74,.68),shrubField*.35*smoothstep(.25,1.5,fp)*(1.-smoothstep(20.,200.,fp)));
 // Launch site: graded pad, access track, and staging area.
 if(g==0&&max(abs(p.x),abs(p.y))<3000.){
  float pad=1.-smoothstep(10.,12.,length(p.xy));
  alb=mix(alb,vec3(.33,.3,.25)*(1.+.2*(m3.r-.5)),pad*.8);
  float road=abs(p.y-(.14*p.x+22.*sin(p.x/260.)-24.));
  float track=(1.-smoothstep(2.4,3.6,road))*step(18.,p.x)*step(p.x,2900.);
  float ruts=1.-.25*(1.-smoothstep(.3,.6,abs(road-1.))) ;
  alb=mix(alb,vec3(.36,.31,.24)*ruts,track*.85);
  vec2 q=p.xy-vec2(210.,26.);float lot=(1.-smoothstep(28.,32.,abs(q.x)))*(1.-smoothstep(16.,20.,abs(q.y)));
  alb=mix(alb,vec3(.34,.31,.26),lot*.85);
  vec2 v=abs(mod(q+vec2(26.,14.),vec2(6.5,11.))-vec2(3.25,5.5));
  float car=(1.-smoothstep(.85,1.,v.x))*(1.-smoothstep(2.1,2.3,v.y))*lot*step(abs(q.y),13.)*step(q.x,20.);
  vec3 paint=mix(vec3(.55,.55,.53),vec3(.08,.1,.12),step(.5,fract(floor((q.x+26.)/6.5)*.618)));
  alb=mix(alb,paint,car);
  float tent=(1.-smoothstep(2.8,3.,max(abs(q.x-25.),abs(q.y-3.))))*lot;
  alb=mix(alb,vec3(.62,.62,.6),tent);
 }
 // Near field: desert pavement and pad gravel, clasts to cobbles. Each scale
 // fades out before it drops below a pixel, so it never aliases.
 if(fp<6.){
  float r0=resolved(fp,.07),r1=resolved(fp,.25),r3=resolved(fp,4.);
  float c0=nz(p.xy/.07,3),c1=nz(p.xy/.25+.3,0),c3=nz(p.xy/4.,2);
  float pebbles=(smoothstep(.6,.7,c0)-.8*smoothstep(.62,.7,1.-c0))*r0;
  float cobbles=(smoothstep(.64,.72,c1)-.6*smoothstep(.64,.72,1.-c1))*r1;
  alb*=1.+(.13*pebbles+.08*cobbles)*(1.-rock*.5)+.07*(c3-.5)*r3;
 }
 float sunVis=pr.r*rocketShadow(p);
 float z=p.z+SITE_ASL;
 vec3 E=sunLight(z)*max(0.,dot(n,SUN))*sunVis+SKY_E*pr.g*(.6+.4*n.z)+vec3(.06,.05,.04)*(.5-.5*n.z);
 return alb/PI*E;
}
vec3 ground(vec3 wo,vec3 d){
 vec3 o=wo+vec3(worldXY,altitude);
 float zAsl=o.z+SITE_ASL;
 vec3 sky=skyRadiance(d,zAsl);
 float h2=dot(d.xy,d.xy);
 if(d.z>0.&&o.z>farTop)return sky;
 float pixel=pixelAngle;
 float tHit;int g=-1;
 // Fine grid first, then the far grid from where the ray leaves the fine box.
 vec2 inv=1./max(abs(d.xy),vec2(1e-9));
 vec2 tf=(FINE_SPAN*.5-.5*FINE_SPAN/GRID_N-sign(d.xy)*o.xy)*inv;
 float tFine=min(tf.x,tf.y);
 vec2 tg=(FAR_SPAN*.5-FAR_SPAN/GRID_N-sign(d.xy)*o.xy)*inv;
 float tFar=min(min(tg.x,tg.y),4e5);
 float tStart=0.;
 if(d.z<0.&&o.z>fineTop)tStart=(o.z-fineTop)/-d.z;
 if(tStart<tFine&&traceGrid(0,o,d,tStart,tFine,pixel,tHit))g=0;
 else {
  float s2=max(tFine,0.);if(d.z<0.&&o.z>farTop)s2=max(s2,(o.z-farTop)/-d.z);
  if(s2<tFar&&traceGrid(1,o,d,s2,tFar,pixel,tHit))g=1;
 }
 vec3 c;float t;
 if(g<0){
  // Beyond the far grid: a hazy plain at the far-edge level, over the horizon.
  float base=-60.;float a=K_CURVE*h2,b=d.z,cc=o.z-base,disc=b*b-4.*a*cc;
  if(disc<0.||(b>0.&&cc>0.))return sky;
  t=(-b-sqrt(disc))/(2.*a);if(t<0.)return sky;
  vec3 p=o+t*d;
  c=vec3(.2,.17,.13)/PI*(sunLight(zAsl)*SUN.z*.9+SKY_E);
 } else {
  t=tHit;vec3 p=o+t*d;p.z=surfaceH(p.xy,g,pixel*t);
  c=shadeTerrain(p,d,t,g);
 }
 vec3 tr;vec3 ins=scatter(d,densityPath(zAsl,d.z+K_CURVE*h2*t,t),zAsl,tr);
 return c*tr+ins;
}
// ------------------------------------------------------------ shading
vec3 shadeObject(Hit h,vec3 d,vec3 wp){
 vec3 n=h.n,sun=sunLight(wp.z+altitude+SITE_ASL);
 float nl=dot(n,SUN);
 vec3 E=sun*max(0.,nl)+SKY_E*(.55+.45*n.z)+vec3(.13,.11,.085)*(.55-.45*n.z);
 vec3 c=h.albedo/PI*E;
 if(h.kind==2){
  // Thin ripstop, lit from both faces. Reflection uses the face toward the
  // viewer; light arriving on the far face is transmitted, tinted by the dye.
  vec3 nv=dot(n,d)<0.?n:-n;
  float lit=dot(nv,SUN);
  vec3 front=sun*max(0.,lit)+SKY_E*(.5+.5*nv.z)+vec3(.12,.1,.08)*(.5-.5*nv.z);
  vec3 back=sun*max(0.,-lit)+SKY_E*(.5-.5*nv.z)+vec3(.12,.1,.08)*(.5+.5*nv.z);
  vec3 tint=normalize(h.albedo+1e-4)*1.7;
  // Grazing views through the sheet lose more to reflection at the fibres.
  float view=sqrt(max(0.,-dot(d,nv)));
  c=h.albedo/PI*front*(1.-fabricT)+tint*fabricT/PI*back*.55*mix(.45,1.,view);
  // Nylon sheen at grazing view.
  float f=pow(1.-max(0.,dot(-d,nv)),4.);
  c+=skyRadiance(normalize(reflect(d,nv)+vec3(0,0,.05)),altitude+SITE_ASL)*f*.08;
  return c;
 }
 vec3 hv=normalize(SUN-d);
 float spec=pow(max(0.,dot(n,hv)),mix(8.,90.,h.gloss))*h.gloss*.06*step(0.,nl);
 vec3 r=reflect(d,n);
 c+=sun*spec+skyRadiance(normalize(vec3(r.xy,abs(r.z)+.05)),altitude+SITE_ASL)*mix(.02,.07,h.gloss)*(1.-max(0.,dot(-d,n))*.6);
 return c;
}
vec3 scene(vec3 o,vec3 d,float tm){
 Hit h;h.t=1e20;h.kind=0;h.gloss=0.;h.albedo=vec3(0);h.n=vec3(0,0,1);
 // Body-frame parts: the airframe (upper section only after separation),
 // camera housings and the airbrake petals.
 airframe(o,d,separated==0,true,h);
 for(int i=0;i<2;i++)box(o,d,housingCenter[i],housingHalf[i],vec3(.03,.032,.035),h);
 if(separated==0)airbrakes(o,d,h);
 vec3 wo=toWorld(o,tm),wd=toWorld(d,tm);
 if(h.kind>0)h.n=toWorld(h.n,tm);
 if(separated==1){
  // Separated booster on its own cord, in booster coordinates.
  vec3 q=wo-boosterTop;
  vec3 lo=vec3(dot(q,boosterX),dot(q,boosterY),dot(q,boosterZ)+Z_JOINT),ld=vec3(dot(wd,boosterX),dot(wd,boosterY),dot(wd,boosterZ));
  Hit b=h;b.kind=0;
  airframe(lo,ld,true,false,b);
  airbrakes(lo,ld,b);
  if(b.kind>0&&b.t<h.t){h=b;h.n=b.n.x*boosterX+b.n.y*boosterY+b.n.z*boosterZ;}
  drogue(wo,wd,h);
  for(int i=0;i<10;i++){if(i>=recoveryLineCount)break;tube(wo,wd,recoveryLines[2*i],recoveryLines[2*i+1],i<4?.0065:.0022,i<4?vec3(.3,.24,.13):vec3(.5,.48,.44),h);}
 }
 launcher(wo+vec3(worldXY,altitude),wd,h);
 if(h.kind>0)return shadeObject(h,wd,wo+h.t*wd);
 return ground(wo,wd);
}
`;
const CAPTURE =
  COMMON +
  `
uniform float eye;
void main(){
 vec2 q=(uv-.5)*sensor;float rr=length(q)*pixelPitch;
 float theta=lensB>0.?asin(clamp(lensB*rr/lensF,0.,1.))/lensB:length(q)/(circlePx*.5)*halfField;
 if(theta>halfField||length(q)>circlePx*.5){frag=vec4(0.);return;}
 vec2 v=length(q)>1e-7?q/length(q):vec2(0.);
 vec3 ray=vec3(eye*cos(theta),eye*v.x*sin(theta),v.y*sin(theta));
 vec3 origin=vec3(eye*(bodyR+stand),0.,0.);
 float tm=(q.y/sensor.y)*readout+(eye<0.?skew:0.);
 vec3 c=scene(origin,ray,tm);
 if(samples>1)c=(scene(origin,ray,tm-exposure*.5)+c+scene(origin,ray,tm+exposure*.5))/3.;
 // Mild lens relative illumination toward the edge of the field.
 c*=1.-.16*pow(theta/halfField,2.);
 frag=vec4(tonemap(c),1.);
}`;
const VIEW = `#version 300 es
precision highp float;
in vec2 uv;out vec4 frag;
uniform sampler2D camA,camB,received;
uniform vec2 sensor,screen;
uniform float circlePx,halfField,yaw,pitch,fov,roll,tilt,heading,pixelPitch,lensF,lensB;
uniform int mode,policy,earth,feed;
const float PI=3.14159265359;
vec3 rz(vec3 v,float a){float c=cos(a),s=sin(a);return vec3(c*v.x-s*v.y,s*v.x+c*v.y,v.z);}
vec3 ry(vec3 v,float a){float c=cos(a),s=sin(a);return vec3(c*v.x+s*v.z,v.y,-s*v.x+c*v.z);}
vec4 eye(vec3 d,float sign){
 float theta=acos(clamp(sign*d.x,-1.,1.));float r=length(d.yz);
 float radial=lensB>0.?lensF*sin(lensB*theta)/lensB/pixelPitch:theta/halfField*circlePx*.5;
 vec2 q=(r<1e-7?vec2(0.):vec2(sign*d.y,d.z)/r)*radial;
 if(theta>halfField||radial>circlePx*.5||any(greaterThan(abs(q),sensor*.5)))return vec4(0.);
 vec2 p=q/sensor+.5;vec4 c=sign>0.?texture(camA,p):texture(camB,p);
 return vec4(c.rgb,max(.0001,halfField-theta));
}
vec3 hatch(){return mix(vec3(.19,.16,.25),vec3(.32,.27,.4),step(.5,fract((gl_FragCoord.x+gl_FragCoord.y)/16.)));}
void main(){
 if(mode==2||mode==3){vec4 c=mode==2?texture(camA,uv):texture(camB,uv);frag=vec4(mix(vec3(.045,.052,.052),c.rgb,c.a),1.);return;}
 vec3 d;vec2 p=uv*2.-1.;
 if(mode==1||mode==7){float lon=p.x*PI,lat=p.y*PI*.5;d=vec3(cos(lat)*cos(lon),cos(lat)*sin(lon),sin(lat));}
 else {vec3 f=vec3(cos(pitch)*cos(yaw),cos(pitch)*sin(yaw),sin(pitch));vec3 r=vec3(-sin(yaw),cos(yaw),0.);vec3 u=cross(f,r);d=normalize(f+r*p.x*tan(fov*.5)+u*p.y*tan(fov*.5)*screen.y/screen.x);}
 if(earth==1)d=rz(ry(rz(d,-heading),-tilt),-roll);
 if(feed==1){vec2 q=vec2((atan(d.y,d.x)+PI)/(2.*PI),asin(clamp(d.z,-1.,1.))/PI+.5);frag=vec4(texture(received,q).rgb,1.);return;}
 vec4 a=eye(d,1.),b=eye(d,-1.);if(mode==4)b=vec4(0.);if(mode==5)a=vec4(0.);
 if(a.a+b.a<.00001){frag=vec4(hatch(),1.);return;}
 float w=a.a/(a.a+b.a);
 if(policy==0)w=a.a>=b.a?1.:0.;
 if(policy==2)w=a.a>0.?1.:0.;if(policy==3)w=b.a>0.?0.:1.;
 if(mode==4)w=1.;if(mode==5)w=0.;
 vec3 color=mix(b.rgb,a.rgb,w);
 if(mode==7){color=mix(vec3(.61,.48,.73),vec3(.20,.61,.51),w);if(a.a>0.&&b.a>0.)color=mix(color,vec3(.97,.88,.64),.48);}
 frag=vec4(color,1.);
}`;
export class CameraRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.gl = canvas.getContext("webgl2", {
      alpha: false,
      antialias: false,
      preserveDrawingBuffer: false,
    });
    if (!this.gl)
      throw Error("WebGL2 is required. Enable graphics acceleration.");
    const g = this.gl;
    this.programs = {
      capture: this.program(CAPTURE),
      view: this.program(VIEW),
    };
    this.cache = new Map();
    this.uploadTerrain(terrainMaps());
    this.buffer = g.createBuffer();
    g.bindBuffer(g.ARRAY_BUFFER, this.buffer);
    g.bufferData(
      g.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
      g.STATIC_DRAW,
    );
    this.a = this.target(1, 1);
    this.b = this.target(1, 1);
    // Received pairs never share storage with synthetic inspector captures.
    this.pairA = this.target(1, 1);
    this.pairB = this.target(1, 1);
    this.pairToken = null;
    this.pairReady = false;
    this.pairStaging = [null, null];
    this.video = this.texture(1, 1);
    this.videoSize = [1, 1];
    this.lastUpload = null;
    this.captured = null;
    this.metrics = { uploadMs: 0, drawMs: 0, captureMs: 0, terrainSeconds: terrainMaps().seconds };
  }
  uploadTerrain(maps) {
    const g = this.gl;
    // R32F filtering is optional in WebGL2; without it heights fall back to nearest.
    const linear = g.getExtension("OES_texture_float_linear") ? g.LINEAR : g.NEAREST;
    const make = (unit, setup) => {
      const t = g.createTexture();
      g.activeTexture(g.TEXTURE0 + unit);
      g.bindTexture(g.TEXTURE_2D, t);
      g.pixelStorei(g.UNPACK_ALIGNMENT, 1);
      setup(t);
      return { unit, t };
    };
    const params = (min, mag, wrap = g.CLAMP_TO_EDGE) => {
      g.texParameteri(g.TEXTURE_2D, g.TEXTURE_MIN_FILTER, min);
      g.texParameteri(g.TEXTURE_2D, g.TEXTURE_MAG_FILTER, mag);
      g.texParameteri(g.TEXTURE_2D, g.TEXTURE_WRAP_S, wrap);
      g.texParameteri(g.TEXTURE_2D, g.TEXTURE_WRAP_T, wrap);
    };
    this.terrain = {};
    let unit = 3;
    for (const kind of ["fine", "far"]) {
      const m = maps[kind], n = TERRAIN[kind].size;
      this.terrain[kind + "Height"] = make(unit++, () => {
        params(linear, linear);
        g.texImage2D(g.TEXTURE_2D, 0, g.R32F, n, n, 0, g.RED, g.FLOAT, m.heights);
      });
      this.terrain[kind + "Max"] = make(unit++, () => {
        params(g.NEAREST_MIPMAP_NEAREST, g.NEAREST);
        m.levels.forEach((level, i) => g.texImage2D(g.TEXTURE_2D, i, g.R32F, n >> i, n >> i, 0, g.RED, g.FLOAT, level));
        g.texParameteri(g.TEXTURE_2D, g.TEXTURE_MAX_LEVEL, m.levels.length - 1);
      });
      this.terrain[kind + "Colour"] = make(unit++, () => {
        params(g.LINEAR_MIPMAP_LINEAR, g.LINEAR);
        g.texImage2D(g.TEXTURE_2D, 0, g.RGBA, n, n, 0, g.RGBA, g.UNSIGNED_BYTE, m.colours);
        g.generateMipmap(g.TEXTURE_2D);
      });
      this.terrain[kind + "Props"] = make(unit++, () => {
        params(g.LINEAR, g.LINEAR);
        g.texImage2D(g.TEXTURE_2D, 0, g.RGBA, n, n, 0, g.RGBA, g.UNSIGNED_BYTE, m.props);
      });
    }
    this.terrain.detailNoise = make(unit++, () => {
      params(g.LINEAR_MIPMAP_LINEAR, g.LINEAR, g.REPEAT);
      const n = TERRAIN.noiseSize;
      g.texImage2D(g.TEXTURE_2D, 0, g.RGBA, n, n, 0, g.RGBA, g.UNSIGNED_BYTE, maps.noise);
      g.generateMipmap(g.TEXTURE_2D);
    });
    this.terrainTop = { fine: maps.fine.levels.at(-1)[0], far: maps.far.levels.at(-1)[0] };
    g.activeTexture(g.TEXTURE0);
  }
  program(fragment) {
    const g = this.gl,
      p = g.createProgram();
    for (const [type, src] of [
      [g.VERTEX_SHADER, VERTEX],
      [g.FRAGMENT_SHADER, fragment],
    ]) {
      const s = g.createShader(type);
      g.shaderSource(s, src);
      g.compileShader(s);
      if (!g.getShaderParameter(s, g.COMPILE_STATUS))
        throw Error(g.getShaderInfoLog(s));
      g.attachShader(p, s);
    }
    g.linkProgram(p);
    if (!g.getProgramParameter(p, g.LINK_STATUS))
      throw Error(g.getProgramInfoLog(p));
    return p;
  }
  texture(w, h) {
    const g = this.gl,
      t = g.createTexture();
    g.bindTexture(g.TEXTURE_2D, t);
    g.texParameteri(g.TEXTURE_2D, g.TEXTURE_MIN_FILTER, g.LINEAR);
    g.texParameteri(g.TEXTURE_2D, g.TEXTURE_MAG_FILTER, g.LINEAR);
    g.texParameteri(g.TEXTURE_2D, g.TEXTURE_WRAP_S, g.CLAMP_TO_EDGE);
    g.texParameteri(g.TEXTURE_2D, g.TEXTURE_WRAP_T, g.CLAMP_TO_EDGE);
    g.texImage2D(
      g.TEXTURE_2D,
      0,
      g.RGBA,
      w,
      h,
      0,
      g.RGBA,
      g.UNSIGNED_BYTE,
      null,
    );
    return t;
  }
  target(w, h) {
    const g = this.gl,
      tex = this.texture(w, h),
      fbo = g.createFramebuffer();
    g.bindFramebuffer(g.FRAMEBUFFER, fbo);
    g.framebufferTexture2D(
      g.FRAMEBUFFER,
      g.COLOR_ATTACHMENT0,
      g.TEXTURE_2D,
      tex,
      0,
    );
    g.bindFramebuffer(g.FRAMEBUFFER, null);
    return { tex, fbo, w, h };
  }
  resizeTarget(t, w, h) {
    if (t.w === w && t.h === h) return;
    const g = this.gl;
    g.bindTexture(g.TEXTURE_2D, t.tex);
    g.texImage2D(
      g.TEXTURE_2D,
      0,
      g.RGBA,
      w,
      h,
      0,
      g.RGBA,
      g.UNSIGNED_BYTE,
      null,
    );
    t.w = w;
    t.h = h;
  }
  use(p) {
    const g = this.gl;
    g.useProgram(p);
    g.bindBuffer(g.ARRAY_BUFFER, this.buffer);
    const loc = g.getAttribLocation(p, "position");
    g.enableVertexAttribArray(loc);
    g.vertexAttribPointer(loc, 2, g.FLOAT, false, 0, 0);
    this.current = p;
  }
  uniform(k, v, int = false) {
    const g = this.gl;
    let map = this.cache.get(this.current);
    if (!map) this.cache.set(this.current, (map = new Map()));
    if (!map.has(k)) map.set(k, g.getUniformLocation(this.current, k));
    const loc = map.get(k);
    if (Array.isArray(v)) g.uniform2f(loc, ...v);
    else if (v instanceof Float32Array) g.uniform2fv(loc, v);
    else if (int) g.uniform1i(loc, v);
    else g.uniform1f(loc, v);
  }
  vec3(name, points) {
    const g = this.gl;
    let map = this.cache.get(this.current);
    if (!map) this.cache.set(this.current, (map = new Map()));
    if (!map.has(name)) map.set(name, g.getUniformLocation(this.current, name));
    g.uniform3fv(map.get(name), new Float32Array(points.flat()));
  }
  common(s) {
    const p = PRESETS[s.preset];
    this.uniform("sensor", [p.w, p.h]);
    this.uniform("pixelPitch", p.pitch);
    this.uniform("lensF", p.focal || 1);
    this.uniform("lensB", p.distortionB || 0);
    this.uniform("circlePx", p.circle / p.pitch);
    this.uniform("halfField", (p.field * Math.PI) / 360);
    this.uniform("roll", (s.roll * Math.PI) / 180);
    this.uniform("tilt", (s.tilt * Math.PI) / 180);
    this.uniform("heading", ((s.heading || 0) * Math.PI) / 180);
  }
  capture(s, { native = false } = {}) {
    const started = performance.now(),
      g = this.gl,
      p = PRESETS[s.preset],
      scale = native ? 1 : Math.min(1, 1024 / p.w),
      w = Math.round(p.w * scale),
      h = Math.round(p.h * scale);
    this.use(this.programs.capture);
    for (const [name, { unit, t }] of Object.entries(this.terrain)) {
      g.activeTexture(g.TEXTURE0 + unit);
      g.bindTexture(g.TEXTURE_2D, t);
      this.uniform(name, unit, true);
    }
    g.activeTexture(g.TEXTURE0);
    this.uniform("fineTop", this.terrainTop.fine);
    this.uniform("farTop", this.terrainTop.far);
    this.common(s);
    this.uniform("worldXY", [s.east || 0, s.north || 0]);
    const R = bodyRadius(s);
    // Angular size of one pixel near the image centre; sets terrain detail.
    const pixelAngle = (p.efl ? p.pitch / p.efl : (p.field * Math.PI / 180) / (p.circle / p.pitch)) / scale;
    // Temporal exposure samples only when roll smear exceeds ~0.2 px.
    const smearPx = Math.abs(s.speed) * 2 * Math.PI * (s.exposure / 1000) / pixelAngle;
    for (const [k, v] of Object.entries({
      bodyR: R,
      stand: s.stand / 1000,
      height: s.height / 1000,
      leafR: 0.18,
      deploy: s.deploy,
      altitude: s.altitude,
      speed: s.speed,
      readout: p.global ? 0 : s.readout / 1000,
      skew: s.skew / 1000,
      exposure: s.exposure / 1000,
      flightTime: s.time,
      pixelAngle,
    }))
      this.uniform(k, v);
    this.uniform("samples", s.exposure > 0 && smearPx > 0.2 ? 3 : 1, true);
    // Airframe profile with per-vertex slope for smooth shading.
    const prof = airframeProfile(s), slopes = prof.map((_, i) => {
      const a = prof[Math.max(0, i - 1)], b = prof[Math.min(prof.length - 1, i + 1)];
      const seg = (u, v) => (v[1] - u[1]) / (v[0] - u[0] || -1e-9);
      if (i === 0) return seg(prof[0], prof[1]);
      if (i === prof.length - 1) return seg(prof[i - 1], prof[i]);
      // Keep shading breaks at real shape changes (cylinder ends).
      const s0 = seg(a, prof[i]), s1 = seg(prof[i], b);
      return Math.abs(s0 - s1) > 0.35 ? (Math.abs(s0) < Math.abs(s1) ? s0 : s1) : (s0 + s1) / 2;
    });
    this.vec3("profile[0]", prof.map(([z, r], i) => [z, r, slopes[i]]));
    this.uniform("profileCount", prof.length, true);
    this.uniform("profileMaxR", Math.max(...prof.map(([, r]) => r)) + 0.3);
    const boxes = cameraBoxes(s), pose = recoveryPose(s);
    this.vec3("finVertices[0]", finFaces(R).flat());
    this.vec3("housingCenter[0]", boxes.map((b) => b.center));
    this.vec3("housingHalf[0]", boxes.map((b) => b.half));
    this.uniform("separated", pose.separated ? 1 : 0, true);
    if (pose.separated) {
      this.vec3("boosterTop", [pose.booster.top]);
      this.vec3("boosterX", [pose.booster.x]);
      this.vec3("boosterY", [pose.booster.y]);
      this.vec3("boosterZ", [pose.booster.z]);
      this.vec3("canopyCenter", [pose.canopy]);
      this.vec3("canopyAxis", [pose.axis]);
      this.vec3("canopyEx", [pose.ex]);
      this.vec3("canopyEy", [pose.ey]);
      this.uniform("canopyRadius", Math.max(0.001, pose.radius));
      this.uniform("canopyDepth", Math.max(0.001, pose.depth));
      this.vec3("recoveryLines[0]", pose.lines.flat());
    }
    this.uniform("recoveryLineCount", pose.lines.length, true);
    // Rail on the rail-button side of the airframe, fixed to the ground.
    const railAz = LAUNCHER.railAzimuth, railR = R + 0.0097 + 0.019;
    this.vec3("railFoot", [[railR * Math.cos(railAz), railR * Math.sin(railAz), 0]]);
    this.uniform("railAngle", railAz);
    this.uniform("meshReady", s.mesh ? 1 : 0, true);
    if (s.mesh) this.uniform("leafOutline[0]", s.mesh);
    for (const [t, eye] of [
      [this.a, 1],
      [this.b, -1],
    ]) {
      this.resizeTarget(t, w, h);
      g.bindFramebuffer(g.FRAMEBUFFER, t.fbo);
      g.viewport(0, 0, w, h);
      this.uniform("eye", eye);
      g.drawArrays(g.TRIANGLES, 0, 6);
    }
    g.bindFramebuffer(g.FRAMEBUFFER, null);
    this.captured = { time: s.time, w, h, native };
    this.metrics.captureMs = performance.now() - started;
  }
  upload(video, token) {
    if (video.readyState < 2 || this.lastUpload === token) return;
    const g = this.gl,
      now = performance.now();
    g.activeTexture(g.TEXTURE2);
    g.bindTexture(g.TEXTURE_2D, this.video);
    g.pixelStorei(g.UNPACK_FLIP_Y_WEBGL, true);
    if (
      this.videoSize[0] !== video.videoWidth ||
      this.videoSize[1] !== video.videoHeight
    ) {
      g.texImage2D(
        g.TEXTURE_2D,
        0,
        g.RGBA,
        video.videoWidth,
        video.videoHeight,
        0,
        g.RGBA,
        g.UNSIGNED_BYTE,
        null,
      );
      this.videoSize = [video.videoWidth, video.videoHeight];
      g.texParameteri(g.TEXTURE_2D, g.TEXTURE_WRAP_S, g.REPEAT);
    }
    g.texSubImage2D(g.TEXTURE_2D, 0, 0, 0, g.RGBA, g.UNSIGNED_BYTE, video);
    g.pixelStorei(g.UNPACK_FLIP_Y_WEBGL, false);
    this.lastUpload = token;
    this.metrics.uploadMs = performance.now() - now;
  }
  uploadPair(sourceA, sourceB, frameToken) {
    if (this.pairReady && this.pairToken === frameToken) return false;
    const sources = [sourceA, sourceB];
    const sizes = sources.map(source => [source?.videoWidth || source?.naturalWidth || source?.displayWidth || source?.width, source?.videoHeight || source?.naturalHeight || source?.displayHeight || source?.height]);
    if (sources.some(source => !source || (typeof source.readyState === "number" && source.readyState < 2))) return false;
    if (sizes.some(([w,h]) => w !== 1552 || h !== 1552)) throw Error("Decoded camera pair must contain two 1552 × 1552 images.");
    const g=this.gl, started=performance.now();
    // Uploaded HTMLVideo/Canvas/Image sources use a top-left image origin.
    g.activeTexture(g.TEXTURE0);
    g.bindFramebuffer(g.FRAMEBUFFER, null);
    g.pixelStorei(g.UNPACK_FLIP_Y_WEBGL, true);
    try {
      [this.pairA,this.pairB].forEach((target,i)=>{
        this.resizeTarget(target,1552,1552);
        g.bindTexture(g.TEXTURE_2D,target.tex);
        // WebGL ignores UNPACK_FLIP_Y_WEBGL for ImageBitmap. Normalize
        // those via a canvas so all accepted source types have the same Y axis.
        let image = sources[i];
        if (typeof ImageBitmap !== "undefined" && image instanceof ImageBitmap) {
          const stage = this.pairStaging[i] ??= document.createElement("canvas");
          if (stage.width !== 1552 || stage.height !== 1552) stage.width = stage.height = 1552;
          stage.getContext("2d").drawImage(image, 0, 0);
          image = stage;
        }
        g.texSubImage2D(g.TEXTURE_2D,0,0,0,g.RGBA,g.UNSIGNED_BYTE,image);
      });
    } finally { g.pixelStorei(g.UNPACK_FLIP_Y_WEBGL, false); }
    this.pairToken=frameToken;
    this.pairReady=true;
    this.metrics.uploadMs=performance.now()-started;
    return true;
  }
  draw(
    s,
    {
      mode = 0,
      feed = 0,
      width = 1280,
      height = null,
      earth = s.earth,
      policy = s.policy,
    } = {},
  ) {
    const started = performance.now(),
      g = this.gl,
      p = PRESETS[feed === 2 ? "900" : s.preset];
    height ??= Math.round(
      width /
        (mode === 1 || mode === 7
          ? 2
          : mode === 2 || mode === 3
            ? p.w / p.h
            : 16 / 9),
    );
    if (this.canvas.width !== width) this.canvas.width = width;
    if (this.canvas.height !== height) this.canvas.height = height;
    g.bindFramebuffer(g.FRAMEBUFFER, null);
    g.viewport(0, 0, width, height);
    this.use(this.programs.view);
    this.common(feed === 2 ? { ...s, preset: "900" } : s);
    for (const [i, t, name] of [
      [0, feed === 2 ? this.pairA.tex : this.a.tex, "camA"],
      [1, feed === 2 ? this.pairB.tex : this.b.tex, "camB"],
      [2, this.video, "received"],
    ]) {
      g.activeTexture(g.TEXTURE0 + i);
      g.bindTexture(g.TEXTURE_2D, t);
      this.uniform(name, i, true);
    }
    this.uniform("screen", [width, height]);
    for (const k of ["yaw", "pitch", "fov"])
      this.uniform(k, (s[k] * Math.PI) / 180);
    for (const [k, v] of Object.entries({
      mode,
      feed,
      policy,
      earth: earth ? 1 : 0,
    }))
      this.uniform(k, v, true);
    g.drawArrays(g.TRIANGLES, 0, 6);
    this.metrics.drawMs = performance.now() - started;
  }
  copyTo(canvas, s, options) {
    this.draw(s, options);
    const c = canvas.getContext("2d");
    if (canvas.width !== this.canvas.width) canvas.width = this.canvas.width;
    if (canvas.height !== this.canvas.height)
      canvas.height = this.canvas.height;
    c.drawImage(this.canvas, 0, 0);
  }
}
