// Measured Mei projection. +Z through camera A, +X right, +Y down in the rig.
// Texture coordinates retain source top-left orientation; no synthetic source.
const vertex = `#version 300 es
in vec2 position;out vec2 uv;void main(){uv=(position+1.)*.5;gl_Position=vec4(position,0.,1.);}`;
const fragment = `#version 300 es
precision highp float;
in vec2 uv;out vec4 color;
uniform sampler2D imageA,imageB;
uniform vec2 viewport;
uniform int mode,seam;
uniform float yaw,pitch,fov;
struct Camera{mat3 rotation;vec4 k;float skew;vec4 distortion;float xi;vec4 crop;vec2 outputSize;vec2 flips;float radius;float maxTheta;};
uniform Camera cameraA,cameraB;
const float PI=3.141592653589793;
vec3 missing(){float stripe=step(.5,fract((gl_FragCoord.x+gl_FragCoord.y)/20.));return mix(vec3(.12,.15,.16),vec3(.19,.21,.22),stripe);}
vec4 project(Camera c,sampler2D image,vec3 rig){
 vec3 d=normalize(c.rotation*rig);float theta=acos(clamp(d.z,-1.,1.));
 if(c.maxTheta>0.&&theta>c.maxTheta||d.z+c.xi<=1e-8||c.xi>1.&&d.z<=-1./c.xi)return vec4(0.);
 vec2 p=d.xy/(d.z+c.xi);float r2=dot(p,p);vec4 D=c.distortion;
 vec2 q=p*(1.+D.x*r2+D.y*r2*r2)+vec2(2.*D.z*p.x*p.y+D.w*(r2+2.*p.x*p.x),D.z*(r2+2.*p.y*p.y)+2.*D.w*p.x*p.y);
 vec2 pixel=vec2(c.k.x*q.x+c.skew*q.y+c.k.z,c.k.y*q.y+c.k.w);
 if(c.radius>0.&&length(pixel-c.k.zw)>c.radius)return vec4(0.);
 if(any(lessThan(pixel,c.crop.xy))||any(greaterThanEqual(pixel,c.crop.xy+c.crop.zw)))return vec4(0.);
 vec2 mapped=(pixel-c.crop.xy+.5)/c.crop.zw;
 mapped=mix(mapped,1.-mapped,c.flips);
 return vec4(texture(image,mapped).rgb,2.+d.z);
}
void main(){
 if(mode<2){vec2 p=vec2(uv.x,1.-uv.y);vec2 imageSize=vec2(textureSize(imageA,0));if(mode==1)imageSize=vec2(textureSize(imageB,0));
  float viewAspect=viewport.x/viewport.y,imageAspect=imageSize.x/imageSize.y;
  if(viewAspect>imageAspect)p.x=(p.x-.5)*viewAspect/imageAspect+.5;else p.y=(p.y-.5)*imageAspect/viewAspect+.5;
  if(any(lessThan(p,vec2(0.)))||any(greaterThan(p,vec2(1.)))){color=vec4(.035,.065,.072,1.);return;}
  color=vec4(mode==0?texture(imageA,p).rgb:texture(imageB,p).rgb,1.);return;
 }
 vec3 d;
 if(mode==3||mode==4){float lon=(uv.x*2.-1.)*PI,lat=(uv.y-.5)*PI;d=vec3(sin(lon)*cos(lat),-sin(lat),cos(lon)*cos(lat));}
 else{vec3 forward=vec3(sin(yaw)*cos(pitch),-sin(pitch),cos(yaw)*cos(pitch));vec3 right=vec3(cos(yaw),0.,-sin(yaw));vec3 up=cross(right,forward);vec2 p=uv*2.-1.;d=normalize(forward+right*p.x*tan(fov*.5)+up*p.y*tan(fov*.5)*viewport.y/viewport.x);}
 vec4 a=project(cameraA,imageA,d),b=project(cameraB,imageB,d);
 if(a.a==0.&&b.a==0.){color=vec4(missing(),1.);return;}
 float w=a.a>0.?1.:0.;
 if(a.a>0.&&b.a>0.){w=smoothstep(-.0872,.0872,a.a-b.a);if(seam==1)w=step(b.a,a.a);if(seam==2)w=1.;if(seam==3)w=0.;}
 vec3 rgb=mix(b.rgb,a.rgb,w);
 if(mode==4){rgb=mix(vec3(.64,.48,.78),vec3(.25,.69,.55),w);if(a.a>0.&&b.a>0.)rgb=mix(rgb,vec3(.94,.83,.44),.5);}
 color=vec4(rgb,1.);
}`;

