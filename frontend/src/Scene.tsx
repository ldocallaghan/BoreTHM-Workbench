import {useEffect,useMemo,useRef,useState} from 'react';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import type {components} from './api.generated';
import {fingerprint,sample} from './state';
import {temperatureColour} from './WellView';
import {DISPLAY_TRANSFORM,holeAtMD,ringPath,frames,pointAt,meshDepths,meshCost,pickedMD,fieldValue,plus,scale,type Trajectory,type Vec3} from './sceneState';
import './scene.css';

type Well=components['schemas']['Scenario']['definition']['well'];
export type SceneProfile={md_m:number[];bore_temperature_C:number[];annulus_temperature_C:number[];annulus_wall_pressure_gauge_Pa:number[]};
type Radial={md:number;radii:number[];temperatures:number[]};
type Model={renderer:THREE.WebGLRenderer;scene:THREE.Scene;camera:THREE.PerspectiveCamera;controls:OrbitControls;objects:THREE.Group;marker:THREE.Mesh<THREE.SphereGeometry,THREE.MeshBasicMaterial>;dirty:boolean;hasGeometry:boolean;fit:()=>void;measure:number[];benchmark?:{started:number;last:number;frames:number;intervals:number[];position:THREE.Vector3;target:THREE.Vector3;hidden:boolean}};
type MappedMesh=THREE.Mesh<THREE.BufferGeometry,THREE.MeshBasicMaterial>;
function release(group:THREE.Object3D){group.traverse(object=>{const mesh=object as THREE.Mesh;mesh.geometry?.dispose();if(mesh.material)(Array.isArray(mesh.material)?mesh.material:[mesh.material]).forEach(material=>material.dispose());});}

// MD is a vertex parameter; it does not depend on exaggerated radius or triangle index.
function tube(trajectory:Trajectory,depths:number[],inner:number,outer:number,factor:number,cutaway:boolean,role:string,colour:string):MappedMesh {
  const points=depths.map(md=>pointAt(trajectory,md)!);const basis=frames(points),positions:number[]=[],uv:number[]=[],indices:number[]=[];
  const sides=16,angles=sides+1,start=cutaway?Math.PI/4:0,sweep=cutaway?1.5*Math.PI:2*Math.PI;
  for(let k=0;k<depths.length;k++)for(const radius of [outer,inner])for(let j=0;j<=sides;j++){
    const theta=start+sweep*j/sides,offset=plus(scale(basis[k].normal,Math.cos(theta)*radius*factor),scale(basis[k].binormal,Math.sin(theta)*radius*factor));
    positions.push(...plus(points[k],offset));uv.push(j/sides,depths[k]/trajectory.bit_md_m);
  }
  const stride=2*angles;
  for(let k=0;k<depths.length-1;k++)for(let j=0;j<sides;j++)for(const side of [0,1]){
    const a=k*stride+side*angles+j,b=a+stride;
    indices.push(a,b,a+1,a+1,b,b+1);
  }
  // End rings and cut faces show actual concentric material thicknesses.
  for(const k of [0,depths.length-1])for(let j=0;j<sides;j++){const a=k*stride+j,b=a+angles;indices.push(a,a+1,b,a+1,b+1,b);}
  if(cutaway)for(let k=0;k<depths.length-1;k++)for(const j of [0,sides]){const a=k*stride+j,b=a+stride;indices.push(a,a+angles,b,b,a+angles,b+angles);}
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));geometry.setIndex(indices);
  geometry.setAttribute('color',new THREE.Float32BufferAttribute(new Float32Array(positions.length).fill(1),3));geometry.computeBoundingSphere();
  const mesh=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({color:colour,side:THREE.DoubleSide,vertexColors:role==='bore'||role==='annulus'}));
  mesh.userData={role,md:depths};return mesh;
}

