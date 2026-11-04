import {useId} from 'react';
import type {components} from './api.generated';
import {sample} from './state';

type Scenario = components['schemas']['Scenario'];
type Thermal = {profile:{md_m:number[]; bore_temperature_C:number[]; annulus_temperature_C:number[]}};
export type ColourRange = {minimum:number; maximum:number};

// Viridis anchors: increasing luminance with a fixed scale across compared runs.
const palette = [[68,1,84],[59,82,139],[33,145,140],[94,201,98],[253,231,37]];
export function temperatureColour(value:number | undefined, range:ColourRange):string {
  if (value == null || !Number.isFinite(value)) return '#b9c0c3';
  const t = Math.max(0,Math.min(1,(value-range.minimum)/(range.maximum-range.minimum)))*4;
  const i = Math.min(3,Math.floor(t)), f = t-i;
  return `rgb(${palette[i].map((v,j)=>Math.round(v+f*(palette[i+1][j]-v))).join(',')})`;
}

export function WellView({scenario,manifest,depth,onDepth,range}:{scenario:Scenario;manifest:Thermal|null;depth:number;onDepth:(depth:number)=>void;range:ColourRange}) {
  const pattern = useId().replaceAll(':','');
  const well = scenario.definition.well, total = well.bit_md_m;
  const y = (md:number)=>35+md/total*325;
  const outer = Math.max(...well.hole.flatMap(h=>[h.r_wall_m,...h.layers.map(l=>l.r_out_m)]));
  const scale = 68 / outer, centre = 113;
  const width = (r:number)=>r*scale;
  let top = 0;
  const segments = well.string.map(s=>{const start=top;top+=s.length_m;return {...s,top:start,bottom:top};});
  const p = manifest?.profile;
  const knots = Array.from(new Set([0,total,...segments.flatMap(s=>[s.top,s.bottom]),...well.hole.flatMap(h=>[h.md_top_m,h.md_bottom_m]),...Array.from({length:81},(_,i)=>total*i/80)])).sort((a,b)=>a-b);
  const containing = (md:number)=>segments.find(s=>md>=s.top&&md<=s.bottom)!;
  const clipped = p && [...p.bore_temperature_C,...p.annulus_temperature_C].some(v=>v<range.minimum||v>range.maximum);
  return <div className="well-wrap"><div className="mini-title">WELL SECTION <span>2D</span></div>
    <svg className="well" viewBox="0 0 240 400" role="img" aria-label="Actual serial pipe and casing intervals. MD downward; radial scale exaggerated." onClick={e=>{
      const rect=e.currentTarget.getBoundingClientRect();onDepth(Math.max(0,Math.min(total,((e.clientY-rect.top)/rect.height*400-35)/325*total)));
    }}><defs><pattern id={pattern} width="9" height="9" patternUnits="userSpaceOnUse"><path d="M-2 9L9 -2M6 12L12 6" stroke="#c7c9bf" strokeWidth=".7"/></pattern></defs>
      <rect x="37" y="35" width="152" height="325" fill="#f2f1ea"/><rect x="37" y="35" width="152" height="325" fill={`url(#${pattern})`}/>
      {well.hole.map(h=><g key={h.id}><title>{h.id}: {h.md_top_m}–{h.md_bottom_m} m MD</title>
        {[...h.layers].reverse().map((l,i)=><rect key={i} x={centre-width(l.r_out_m)} y={y(h.md_top_m)} width={2*width(l.r_out_m)} height={y(h.md_bottom_m)-y(h.md_top_m)} fill={l.conductivity_W_m_K>10?'#7e8d96':'#c3bda9'}/>)}
        <rect x={centre-width(h.r_wall_m)} y={y(h.md_top_m)} width={2*width(h.r_wall_m)} height={y(h.md_bottom_m)-y(h.md_top_m)} fill="#e8eded"/>
        {h.md_bottom_m<total&&<path d={`M37 ${y(h.md_bottom_m)}H190`} stroke="#8b928c" strokeDasharray="2 2"/>}
      </g>)}
      {knots.slice(0,-1).map((md,i)=>{const end=knots[i+1],mid=(md+end)/2,s=containing(mid),h=well.hole.find(h=>mid>=h.md_top_m&&mid<=h.md_bottom_m)!;
        const bore=s.pipe.layers[0].r_in_m, outside=s.pipe.layers.at(-1)!.r_out_m;
        const annulus=p?temperatureColour(sample(p.md_m,p.annulus_temperature_C,mid),range):'#dde9e7';
        return <g key={md}>
          <rect x={centre-width(h.r_wall_m)} y={y(md)} width={2*width(h.r_wall_m)} height={y(end)-y(md)+.2} fill={annulus}/>
          {[...s.pipe.layers].reverse().map((l,j)=><rect key={j} x={centre-width(l.r_out_m)} y={y(md)} width={2*width(l.r_out_m)} height={y(end)-y(md)+.2} fill={l.conductivity_W_m_K>10?'#71848c':'#e1ded2'}/>)}
          <rect x={centre-width(bore)} y={y(md)} width={2*width(bore)} height={y(end)-y(md)+.2} fill={p?temperatureColour(sample(p.md_m,p.bore_temperature_C,mid),range):'#dce9e7'}/>
        </g>;
      })}
      {segments.slice(1).map(s=><g key={s.id}><title>{s.id}: {s.pipe.name}, {s.top} m MD</title><path d={`M${centre-75} ${y(s.top)}h150`} stroke="#526976" strokeDasharray="4 2"/></g>)}
      {[0,.25,.5,.75,1].map(r=><g key={r}><path d={`M189 ${y(r*total)}h5`} stroke="#899e99"/><text x="197" y={y(r*total)+3} fontSize="9" fill="#536d65">{Math.round(r*total)}</text></g>)}
      <path d="M113 49v27m-3-4l3 4 3-4M93 77V50m-3 4l3-4 3 4" fill="none" stroke="#233c48" strokeWidth="1.6"/>
      <line x1="30" x2="192" y1={y(depth)} y2={y(depth)} stroke="#102d3f" strokeDasharray="3 3"/><circle cx="30" cy={y(depth)} r="3" fill="#102d3f"/>
      <text x="52" y="21" fontSize="9" fill="#536d65">ANNULUS</text><text x="119" y="21" fontSize="9" fill="#536d65">BORE</text><text x="55" y="389" fontSize="9" fill="#536d65">MD ↓ · radius exaggerated</text>
    </svg>
    {p?<><div className="thermal-key"><span>{range.minimum} °C</span><div style={{background:'linear-gradient(90deg,#440154,#3b528b,#21918c,#5ec962,#fde725)'}}/><span>{range.maximum} °C</span></div>{clipped&&<p className="clip-note">△ Values outside colour range are clipped</p>}</>:<div className="small muted">Material colours · no result</div>}
    <div className="well-caption">Bulk fluid temperatures<br/>Layer material colours; no computed rock field<br/>Decorative arrows · no time animation</div>
  </div>;
}