export class Renderer {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = (this.gl = canvas.getContext("webgl2", {
      alpha: false,
      antialias: false,
    }));
    if (!gl) throw Error("WebGL2 is required for the ground viewer.");
    const shader = (kind, source) => {
      const s = gl.createShader(kind);
      gl.shaderSource(s, source);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
        throw Error(gl.getShaderInfoLog(s));
      return s;
    };
    const program = (this.program = gl.createProgram());
    gl.attachShader(program, shader(gl.VERTEX_SHADER, vertex));
    gl.attachShader(program, shader(gl.FRAGMENT_SHADER, fragment));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS))
      throw Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
      gl.STATIC_DRAW,
    );
    const position = gl.getAttribLocation(program, "position");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    this.raw = { A: this.texture(), B: this.texture() };
    this.pair = { A: this.texture(), B: this.texture() };
    this.mode = "a";
    this.seam = 0;
    this.yaw = 0;
    this.pitch = 0;
    this.fov = Math.PI / 2;
    this.calibration = null;
    this.draw();
  }
  texture() {
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
      1,
      1,
      0,
      g.RGBA,
      g.UNSIGNED_BYTE,
      new Uint8Array([10, 20, 23, 255]),
    );
    return { texture: t, width: 1, height: 1 };
  }
  upload(name, frame, paired = false) {
    const g = this.gl,
      t = (paired ? this.pair : this.raw)[name];
    g.bindTexture(g.TEXTURE_2D, t.texture);
    g.pixelStorei(g.UNPACK_FLIP_Y_WEBGL, false);
    g.texImage2D(g.TEXTURE_2D, 0, g.RGBA, g.RGBA, g.UNSIGNED_BYTE, frame);
    t.width = frame.displayWidth;
    t.height = frame.displayHeight;
  }
  uniform(name, value, integer = false) {
    const g = this.gl,
      l = g.getUniformLocation(this.program, name);
    if (l === null) return;
    if (Array.isArray(value)) {
      if (value.length === 9) g.uniformMatrix3fv(l, false, value);
      else g[`uniform${value.length}fv`](l, value);
    } else if (integer) g.uniform1i(l, value);
    else g.uniform1f(l, value);
  }
  setCamera(name, c) {
    const p = `camera${name}.`,
      R = c.R_camera_from_rig;
    this.uniform(p + "rotation", [
      R[0][0],
      R[1][0],
      R[2][0],
      R[0][1],
      R[1][1],
      R[2][1],
      R[0][2],
      R[1][2],
      R[2][2],
    ]);
    this.uniform(p + "k", [c.K[0][0], c.K[1][1], c.K[0][2], c.K[1][2]]);
    this.uniform(p + "skew", c.K[0][1]);
    this.uniform(p + "distortion", c.D);
    this.uniform(p + "xi", c.xi);
    this.uniform(p + "crop", c.crop);
    this.uniform(p + "outputSize", c.output_size);
    this.uniform(p + "flips", [
      Number(c.flip_x || false),
      Number(c.flip_y || false),
    ]);
    this.uniform(p + "radius", c.valid_radius_px ?? -1);
    this.uniform(
      p + "maxTheta",
      c.max_theta_deg ? (c.max_theta_deg * Math.PI) / 180 : -1,
    );
  }
  draw() {
    const g = this.gl,
      c = this.canvas;
    const ratio = Math.min(devicePixelRatio || 1, 2);
    const width = Math.round(c.clientWidth * ratio),
      height = Math.round(c.clientHeight * ratio);
    if (c.width !== width || c.height !== height) {
      c.width = width;
      c.height = height;
    }
    g.viewport(0, 0, c.width, c.height);
    g.useProgram(this.program);
    let mode = { a: 0, b: 1, perspective: 2, sphere: 3, mask: 4 }[this.mode];
    if (mode >= 2 && !this.calibration) mode = 0;
    const sources = mode < 2 ? this.raw : this.pair;
    for (const [index, name] of ["A", "B"].entries()) {
      g.activeTexture(g.TEXTURE0 + index);
      g.bindTexture(g.TEXTURE_2D, sources[name].texture);
      this.uniform("image" + name, index, true);
      if (this.calibration)
        this.setCamera(name, this.calibration.cameras[name]);
    }
    this.uniform("mode", mode, true);
    this.uniform("seam", this.seam, true);
    this.uniform("viewport", [c.width, c.height]);
    this.uniform("yaw", this.yaw);
    this.uniform("pitch", this.pitch);
    this.uniform("fov", this.fov);
    g.drawArrays(g.TRIANGLES, 0, 6);
  }
}
