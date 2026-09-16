import { SCENARIO } from "./scenario.js";
import { AIRFRAME, finFaces, cameraBoxes, recoveryPose } from "./scene.js";
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
const VERTEX = `#version 300 es
in vec2 position;out vec2 uv;void main(){uv=position*.5+.5;gl_Position=vec4(position,0.,1.);}`;
const COMMON = `#version 300 es
precision highp float;
in vec2 uv;out vec4 frag;
uniform vec2 screen,sensor;
uniform float circlePx,halfField,roll,tilt,bodyR,stand,height,leafR,deploy,altitude,speed,readout,skew,exposure;
uniform int meshReady;
uniform vec2 leafOutline[24];
uniform float flightTime,noseOffset,chuteOpen;
uniform float pixelPitch,lensF,lensB;
uniform vec3 finVertices[24],housingCenter[2],housingHalf[2];
uniform vec3 noseBase,noseAxis,canopyCenter,recoveryLines[30];
uniform float canopyRadius,canopyDepth;
uniform int recoveryLineCount;
const float PI=3.14159265359;
vec3 rz(vec3 v,float a){float c=cos(a),s=sin(a);return vec3(c*v.x-s*v.y,s*v.x+c*v.y,v.z);}
vec3 ry(vec3 v,float a){float c=cos(a),s=sin(a);return vec3(c*v.x+s*v.z,v.y,-s*v.x+c*v.z);}
vec3 toWorld(vec3 d,float tm){return ry(rz(d,roll+tm*speed*2.*PI),tilt);}
vec3 toBody(vec3 d){return rz(ry(d,-tilt),-roll);}
vec3 ground(vec3 origin,vec3 d){
 if(d.z>=-.002){float h=clamp(d.z,0.,1.);vec3 sky=mix(vec3(.79,.86,.88),vec3(.17,.43,.68),pow(h,.45));float sun=pow(max(dot(d,normalize(vec3(.4,.3,.6))),0.),400.);return sky+sun*.75;}
 float t=(-altitude-origin.z)/d.z;vec2 p=(origin+t*d).xy;
 vec2 grid=floor(p/65.);float hash=fract(sin(dot(grid,vec2(127.1,311.7)))*43758.54);
 vec3 c=mix(vec3(.29,.38,.20),vec3(.56,.49,.29),hash);
 float river=abs(p.y-190.*sin(p.x/650.)-40.*sin(p.x/110.));c=mix(c,vec3(.13,.32,.42),1.-smoothstep(14.,22.,river));
 vec2 road=abs(mod(p+125.,250.)-125.);float rd=1.-smoothstep(1.5,3.,min(road.x,road.y));c=mix(c,vec3(.62,.59,.49),rd*.7);
 float pad=1.-smoothstep(11.8,12.2,length(p));c=mix(c,vec3(.66,.66,.64),pad);if(length(p)<10.&&(abs(p.x)<.3||abs(p.y)<.3))c=vec3(.93);
 float haze=clamp(t/22000.,0.,.8);return mix(c,vec3(.74,.81,.83),haze);
}
// Intersect an opaque finite cylinder; both roots matter for back-facing rays.
void cylinder(vec3 o,vec3 d,inout float best,inout vec3 col){
 float a=dot(d.xy,d.xy),b=dot(o.xy,d.xy),c=dot(o.xy,o.xy)-bodyR*bodyR;
 float disc=b*b-a*c;if(a<1e-8||disc<0.)return;
 for(int i=0;i<2;i++){float t=(-b+(i==0?-1.:1.)*sqrt(disc))/a;float z=o.z+t*d.z;
 if(t>.0001&&t<best&&z>${AIRFRAME.bottom.toFixed(1)}&&z<${AIRFRAME.shoulder.toFixed(1)}){best=t;vec3 p=o+t*d;float az=atan(p.y,p.x);float stripe=step(.60,fract((az+PI)/(2.*PI)*8.));col=mix(vec3(.77,.81,.83),vec3(.13,.18,.22),stripe);col*=.7+.3*abs(dot(normalize(p.xy),normalize(vec2(1.,.5))));float band=1.-smoothstep(.007,.012,abs(mod(z+.075,.15)-.075));col=mix(col,vec3(.2,.24,.26),band*.4);}}
}
void triangleHit(vec3 o,vec3 d,vec3 a,vec3 b,vec3 c,inout float best,inout vec3 col){
 vec3 e=b-a,f=c-a,h=cross(d,f);float det=dot(e,h);if(abs(det)<1e-8)return;
 vec3 q=o-a;float u=dot(q,h)/det;vec3 v=cross(q,e);float w=dot(d,v)/det,t=dot(f,v)/det;
 if(u>=0.&&w>=0.&&u+w<=1.&&t>.0001&&t<best){best=t;col=vec3(.20,.29,.27)*(.65+.35*abs(dot(normalize(cross(e,f)),normalize(vec3(1.,.5,1.)))));}
}
void boxHit(vec3 o,vec3 d,vec3 center,vec3 halfSize,inout float best,inout vec3 col){
 vec3 safe=vec3(d.x<0.?-1.:1.,d.y<0.?-1.:1.,d.z<0.?-1.:1.)*max(abs(d),vec3(1e-8));
 vec3 a=(center-halfSize-o)/safe,b=(center+halfSize-o)/safe,lo=min(a,b),hi=max(a,b);
 float nearT=max(max(lo.x,lo.y),lo.z),farT=min(min(hi.x,hi.y),hi.z),t=nearT>.0001?nearT:farT;
 if(nearT<=farT&&t>.0001&&t<best){best=t;col=vec3(.16,.20,.21);}
}
void tetherHit(vec3 o,vec3 d,vec3 a,vec3 b,float radius,inout float best,inout vec3 col){
 vec3 ba=b-a,oa=o-a;float baba=dot(ba,ba),bard=dot(ba,d),baoa=dot(ba,oa),rdoa=dot(d,oa),oaoa=dot(oa,oa);
 float aa=baba-bard*bard,bb=baba*rdoa-baoa*bard,cc=baba*oaoa-baoa*baoa-radius*radius*baba,h=bb*bb-aa*cc;
 if(h>=0.&&abs(aa)>1e-9){float t=(-bb-sqrt(h))/aa,y=baoa+t*bard;if(y>0.&&y<baba&&t>.0001&&t<best){best=t;col=vec3(.88,.81,.62);}}
}
void noseHit(vec3 o,vec3 d,inout float best,inout vec3 col){
 vec3 q=o-noseBase;float z=dot(q,noseAxis),dz=dot(d,noseAxis);vec3 xy=q-z*noseAxis,dx=d-dz*noseAxis;
 float k=bodyR/${AIRFRAME.noseLength.toFixed(1)};float a=dot(dx,dx)-k*k*dz*dz,b=dot(xy,dx)+k*k*(${AIRFRAME.noseLength.toFixed(1)}-z)*dz,c=dot(xy,xy)-k*k*(${AIRFRAME.noseLength.toFixed(1)}-z)*(${AIRFRAME.noseLength.toFixed(1)}-z),disc=b*b-a*c;
 if(abs(a)>1e-8&&disc>=0.)for(int i=0;i<2;i++){float t=(-b+(i==0?-1.:1.)*sqrt(disc))/a,h=z+t*dz;if(t>.0001&&t<best&&h>=0.&&h<=${AIRFRAME.noseLength.toFixed(1)}){best=t;col=vec3(.73,.78,.76);}}
 // The separated nose's base is opaque too.
 if(abs(dz)>1e-8){float t=-z/dz;if(t>.0001&&t<best&&length(xy+t*dx)<bodyR){best=t;col=vec3(.27,.31,.30);}}
}
vec3 scene(vec3 o,vec3 d,float tm){
 float best=1e20;vec3 col=vec3(0.);cylinder(o,d,best,col);
 for(int i=0;i<8;i++)triangleHit(o,d,finVertices[i*3],finVertices[i*3+1],finVertices[i*3+2],best,col);
 for(int i=0;i<2;i++)boxHit(o,d,housingCenter[i],housingHalf[i],best,col);
 // Actual REV2 leaf silhouette: rotate about hinge, then around rocket axis.
 if(meshReady==1 && abs(d.z)>1e-6){
 float t=(-height-o.z)/d.z;
 if(t>.0001 && t<best){vec2 hit=(o+t*d).xy;
 if(length(hit)<.20 && length(hit)>bodyR*.9){
 for(int j=0;j<3;j++){
 vec2 local=rz(vec3(hit,0.),-float(j)*2.*PI/3.).xy-vec2(.0575,0.);
 local=rz(vec3(local,0.),radians(108.879142)*deploy).xy+vec2(.0575,0.);
 bool inside=false;vec2 prev=leafOutline[23];
 for(int k2=0;k2<24;k2++){vec2 cur=leafOutline[k2];if((cur.y>local.y)!=(prev.y>local.y))if(local.x<(prev.x-cur.x)*(local.y-cur.y)/(prev.y-cur.y)+cur.x)inside=!inside;prev=cur;}
 if(inside){best=t;col=mix(vec3(.70,.73,.75),vec3(.88,.89,.90),float(j)/2.);float marks=step(.985,fract(local.x*190.));col*=1.-marks*.18;}
 }}}
 } else if(meshReady==0 && abs(d.z)>1e-6){
 float t=(-height-o.z)/d.z;vec2 p=(o+t*d).xy;
 if(t>.0001&&t<best&&length(p)>bodyR&&length(p)<mix(bodyR,leafR,deploy)){best=t;col=vec3(.7,.7,.68);}
 }
 // Recovery geometry is world-oriented, independent of airframe roll.
 vec3 worldO=toWorld(o,tm),worldD=toWorld(d,tm);
 noseHit(worldO,worldD,best,col);
 if(chuteOpen>.01){
 vec3 radii=vec3(canopyRadius,canopyRadius,canopyDepth),oc=(worldO-canopyCenter)/radii,rd=worldD/radii;
 float a=dot(rd,rd),b=dot(oc,rd),disc=b*b-a*(dot(oc,oc)-1.);
 if(disc>=0.)for(int side=0;side<2;side++){float t=(-b+(side==0?-1.:1.)*sqrt(disc))/a;vec3 q=worldO+t*worldD-canopyCenter;
 if(t>.0001&&t<best&&q.z>-.18*canopyDepth){best=t;float sector=step(.5,fract((atan(q.y,q.x)+PI)/(2.*PI)*12.));col=mix(vec3(.84,.26,.17),vec3(.94,.91,.82),sector);col*=.8+.2*abs(normalize(q/radii).z);}}
 }
 for(int i=0;i<15;i++){if(i>=recoveryLineCount)break;tetherHit(worldO,worldD,recoveryLines[2*i],recoveryLines[2*i+1],.004,best,col);}
 if(best<1e19)return col;
 return ground(toWorld(o,tm),toWorld(d,tm));
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
 if(exposure>0.)c=(scene(origin,ray,tm-exposure*.5)+c+scene(origin,ray,tm+exposure*.5))/3.;
 frag=vec4(c,1.);
}`;
const VIEW = `#version 300 es
precision highp float;
in vec2 uv;out vec4 frag;
uniform sampler2D camA,camB,received;
uniform vec2 sensor,screen;
uniform float circlePx,halfField,yaw,pitch,fov,roll,tilt,pixelPitch,lensF,lensB;
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
 if(earth==1)d=rz(ry(d,-tilt),-roll);
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
    this.metrics = { uploadMs: 0, drawMs: 0 };
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
  }
  capture(s, { native = false } = {}) {
    const g = this.gl,
      p = PRESETS[s.preset],
      scale = native ? 1 : Math.min(1, 1024 / p.w),
      w = Math.round(p.w * scale),
      h = Math.round(p.h * scale);
    this.use(this.programs.capture);
    this.common(s);
    for (const [k, v] of Object.entries({
      bodyR: s.diameter / 2000,
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
      noseOffset: s.noseOffset,
      chuteOpen: s.chuteOpen,
    }))
      this.uniform(k, v);
    const pose = recoveryPose(s), boxes = cameraBoxes(s);
    const triples = (name, points) => g.uniform3fv(g.getUniformLocation(this.current, name), new Float32Array(points.flat()));
    triples("finVertices[0]", finFaces(s.diameter / 2000).flatMap(f => [f[0], f[1], f[2], f[0], f[2], f[3]]));
    triples("housingCenter[0]", boxes.map(b => b.center));
    triples("housingHalf[0]", boxes.map(b => b.half));
    triples("noseBase", [pose.noseBase]);
    triples("noseAxis", [pose.noseAxis]);
    triples("canopyCenter", [pose.canopy]);
    if (pose.lines.length) triples("recoveryLines[0]", pose.lines.flat());
    this.uniform("canopyRadius", Math.max(.001, pose.radius));
    this.uniform("canopyDepth", pose.depth);
    this.uniform("recoveryLineCount", pose.lines.length, true);
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