function radialSection(t:Trajectory,field:Radial):MappedMesh {
  const centre=pointAt(t,field.md)!;const delta=Math.max(.001,t.bit_md_m/10000),a=pointAt(t,Math.max(0,field.md-delta))!,b=pointAt(t,Math.min(t.bit_md_m,field.md+delta))!;
  const basis=frames([a,centre,b])[1],positions:number[]=[],indices:number[]=[];const sides=32;
  field.radii.forEach(r=>{for(let j=0;j<=sides;j++){const theta=j/sides*Math.PI*2;positions.push(...plus(centre,plus(scale(basis.normal,r*Math.cos(theta)),scale(basis.binormal,r*Math.sin(theta)))));}});
  for(let i=0;i<field.radii.length-1;i++)for(let j=0;j<sides;j++){const a=i*(sides+1)+j,b=a+sides+1;indices.push(a,b,a+1,a+1,b,b+1);}
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute('color',new THREE.Float32BufferAttribute(new Float32Array(positions.length).fill(1),3));geometry.setIndex(indices);geometry.computeBoundingSphere();
  const mesh=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({side:THREE.DoubleSide,vertexColors:true}));mesh.userData={role:'radial',sides,md:field.md};return mesh;
}

export function Scene({trajectory,well,profile,depth,onDepth,range,identity,radial,tool,pressureBasis='basis unavailable',alternatives=[]}:{trajectory:Trajectory;well?:Well;profile?:SceneProfile;depth:number;onDepth:(md:number)=>void;range:{minimum:number;maximum:number};identity:string;pressureBasis?:string;radial?:Radial;tool?:{md:number;temperature_C:number;radius_m:number};alternatives?:{label:string;well:Well;trajectory:Trajectory;profile:SceneProfile;identity:string;pressureBasis?:string}[]}){
  const host=useRef<HTMLDivElement>(null),model=useRef<Model|null>(null),onDepthRef=useRef(onDepth);onDepthRef.current=onDepth;
  const [status,setStatus]=useState('checking'),[epoch,setEpoch]=useState(0),[factor,setFactor]=useState(600),[cutaway,setCutaway]=useState(true),[field,setField]=useState<'temperature'|'pressure'>('temperature');
  const [conduit,setConduit]=useState<'both'|'bore'|'annulus'>('both'),[showPipe,setShowPipe]=useState(true),[showCasing,setShowCasing]=useState(true),[lockCamera,setLockCamera]=useState(true),[selection,setSelection]=useState('current');
  const [pressureRange,setPressureRange]=useState({minimum:0,maximum:50}),[metrics,setMetrics]=useState(''),[hover,setHover]=useState<number|null>(null);
  const [benchmarking,setBenchmarking]=useState(false),[renderReport,setRenderReport]=useState<Record<string,unknown>|null>(null);
  const chosen=alternatives.find(a=>a.identity===selection),t=chosen?.trajectory||trajectory,w=chosen?.well||well,p=chosen?.profile||profile,runIdentity=chosen?.identity||identity;
  const selectedMD=Math.max(0,Math.min(t.bit_md_m,tool?.md??depth));
  const current=useRef({t,w,p,selectedMD,radial,tool,factor,field,range,pressureRange,conduit,showPipe,showCasing,lockCamera});current.current={t,w,p,selectedMD,radial,tool,factor,field,range,pressureRange,conduit,showPipe,showCasing,lockCamera};
  const geometryKey=fingerprint({well:w,survey:[t.station_md_m,t.station_easting_m,t.station_northing_m,t.station_tvd_m],factor,cutaway,radial:radial?[radial.md,radial.radii]:null});
  useEffect(()=>{
    if(!t.fully_positioned){setStatus('incomplete');return;}
    const element=host.current!;let renderer:THREE.WebGLRenderer;
    const canvas=document.createElement('canvas');const context=canvas.getContext('webgl2',{antialias:true});
    if(!context){setStatus('unavailable');return;}
    try{renderer=new THREE.WebGLRenderer({canvas,context,antialias:true,logarithmicDepthBuffer:true});}catch{setStatus('unavailable');return;}
    renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.setClearColor('#edf3f1');canvas.setAttribute('aria-label','3D well trajectory; numeric depth controls and 2D charts provide equivalent values');canvas.tabIndex=0;element.appendChild(canvas);
    const scene=new THREE.Scene();scene.add(new THREE.AxesHelper(current.current.t.bit_md_m/8));const camera=new THREE.PerspectiveCamera(38,1,.01,1.e6),controls=new OrbitControls(camera,canvas);controls.enableDamping=false;
    const objects=new THREE.Group();scene.add(objects);const marker=new THREE.Mesh(new THREE.SphereGeometry(1,16,12),new THREE.MeshBasicMaterial({color:'#cf8149',depthTest:false}));marker.renderOrder=10;scene.add(marker);
    const state:Model={renderer,scene,camera,controls,objects,marker,dirty:true,hasGeometry:false,measure:[],fit:()=>{
      const bounds=new THREE.Box3().setFromObject(objects);if(bounds.isEmpty())return;const centre=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3()).length()||1;
      controls.target.copy(centre);const distance=size/2/Math.sin(THREE.MathUtils.degToRad(camera.fov/2))/Math.min(1,camera.aspect)*1.15;camera.position.copy(centre).add(new THREE.Vector3(.85,.25,.9).normalize().multiplyScalar(distance));camera.near=Math.max(.001,size/100000);camera.far=Math.max(1000,size*30);camera.updateProjectionMatrix();controls.update();state.dirty=true;
    }};model.current=state;
    const resize=()=>{const width=element.clientWidth,height=element.clientHeight;renderer.setSize(width,height);camera.aspect=width/Math.max(height,1);camera.updateProjectionMatrix();state.dirty=true;};
    const observer=new ResizeObserver(resize);observer.observe(element);resize();
    const changed=()=>{state.dirty=true;};controls.addEventListener('change',changed);
    const lost=(event:Event)=>{event.preventDefault();if(state.benchmark){camera.position.copy(state.benchmark.position);controls.target.copy(state.benchmark.target);state.benchmark=undefined;controls.enabled=true;setBenchmarking(false);}setStatus('lost');};canvas.addEventListener('webglcontextlost',lost);
    const restored=()=>{setStatus('ready');state.dirty=true;};canvas.addEventListener('webglcontextrestored',restored);
    const raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();raycaster.params.Line.threshold=current.current.t.bit_md_m/150;let down:[number,number]|null=null;
    const pick=(event:PointerEvent,pin:boolean)=>{
      const rect=canvas.getBoundingClientRect();pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);raycaster.setFromCamera(pointer,camera);
      const hits=raycaster.intersectObjects(objects.children,true);const hit=hits.find(hit=>hit.object.userData.role!=='radial'&&hit.uv);
      let md=hit?.uv?pickedMD(current.current.t.bit_md_m,hit.uv.y):null;
      if(md==null){const line=hits.find(hit=>hit.object.userData.role==='trajectory');if(line&&line.index!=null){const values=line.object.userData.md as number[],i=line.index;const a=pointAt(current.current.t,values[i])!,b=pointAt(current.current.t,values[i+1])!;const fraction=Math.min(1,Math.max(0,line.point.distanceTo(new THREE.Vector3(...a))/Math.max(1.e-12,new THREE.Vector3(...a).distanceTo(new THREE.Vector3(...b)))));md=values[i]+fraction*(values[i+1]-values[i]);}}
      if(pin&&md!=null)onDepthRef.current(md);else if(!pin)setHover(md);
    };
    const pointerDown=(event:PointerEvent)=>{down=[event.clientX,event.clientY];};
    const pointerUp=(event:PointerEvent)=>{if(down&&Math.hypot(event.clientX-down[0],event.clientY-down[1])<4)pick(event,true);down=null;};
    const move=(event:PointerEvent)=>{if(!down)pick(event,false);};const leave=()=>setHover(null);
    canvas.addEventListener('pointerdown',pointerDown);canvas.addEventListener('pointerup',pointerUp);canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerleave',leave);
    let frame=0,stopped=false,count=0;
    const visibility=()=>{if(state.benchmark&&document.hidden)state.benchmark.hidden=true;};document.addEventListener('visibilitychange',visibility);
    const draw=()=>{if(stopped)return;frame=requestAnimationFrame(draw);const b=state.benchmark;const now=performance.now();
      if(b){
        if(now-b.started>=5000){const duration=now-b.started,intervals=[...b.intervals].sort((a,c)=>a-c),fps=b.frames*1000/duration;const report={duration_ms:duration,drawn_frames:b.frames,mean_fps:fps,frame_interval_p95_ms:intervals[Math.floor(.95*intervals.length)]??null,triangles:renderer.info.render.triangles,visibility_interrupted:b.hidden,target_fps:30,passed:!b.hidden&&fps>=30,qualification:'Five-second rotating scene in this browser/device; no cross-device guarantee'};setRenderReport(report);canvas.dataset.benchmark=JSON.stringify(report);camera.position.copy(b.position);controls.target.copy(b.target);controls.enabled=true;controls.update();state.benchmark=undefined;setBenchmarking(false);state.dirty=true;}
        else{if(b.last)b.intervals.push(now-b.last);b.last=now;camera.position.copy(b.position).sub(b.target).applyAxisAngle(new THREE.Vector3(0,1,0),(now-b.started)*.0002).add(b.target);controls.update();state.dirty=true;}
      }
      if(state.dirty&&!document.hidden){const started=performance.now();renderer.render(scene,camera);const elapsed=performance.now()-started;state.measure.push(elapsed);if(state.measure.length>240)state.measure.shift();state.dirty=false;if(state.benchmark)state.benchmark.frames++;canvas.dataset.camera=JSON.stringify([...camera.position.toArray(),...controls.target.toArray()].map(v=>+v.toFixed(6)));canvas.dataset.geometryIds=objects.children.map(o=>(o as THREE.Mesh).geometry?.uuid||'').join(',');canvas.dataset.pinnedMd=String(current.current.selectedMD);canvas.dataset.renderMs=elapsed.toFixed(3);canvas.dataset.frames=String(++count);canvas.dataset.triangles=String(renderer.info.render.triangles);}};draw();setStatus('ready');
    return()=>{stopped=true;cancelAnimationFrame(frame);document.removeEventListener('visibilitychange',visibility);observer.disconnect();controls.removeEventListener('change',changed);controls.dispose();canvas.removeEventListener('webglcontextlost',lost);canvas.removeEventListener('webglcontextrestored',restored);canvas.removeEventListener('pointerdown',pointerDown);canvas.removeEventListener('pointerup',pointerUp);canvas.removeEventListener('pointermove',move);canvas.removeEventListener('pointerleave',leave);release(scene);renderer.dispose();renderer.forceContextLoss();canvas.remove();model.current=null;};
  },[epoch,t.fully_positioned]);
  useEffect(()=>{
    const state=model.current;if(!state||!['ready','oversized'].includes(status))return;release(state.objects);state.objects.clear();
    const boundaries:number[]=[];let end=0;w?.string.forEach(s=>{end+=s.length_m;boundaries.push(end);});w?.hole.forEach(h=>boundaries.push(h.md_top_m,h.md_bottom_m));
    const depths=meshDepths(t.bit_md_m,t.station_md_m,boundaries);
    if(w&&!meshCost(depths,w).withinBudget){setStatus('oversized');state.marker.visible=false;state.dirty=true;return;}
    if(status==='oversized')setStatus('ready');
    const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(depths.map(md=>new THREE.Vector3(...pointAt(t,md)!))),new THREE.LineBasicMaterial({color:'#6c8c83'}));line.userData={role:'trajectory',md:depths};state.objects.add(line);
    if(w){
      let top=0;
      for(const segment of w.string){const bottom=top+segment.length_m;const local=depths.filter(md=>md>=top&&md<=bottom);
        state.objects.add(tube(t,local,0,segment.pipe.layers[0].r_in_m,factor,cutaway,'bore','#fff'));
        for(const layer of segment.pipe.layers)state.objects.add(tube(t,local,layer.r_in_m,layer.r_out_m,factor,cutaway,'pipe',layer.conductivity_W_m_K>10?'#6f8492':'#c8bda2'));
        for(const hole of w.hole){const a=Math.max(top,hole.md_top_m),b=Math.min(bottom,hole.md_bottom_m);if(b<=a)continue;
          state.objects.add(tube(t,depths.filter(md=>md>=a&&md<=b),segment.pipe.layers.at(-1)!.r_out_m,hole.r_wall_m,factor,cutaway,'annulus','#fff'));
        }top=bottom;
      }
      for(const hole of w.hole)for(const layer of hole.layers)state.objects.add(tube(t,depths.filter(md=>md>=hole.md_top_m&&md<=hole.md_bottom_m),layer.r_in_m,layer.r_out_m,factor,cutaway,'casing',layer.conductivity_W_m_K>10?'#93a5ac':'#d3c8b1'));
    }
    if(radial)state.objects.add(radialSection(t,radial));
    if(!state.hasGeometry||!lockCamera)state.fit();state.hasGeometry=true;state.dirty=true;
  },[geometryKey,status]);
  useEffect(()=>{
    const state=model.current;if(!state||status!=='ready')return;
    const palette=field==='pressure'?pressureRange:range;
    state.objects.children.forEach(object=>{
      const mesh=object as MappedMesh,role=mesh.userData.role;
      if(role==='pipe'){mesh.visible=showPipe;return;}if(role==='casing'){mesh.visible=showCasing;return;}
      if(role==='bore'||role==='annulus'){
        mesh.visible=conduit==='both'||conduit===role;
        const attr=mesh.geometry.getAttribute('color'),uv=mesh.geometry.getAttribute('uv');mesh.material.color.set('#fff');
        for(let i=0;i<attr.count;i++){const value=p?fieldValue(p,field,role,pickedMD(t.bit_md_m,uv.getY(i))):undefined;const c=new THREE.Color(temperatureColour(value==null?undefined:field==='pressure'?value/1e6:value,palette));attr.setXYZ(i,c.r,c.g,c.b);}attr.needsUpdate=true;
      }else if(role==='radial'&&radial){const attr=mesh.geometry.getAttribute('color');for(let i=0;i<attr.count;i++){const value=radial.temperatures[Math.floor(i/(mesh.userData.sides+1))],c=new THREE.Color(temperatureColour(value,range));attr.setXYZ(i,c.r,c.g,c.b);}attr.needsUpdate=true;}
    });
    const point=pointAt(t,selectedMD);state.marker.visible=!!point&&!radial;
    if(point){state.marker.position.set(...point);state.marker.scale.setScalar(tool?Math.max(tool.radius_m*factor,t.bit_md_m/1000):Math.max(t.bit_md_m/350,1));state.marker.material.color.set(tool?temperatureColour(tool.temperature_C,range):'#d18a52');}
    state.dirty=true;
  },[geometryKey,status,p,selectedMD,radial,tool,field,range,pressureRange,conduit,showPipe,showCasing]);
  const cameraAction=(action:string)=>{const state=model.current;if(!state)return;const {camera,controls}=state;
    if(action==='fit'||action==='reset'){state.fit();return;}
    if(action==='focus'||action==='wall'){const centre=pointAt(t,selectedMD);if(!centre)return;controls.target.set(...centre);const distance=radial?(action==='wall'?radial.radii[0]*8:Math.max(...radial.radii)*3):Math.max(2,(holeAtMD(w?.hole||[],selectedMD)?.r_wall_m||.1)*factor*12);if(radial){const a=pointAt(t,Math.max(0,selectedMD-.01))!,b=pointAt(t,Math.min(t.bit_md_m,selectedMD+.01))!,basis=frames([a,b])[0];camera.position.copy(controls.target).add(new THREE.Vector3(...plus(scale(basis.tangent,distance),scale(basis.normal,distance*.1))));}else camera.position.copy(controls.target).add(new THREE.Vector3(distance,distance*.3,distance));camera.near=Math.max(.001,distance/10000);camera.updateProjectionMatrix();}
    else{const offset=camera.position.clone().sub(controls.target),spherical=new THREE.Spherical().setFromVector3(offset);
      if(action==='left')spherical.theta-=Math.PI/12;if(action==='right')spherical.theta+=Math.PI/12;if(action==='up')spherical.phi=Math.max(.03,spherical.phi-Math.PI/12);if(action==='down')spherical.phi=Math.min(Math.PI-.03,spherical.phi+Math.PI/12);
      if(action==='in')spherical.radius*=.8;if(action==='out')spherical.radius*=1.25;
      if(action.startsWith('pan')){const shift=new THREE.Vector3(action==='panLeft'?-1:action==='panRight'?1:0,action==='panUp'?1:action==='panDown'?-1:0,0).applyQuaternion(camera.quaternion).multiplyScalar(offset.length()*.08);controls.target.add(shift);camera.position.add(shift);}else camera.position.copy(controls.target).add(new THREE.Vector3().setFromSpherical(spherical));
    }controls.update();state.dirty=true;
  };
  const stats=()=>{const state=model.current;if(!state)return;const durations=[...state.measure].sort((a,b)=>a-b);setMetrics(`${state.renderer.info.render.triangles.toLocaleString()} triangles · ${state.renderer.info.memory.geometries} geometries · render p95 ${durations[Math.min(durations.length-1,Math.floor(.95*durations.length))]?.toFixed(2)||'Unavailable'} ms (${durations.length} observed renders)`);};
  const inspected=hover??selectedMD,temperature=p?fieldValue(p,'temperature',conduit==='bore'?'bore':'annulus',inspected):tool?.temperature_C??radial?.temperatures[0];
  const basisLabel=chosen?.pressureBasis||pressureBasis;
  const pressure=p?fieldValue(p,'pressure','annulus',inspected):undefined;
  const pipe=useMemo(()=>{let top=0;return w?.string.find(s=>{top+=s.length_m;return selectedMD<=top+1.e-8;});},[w,selectedMD]);
  const hole=holeAtMD(w?.hole||[],selectedMD);
  const layers=[...(hole?.layers||[]),...(pipe?.pipe.layers||[])].sort((a,b)=>b.r_out_m-a.r_out_m),maxRadius=Math.max(hole?.r_wall_m||0,...layers.map(l=>l.r_out_m),.1);
  return <section className="scene-panel" aria-label="3D view">
    <div className="scene-heading"><div><strong>Trajectory & fields <span>3D VIEW · {status==='ready'?'WebGL2':status}</span></strong><small>{runIdentity} · 1D/radial physics · {p?'accepted result':'geometry/history display'}{field==='pressure'?' · '+basisLabel:''}</small></div><label>Scene result <select aria-label="3D comparison result" value={selection} onChange={e=>setSelection(e.target.value)}><option value="current">Current result</option>{alternatives.map(a=><option key={a.identity} value={a.identity}>{a.label}</option>)}</select></label></div>
    <p className="scene-qualification">{t.qualification}. {t.interpolation} {radial?'Only the selected computed radial column is coloured; no interpolation through unsolved depths.':''}</p>
    <div className="scene-settings"><label>Field <select aria-label="3D field" value={field} onChange={e=>{setField(e.target.value as 'temperature'|'pressure');if(e.target.value==='pressure')setConduit('annulus');}}><option value="temperature">Temperature · °C</option><option value="pressure" disabled={!p}>Annulus wall pressure · MPa gauge</option></select></label><label>Conduit <select aria-label="3D conduit" disabled={!p} value={conduit} onChange={e=>setConduit(e.target.value as typeof conduit)}><option value="both" disabled={field==='pressure'}>Bore & annulus</option><option value="bore" disabled={field==='pressure'}>Bore</option><option value="annulus">Annulus</option></select></label><label>Radius exaggeration <input aria-label="3D radius exaggeration" type="number" min={1} max={2000} value={factor} onChange={e=>{const next=e.currentTarget.valueAsNumber;if(next>=1&&next<=2000)setFactor(next);}}/>×</label><label><input type="checkbox" checked={cutaway} onChange={e=>setCutaway(e.target.checked)}/>Cutaway</label><label><input type="checkbox" checked={showPipe} onChange={e=>setShowPipe(e.target.checked)}/>Pipe layers</label><label><input type="checkbox" checked={showCasing} onChange={e=>setShowCasing(e.target.checked)}/>Casing/cement</label><label><input type="checkbox" checked={lockCamera} onChange={e=>setLockCamera(e.target.checked)}/>Lock camera between fields/results</label></div>
    {field==='pressure'&&<div className="scene-settings">Fixed pressure colour range <label>Min MPa gauge <input type="number" aria-label="3D pressure colour minimum" value={pressureRange.minimum} onChange={e=>{const v=e.currentTarget.valueAsNumber;if(Number.isFinite(v)&&v<pressureRange.maximum)setPressureRange({...pressureRange,minimum:v});}}/></label><label>Max MPa gauge <input type="number" aria-label="3D pressure colour maximum" value={pressureRange.maximum} onChange={e=>{const v=e.currentTarget.valueAsNumber;if(Number.isFinite(v)&&v>pressureRange.minimum)setPressureRange({...pressureRange,maximum:v});}}/></label></div>}
    <div className="scene-stage" data-display-transform={DISPLAY_TRANSFORM} ref={host}/>
    {status!=='ready'&&<div className="scene-fallback" role="status">{status==='oversized'?'Geometry exceeds the interactive scene budget (512 meshes, 200,000 vertices or 300,000 triangles). Use the complete 2D schematic and numeric records; no material layers are silently omitted.':status==='incomplete'?'MD/TVD data do not determine a unique 3D path. Use the labelled 2D MD section and numeric records.':status==='lost'?'WebGL context was lost. Calculated data remain available in 2D and tables.':status==='checking'?'Checking WebGL2 capability…':'WebGL2 is unavailable. Use the linked 2D schematic, plots and numeric records.'}{!['incomplete','oversized'].includes(status)&&<button onClick={()=>setEpoch(epoch+1)}>Retry 3D</button>}</div>}
    <div className="scene-camera" aria-label="Keyboard accessible camera controls">{[['left','Rotate left'],['right','Rotate right'],['up','Rotate up'],['down','Rotate down'],['in','Zoom in'],['out','Zoom out'],['panLeft','Pan left'],['panRight','Pan right'],['panUp','Pan up'],['panDown','Pan down'],['fit','Fit well'],['reset','Reset camera'],['focus',radial?'Focus radial column':'Focus pinned MD'],...(radial?[['wall','Focus near-wall field']]:[])].map(([action,label])=><button key={action} disabled={status!=='ready'} onClick={()=>cameraAction(action)}>{label}</button>)}</div>
    <div className="scene-legend"><span>{field==='pressure'?pressureRange.minimum:range.minimum} {field==='pressure'?'MPa gauge':'°C'}</span><i/><span>{field==='pressure'?pressureRange.maximum:range.maximum} {field==='pressure'?'MPa gauge':'°C'}</span><small>Axes: X east · Y up · −Z north (m). Fixed colour limits · clipped outside range · material colours on pipe/casing · {factor}× radius only{radial?' · radial cross-section uses actual radii':''}</small></div>
    <div className="scene-inspector"><label>Pinned MD (m) <input type="number" min={0} max={t.bit_md_m} aria-label="3D pinned measured depth" disabled={!!tool} value={selectedMD} onChange={e=>{const md=e.currentTarget.valueAsNumber;if(Number.isFinite(md)&&md>=0&&md<=t.bit_md_m)onDepth(md);}}/></label><span>{hover!=null?'Hover':'Pinned'} MD {inspected.toFixed(2)} m · TVD {sample(t.md_m,t.tvd_m,inspected)?.toFixed(2)} m · E {t.easting_m?sample(t.md_m,t.easting_m,inspected)?.toFixed(2):'Unavailable'} m · N {t.northing_m?sample(t.md_m,t.northing_m,inspected)?.toFixed(2):'Unavailable'} m</span><strong>{tool?'Tool internal fluid':radial?'Rock at wall':conduit==='bore'?'Bore':'Annulus'} {temperature?.toFixed(3)??'Unavailable'} °C</strong>{p&&<strong>Annulus wall {pressure==null?'Unavailable':(pressure/1e6).toFixed(5)} MPa gauge</strong>}</div>
    {pipe&&hole&&<details className="scene-cross-section"><summary>Physical cross-section at pinned MD · actual radii in metres</summary><div><svg viewBox="0 0 180 180" role="img" aria-label="Concentric physical cross section; all radii use one actual scale"><circle cx="90" cy="90" r={70*hole.r_wall_m/maxRadius} fill="#b8d8d1"/>{layers.map((layer,i)=><path key={i} data-inner-radius-m={layer.r_in_m} data-outer-radius-m={layer.r_out_m} d={ringPath(70*layer.r_in_m/maxRadius,70*layer.r_out_m/maxRadius)} fillRule="evenodd" fill={layer.conductivity_W_m_K>10?'#82929c':'#cec3ad'}/>)}<circle cx="90" cy="90" r={70*pipe.pipe.layers[0].r_in_m/maxRadius} fill="#64a89b"/></svg><p>Bore radius {pipe.pipe.layers[0].r_in_m} m<br/>Pipe outside {pipe.pipe.layers.at(-1)!.r_out_m} m<br/>Hole fluid boundary {hole.r_wall_m} m<br/>{pipe.id} · {hole.id}<br/>No 3D temperature gradient is inferred inside material layers.</p></div></details>}
    <details className="scene-provenance"><summary>Trajectory identity, applicability and render measurements</summary><p>{t.applicability}</p><code>{t.coordinate_sha256}</code><p>Display transform {DISPLAY_TRANSFORM}: X east, Y up, −Z north; engineering coordinates are unchanged. Scene meshes sample by MD; decorative tool/selection markers do not alter coordinates.</p><button onClick={stats}>Measure current scene</button><output>{metrics}</output></details>
    <details className="scene-provenance"><summary>Local renderer diagnostics</summary><p>These checks redraw or temporarily interrupt the renderer. Saved calculations remain intact. The five-second rotating check restores the original camera and respects a hidden-tab interruption.</p><button disabled={status!=='ready'||benchmarking} onClick={()=>{const s=model.current;if(!s)return;setRenderReport(null);setBenchmarking(true);s.controls.enabled=false;s.benchmark={started:performance.now(),last:0,frames:0,intervals:[],position:s.camera.position.clone(),target:s.controls.target.clone(),hidden:document.hidden};}}>{benchmarking?'Measuring five-second redraw…':'Run five-second render check'}</button><button disabled={status!=='ready'||benchmarking} onClick={()=>model.current?.renderer.forceContextLoss()}>Simulate renderer context loss</button><button disabled={status!=='lost'} onClick={()=>model.current?.renderer.forceContextRestore()}>Restore renderer context</button>{renderReport&&<pre aria-label="Renderer benchmark result">{JSON.stringify(renderReport,null,2)}</pre>}</details>
  </section>;
}

