import {sample} from './state.ts';

export type Vec3=[number,number,number];
export type Trajectory={schema_version:string;bit_md_m:number;md_m:number[];tvd_m:number[];easting_m:number[]|null;northing_m:number[]|null;station_md_m:number[];station_tvd_m:number[];station_easting_m:number[]|null;station_northing_m:number[]|null;fully_positioned:boolean;qualification:string;applicability:string;coordinate_sha256:string;interpolation:string};
export const plus=(a:Vec3,b:Vec3):Vec3=>[a[0]+b[0],a[1]+b[1],a[2]+b[2]];
export const minus=(a:Vec3,b:Vec3):Vec3=>[a[0]-b[0],a[1]-b[1],a[2]-b[2]];
export const scale=(a:Vec3,s:number):Vec3=>[a[0]*s,a[1]*s,a[2]*s];
export const dot=(a:Vec3,b:Vec3):number=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
export const cross=(a:Vec3,b:Vec3):Vec3=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
export const unit=(a:Vec3):Vec3=>scale(a,1/(Math.hypot(...a)||1));

export const DISPLAY_TRANSFORM='enu-y-up-v2';
// ENU is right-handed: east × north = up. With renderer Y up, north is -Z.
export function rendererPoint(e:number,n:number,d:number,origin:Vec3):Vec3{return [e-origin[0],-(d-origin[2]),n===origin[1]?0:origin[1]-n];}
export function holeAtMD<T extends {md_top_m:number;md_bottom_m:number}>(holes:T[],md:number):T|undefined {
  return holes.find((h,i)=>md>=h.md_top_m&&(md<h.md_bottom_m||(i===holes.length-1&&md===h.md_bottom_m)));
}
export function ringPath(inner:number,outer:number):string {
  const circle=(r:number)=>`M ${90+r} 90 a ${r} ${r} 0 1 0 ${-2*r} 0 a ${r} ${r} 0 1 0 ${2*r} 0`;
  return circle(outer)+' '+circle(inner);
}
export function pointAt(t:Trajectory,md:number):Vec3|undefined {
  if(!t.fully_positioned||!t.easting_m||!t.northing_m)return undefined;
  const e=sample(t.md_m,t.easting_m,md),n=sample(t.md_m,t.northing_m,md),d=sample(t.md_m,t.tvd_m,md);
  if(e==null||n==null||d==null)return undefined;
  return rendererPoint(e,n,d,[t.easting_m[0],t.northing_m[0],t.tvd_m[0]]);
}
export function frames(points:Vec3[]):{normal:Vec3;binormal:Vec3;tangent:Vec3}[]{
  let previous:Vec3|undefined;
  return points.map((p,i)=>{
    let direction=minus(points[Math.min(i+1,points.length-1)],points[Math.max(0,i-1)]);
    if(Math.hypot(...direction)<1.e-12)direction=minus(points[Math.min(i+1,points.length-1)],p);
    if(Math.hypot(...direction)<1.e-12)direction=minus(p,points[Math.max(0,i-1)]);
    const tangent=Math.hypot(...direction)<1.e-12?[0,-1,0] as Vec3:unit(direction);
    const reference=previous||([Math.abs(tangent[0])<.8?1:0,Math.abs(tangent[0])<.8?0:1,0] as Vec3);
    let projected=minus(reference,scale(tangent,dot(reference,tangent)));
    if(Math.hypot(...projected)<1.e-8){const axis:Vec3=Math.abs(tangent[2])<.8?[0,0,1]:[1,0,0];projected=minus(axis,scale(tangent,dot(axis,tangent)));}
    let normal=unit(projected);if(previous&&dot(normal,previous)<0)normal=scale(normal,-1);
    previous=normal;return {tangent,normal,binormal:unit(cross(tangent,normal))};
  });
}
export function pickedMD(length:number,uvY:number):number{return Math.max(0,Math.min(length,uvY*length));}
export function meshDepths(length:number,stations:number[],boundaries:number[],detail=160):number[]{
  return [...new Set([0,length,...stations,...boundaries,...Array.from({length:detail+1},(_,i)=>i/detail*length)])].filter(z=>z>=0&&z<=length).sort((a,b)=>a-b);
}
export function meshCost(depths:number[],well:{string:{length_m:number;pipe:{layers:unknown[]}}[];hole:{md_top_m:number;md_bottom_m:number;layers:unknown[]}[]}):{meshes:number;vertices:number;triangles:number;withinBudget:boolean}{
  let top=0,meshes=0,vertices=0,triangles=0;
  const add=(a:number,b:number,count:number)=>{const n=depths.filter(md=>md>=a&&md<=b).length;meshes+=count;vertices+=n*34*count;triangles+=(68*n-4)*count;};
  for(const segment of well.string){const bottom=top+segment.length_m;add(top,bottom,1+segment.pipe.layers.length);for(const hole of well.hole){const a=Math.max(top,hole.md_top_m),b=Math.min(bottom,hole.md_bottom_m);if(b>a)add(a,b,1);}top=bottom;}
  for(const hole of well.hole)add(hole.md_top_m,hole.md_bottom_m,hole.layers.length);
  return {meshes,vertices,triangles,withinBudget:meshes<=512&&vertices<=200000&&triangles<=300000};
}
export function fieldValue(profile:{md_m:number[];bore_temperature_C:number[];annulus_temperature_C:number[];annulus_wall_pressure_gauge_Pa:number[]},field:'temperature'|'pressure',conduit:'bore'|'annulus',md:number):number|undefined {
  if(field==='pressure'&&conduit==='bore')return undefined;
  return sample(profile.md_m,field==='pressure'?profile.annulus_wall_pressure_gauge_Pa:conduit==='bore'?profile.bore_temperature_C:profile.annulus_temperature_C,md);
}
