// An invented desert basin, in metres. Not a survey of the launch site.
// Height and colour share the same deterministic relief; both camera eyes use it.
export const TERRAIN = { size: 1024, span: 32000, ceiling: 2400 };
let cached;
const mix = (a,b,t) => a+(b-a)*t;
const smooth = (a,b,x) => { const t=Math.max(0,Math.min(1,(x-a)/(b-a))); return t*t*(3-2*t); };
function hash(x,y) {
  let n=Math.imul(x,374761393)+Math.imul(y,668265263);
  n=Math.imul(n^(n>>>13),1274126177);
  return ((n^(n>>>16))>>>0)/4294967295;
}
function noise(x,y) {
  const i=Math.floor(x),j=Math.floor(y),u=x-i,v=y-j,a=u*u*(3-2*u),b=v*v*(3-2*v);
  return mix(mix(hash(i,j),hash(i+1,j),a),mix(hash(i,j+1),hash(i+1,j+1),a),b);
}
function fbm(x,y) {
  let value=0,amp=.5;
  for(let i=0;i<5;i++){value+=amp*noise(x,y);x=x*2.03+17.8;y=y*2.03-9.2;amp*=.5;}
  return value;
}
export function terrainMaps() {
  if(cached)return cached;
  const {size,span,ceiling}=TERRAIN, heights=new Float32Array(size*size), colours=new Uint8Array(size*size*4);
  for(let j=0;j<size;j++)for(let i=0;i<size;i++) {
    const x=(i/(size-1)-.5)*span,y=(j/(size-1)-.5)*span,r=Math.hypot(x,y);
    const warp=900*(fbm(x/2800+60,y/2800)-.48);
    // Two broken ranges frame a broad alluvial basin, with a lower southern ridge.
    const east=Math.exp(-Math.pow((x-5800+.38*y+warp)/2100,2)-Math.pow(y/10500,2));
    const west=Math.exp(-Math.pow((x+6900-.25*y+warp)/1800,2)-Math.pow((y-1500)/12000,2));
    const south=Math.exp(-Math.pow((y+8600+.17*x+warp)/2300,2)-Math.pow(x/9500,2));
    const ridge=1-Math.abs(2*fbm(x/1100+13,y/1100+29)-1);
    const relief=fbm(x/530+31,y/530-7);
    const mountain=(east*1700+west*1400+south*1150)*(.27+.73*ridge*ridge);
    const apron=(65+150*fbm(x/2300,y/2300))*smooth(350,3200,r);
    const height=(mountain+apron+45*relief)*smooth(250,900,r)*smooth(16000,12500,r);
    heights[j*size+i]=height;
    const rock=smooth(210,850,height), variation=fbm(x/260+8,y/260+40), strata=noise(x/75,y/75)*.045;
    // Fine branching drainage colours on the fans, not a rectangular field grid.
    const wash=Math.pow(1-Math.abs(2*noise(x/600+2*noise(x/1600,y/1600),y/600)-1),16)*(1-rock);
    const base=[mix(.65,.48,rock),mix(.57,.45,rock),mix(.43,.36,rock)];
    for(let k=0;k<3;k++)colours[(j*size+i)*4+k]=Math.round(255*Math.min(1,base[k]+(variation-.5)*.17+strata+wash*.065));
    colours[(j*size+i)*4+3]=255;
  }
  // Float heights avoid quantisation steps on shallow slopes.
  cached={heights,colours};
  return cached;
}

export const TERRAIN_GLSL = `
uniform sampler2D terrainHeight,terrainColour;
float terrainH(vec2 p){return texture(terrainHeight,p/${TERRAIN.span.toFixed(1)}+.5).r;}
float hash2(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float groundNoise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(hash2(i),hash2(i+vec2(1,0)),f.x),mix(hash2(i+vec2(0,1)),hash2(i+1.),f.x),f.y);}
vec3 skyColour(vec3 d){
 float up=max(0.,d.z);
 vec3 sky=mix(vec3(.82,.83,.77),vec3(.24,.48,.64),pow(up,.48));
 vec3 sunDir=normalize(vec3(-.55,-.35,.7));
 float sun=max(0.,dot(d,sunDir));
 return sky+vec3(.16,.12,.06)*pow(sun,20.)+vec3(.7,.6,.4)*pow(sun,1300.);
}
vec3 ground(vec3 origin,vec3 d){
 vec3 sky=skyColour(d),o=origin+vec3(0,0,altitude);
 if(d.z>.4||(o.z>${TERRAIN.ceiling.toFixed(1)}&&d.z>=0.))return sky;
 float t=.01,previous=0.;
 if(o.z>${TERRAIN.ceiling.toFixed(1)})t=(o.z-${TERRAIN.ceiling.toFixed(1)})/-d.z;
 bool hit=false;
 for(int i=0;i<112;i++){
   vec3 p=o+t*d;
   if(t>48000.||p.z>${TERRAIN.ceiling.toFixed(1)}+1.||max(abs(p.x),abs(p.y))>15900.)break;
   float gap=p.z-terrainH(p.xy);
   if(gap<0.){hit=true;break;}
   previous=t;t+=max(5.,max(t*.055,gap*.65/(abs(d.z)+.9)));
 }
 if(!hit)return sky;
 for(int i=0;i<9;i++){float mid=(previous+t)*.5;vec3 p=o+mid*d;if(p.z>terrainH(p.xy))previous=mid;else t=mid;}
 vec3 p=o+t*d;
 float e=22.;
 vec3 n=normalize(vec3(terrainH(p.xy-vec2(e,0))-terrainH(p.xy+vec2(e,0)),terrainH(p.xy-vec2(0,e))-terrainH(p.xy+vec2(0,e)),2.*e));
 vec3 c=texture(terrainColour,p.xy/${TERRAIN.span.toFixed(1)}+.5).rgb;
 float detail=groundNoise(p.xy*.8)-.5;
 c+=detail*.045*(1.-smoothstep(2500.,6500.,t));
 float shrubs=step(.83,groundNoise(p.xy*.18))*(1.-smoothstep(700.,2200.,t));
 c=mix(c,vec3(.28,.32,.21),shrubs*.55);
 // One access road and a gravel staging area, at human scale.
 float road=abs(p.y-(.12*p.x+18.*sin(p.x/240.)));
 float roadMask=(1.-smoothstep(2.8,4.8,road))*step(22.,p.x)*step(p.x,2600.);
 c=mix(c,vec3(.73,.66,.53),roadMask*.75);
 float lot=(1.-smoothstep(12.,15.,abs(p.x-180.)))*(1.-smoothstep(23.,26.,abs(p.y-22.)));
 c=mix(c,vec3(.65,.62,.54),lot);
 vec2 vehicles=abs(mod(p.xy-vec2(174.,8.),vec2(9.,12.))-vec2(4.5,6.));
 float car=(1.-smoothstep(.8,1.1,vehicles.x))*(1.-smoothstep(1.7,2.1,vehicles.y))*lot;
 c=mix(c,vec3(.88,.88,.81),car);
 float pad=1.-smoothstep(2.4,2.7,length(p.xy));c=mix(c,vec3(.48,.48,.43),pad);
 c*=.62+.48*max(0.,dot(n,normalize(vec3(-.55,-.35,.7))));
 float fog=1.-exp(-t/23000.);
 return mix(c,skyColour(normalize(vec3(d.xy,.025))),min(.88,fog));
}
`;