export function ScenarioScene({scenario,manifest,depth,onDepth,range,alternatives=[]}:{scenario:components['schemas']['Scenario'];manifest?:{trajectory?:Trajectory;profile:SceneProfile;run_id:string;pressure_reference?:unknown}|null;depth:number;onDepth:(md:number)=>void;range:{minimum:number;maximum:number};alternatives?:Parameters<typeof Scene>[0]['alternatives']}){
  const [preview,setPreview]=useState<Trajectory|null>(null),[previewKey,setPreviewKey]=useState(''),[error,setError]=useState('');
  const key=fingerprint([scenario.definition.well.bit_md_m,scenario.definition.well.survey]);
  useEffect(()=>{let current=true;setPreview(null);setError('');if(manifest?.trajectory)return;void fetch('/api/v1/trajectories/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bit_md_m:scenario.definition.well.bit_md_m,survey:scenario.definition.well.survey})}).then(async response=>{const value=await response.json();if(!response.ok)throw new Error(JSON.stringify(value.detail));if(current){setPreviewKey(key);setPreview(value);}}).catch(e=>{if(current)setError(e.message);});return()=>{current=false;};},[key,manifest?.trajectory]);
  const trajectory=manifest?.trajectory||(previewKey===key?preview:null);
  return trajectory?<Scene trajectory={trajectory} well={scenario.definition.well} profile={manifest?.profile} identity={manifest?.run_id||'Draft geometry · no numerical result'} pressureBasis={(manifest?.pressure_reference as {wall_basis?:string})?.wall_basis} depth={depth} onDepth={onDepth} range={range} alternatives={alternatives}/>:<div className="scene-fallback" role="status">{error||'Preparing trajectory coordinates…'}</div>;
}
