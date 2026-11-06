import {api} from './apiClient';
import {PhysicsChoice, PhysicsRecord} from './PhysicsChoice';
import {formatApiError} from './apiErrors';
import React,{useEffect,useRef,useState,lazy,Suspense} from 'react';
import Plotly from 'plotly.js-dist-min';
import type {Data,Layout} from 'plotly.js';
import {Clock3,Play,Pause,SkipBack,SkipForward,StepBack,StepForward,Upload,Download,FlaskConical,Square,Check,TriangleAlert,Pin} from 'lucide-react';
import type {components} from './api.generated';
import {fingerprint,inputChanges} from './state';
import {savedIndex,eventIndex,stateSegments} from './historyState';
import './history.css';
const Scene=lazy(()=>import('./Scene').then(m=>({default:m.Scene})));
import type {Trajectory} from './sceneState';
import {InterventionSettings,InterventionPanel} from './Intervention';
import type {InterventionRecord} from './Intervention';

type Scenario=components['schemas']['WallHistoryScenario']|components['schemas']['ToolHistoryScenario'];
type Dataset=components['schemas']['HistoryDataset'];
type Entry={id:string;kind:string;md_m:number|null;samples:number;grid_sha256:string;statistics:Record<string,unknown>};
type Manifest={physics?:string;physics_settings?:unknown;intervention?:InterventionRecord;trajectory?:Trajectory;mode:string;run_id:string;title:string;resolved_scenario:Scenario;history:{columns:Entry[];coupling:string;boundary_source:string};warnings:{code:string;message:string}[];validity:Record<string,unknown>;build:Record<string,unknown>;numerical_profile:Record<string,unknown>;limitations:string[];imported?:boolean};
type Job={id:string;mode?:string;state:string;manifest:Manifest|null;failure_kind?:string};
export type HistoryImport={scenario:Scenario;manifest?:Manifest;datasets?:Dataset[]};
type Difference={time_s:number[];state_id:string[];fields:Record<string,(number|null)[]>;qualification:string};
const DRAFT='borethm-history-draft-v1',ACTIVE='borethm-history-active-v1';
const terminal=new Set(['completed','failed','cancelled','timed_out']);
const fmt=(value:number|undefined|null,places=2)=>value==null?'Unavailable':value.toLocaleString('en-GB',{maximumFractionDigits:places});
function save(name:string,value:unknown){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

function Plot({dataset,index,radial,baseline,difference}:{dataset:Dataset;index:number;radial?:boolean;baseline?:Dataset;difference?:Difference|null}){
  const ref=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const node=ref.current!;const traces:Data[]=[];
    const line=(d:Dataset,name:string,values:(number|null)[],color:string,dash?:'dot')=>{
      stateSegments(d.time_s,values,d.state_id).forEach((segment,i)=>traces.push({name,legendgroup:name,showlegend:i===0,x:segment.x,y:segment.y,type:'scatter',mode:'lines',line:{color,dash,width:2},connectgaps:false}));
    };
    if(radial&&dataset.kind==='wall'){
      traces.push({name:'Rock · saved field',x:dataset.grid.radius_m as number[],y:dataset.rock_temperature_C![index],type:'scatter',mode:'lines',line:{color:'#168d84',width:3}});
    }else if(difference){
      Object.entries(difference.fields).forEach(([name,values],i)=>{
        stateSegments(difference.time_s,values,difference.state_id).forEach((segment,j)=>traces.push({name:name.replaceAll('_',' '),legendgroup:name,showlegend:j===0,x:segment.x,y:segment.y,type:'scatter',mode:'lines',line:{color:i?'#d58552':'#168d84'},connectgaps:false}));
      });
    }else{
      const add=(d:Dataset,prefix:string,dash?:'dot')=>{
        line(d,prefix+(d.kind==='wall'?'Wall':'Steel'),d.kind==='wall'?d.wall_temperature_C!:d.steel_temperature_C!,'#168d84',dash);
        line(d,prefix+(d.kind==='wall'?'Static fluid node':'Internal fluid'),d.kind==='wall'?d.fluid_node_temperature_C!:d.internal_fluid_temperature_C!,'#d58552',dash);
      };
      add(dataset,'');if(baseline&&baseline.kind===dataset.kind)add(baseline,'Baseline · ','dot');
    }
    const layout:Partial<Layout>={font:{family:'Segoe UI, sans-serif',size:11,color:'#687d7b'},paper_bgcolor:'transparent',plot_bgcolor:'transparent',margin:{l:65,r:20,t:42,b:50},legend:{orientation:'h',y:1.16},xaxis:{title:{text:radial?'Radius from bore axis (m · actual scale)':'Physical time (s)'},gridcolor:'#e8eeeb'},yaxis:{title:{text:difference?'Temperature difference (°C · variant − baseline)':'Temperature (°C)'},gridcolor:'#e8eeeb'},uirevision:dataset.id+(radial?'radial':difference?'difference':'history'),shapes:radial?[]:[{type:'line',x0:dataset.time_s[index],x1:dataset.time_s[index],y0:0,y1:1,yref:'paper',line:{color:'#8c9f95',dash:'dot',width:1}}],transition:{duration:0,easing:'linear'}};
    void Plotly.react(node,traces,layout,{responsive:true,displayModeBar:false});
  },[dataset,index,radial,baseline,difference]);
  useEffect(()=>{const node=ref.current!;const observer=new ResizeObserver(()=>Plotly.Plots.resize(node));observer.observe(node);return()=>{observer.disconnect();Plotly.purge(node);};},[]);
  return <div ref={ref} className="history-plot" aria-label={radial?'Saved radial rock temperature field':'Thermal history against physical time'} />;
}

export function History({incoming,consumed}:{incoming:HistoryImport|null;consumed:()=>void}){
  const [scenario,setScenario]=useState<Scenario|null>(null),scenarioRef=useRef<Scenario|null>(null);
  const [manifest,setManifest]=useState<Manifest|null>(null),[dataset,setDataset]=useState<Dataset|null>(null);
  const [column,setColumn]=useState(''),[index,setIndex]=useState(0),[playing,setPlaying]=useState(false),[speed,setSpeed]=useState(60);
  const [jobs,setJobs]=useState<Job[]>([]),[job,setJob]=useState<Job|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [validated,setValidated]=useState(''),[resolved,setResolved]=useState<unknown>(null),[pending,setPending]=useState(false),[ready,setReady]=useState(false);
  const [show3D,setShow3D]=useState(false),[spatial,setSpatial]=useState<Trajectory|null>(null);
  const [source,setSource]=useState(''),[sourceOpen,setSourceOpen]=useState(false),[view,setView]=useState<'history'|'data'|'provenance'>('history');
  const [baseline,setBaseline]=useState<{manifest:Manifest;dataset:Dataset}|null>(null),[difference,setDifference]=useState<Difference|null>(null);
  const [reduceMotion,setReduceMotion]=useState(()=>matchMedia('(prefers-reduced-motion: reduce)').matches);
  const target=useRef(0),active=useRef(''),frozen=useRef(''),imported=useRef<Dataset[]>([]),requestSerial=useRef(0),upload=useRef<HTMLInputElement>(null);
  const draft=(s:Scenario)=>{scenarioRef.current=s;setScenario(s);setValidated('');setResolved(null);setSource(JSON.stringify(s,null,2));try{localStorage.setItem(DRAFT,JSON.stringify(s));}catch{setNotice('Browser draft storage is full. Export the scenario to preserve edits.');}};
  const edit=(change:(s:Scenario)=>void)=>{if(!scenario)return;const next=structuredClone(scenario);change(next);draft(next);};
  const refresh=()=>api<{items:Job[]}>('/runs?limit=100').then(x=>setJobs(x.items.filter(j=>j.mode==='wall_history'||j.mode==='tool_history')));
  const adopt=(m:Manifest,arrays:Dataset[]=[])=>{requestSerial.current++;setManifest(m);setDataset(null);setColumn(m.history.columns[0].id);setIndex(0);setPlaying(false);setDifference(null);imported.current=arrays;};
  const applyImport=(value:HistoryImport)=>{active.current='';localStorage.removeItem(ACTIVE);setJob(null);draft(value.scenario);if(value.manifest)adopt({...value.manifest,imported:true},value.datasets||[]);setNotice(value.manifest?'Opened a verified history bundle. Original numerical samples and engine identity retained.':'Scenario opened as a local draft. Run explicitly to calculate.');};
  useEffect(()=>{
    let mounted=true;
    void (async()=>{try{
      const cap=await api<{enabled:{histories:boolean};engine:{adapter_compatible:boolean};property_cache:{ready:boolean}}>('/capabilities');
      if(mounted)setReady(cap.enabled.histories&&cap.engine.adapter_compatible&&cap.property_cache.ready);
      const saved=localStorage.getItem(DRAFT);
      const initial=saved?JSON.parse(saved):await api<Scenario>('/presets/open-hole-history');
      const checked=await api<{resolved_scenario:Scenario}>('/scenarios/validate',{method:'POST',body:JSON.stringify(initial)});
      if(mounted&&!incoming)draft(checked.resolved_scenario);
      await refresh();
      const stored=localStorage.getItem(ACTIVE);
      if(stored&&mounted&&!incoming){const record=JSON.parse(stored);active.current=record.id;frozen.current=record.fingerprint;const run=await api<Job>('/runs/'+record.id);if(mounted)setJob(run);}
    }catch(e){if(mounted)setError((e as Error).message);}})();
    return()=>{mounted=false;};
  },[]);
  useEffect(()=>{if(incoming){applyImport(incoming);consumed();}},[incoming]);
  useEffect(()=>{
    if(!job||terminal.has(job.state))return;
    let mounted=true;const id=job.id;
    const poll=async()=>{try{const next=await api<Job>('/runs/'+id);if(mounted&&active.current===id)setJob(next);}catch(e){if(mounted)setError((e as Error).message);}};
    const timer=setInterval(()=>void poll(),500);return()=>{mounted=false;clearInterval(timer);};
  },[job?.id,job?.state]);
  useEffect(()=>{
    if(!job||!terminal.has(job.state)||active.current!==job.id)return;
    active.current='';localStorage.removeItem(ACTIVE);void refresh().catch(e=>setError(e.message));
    if(job.state==='completed'&&job.manifest){
      if(frozen.current===fingerprint(scenarioRef.current))adopt(job.manifest);
      else setNotice('The history finished for earlier inputs. Open it from saved runs; the current draft remains selected.');
    }else setError(`History ${job.state}: ${job.failure_kind||'see saved run details'}. No partial interval is available for playback.`);
  },[job]);
  useEffect(()=>{
    if(!manifest||!column)return;
    const serial=++requestSerial.current;setPlaying(false);setDataset(null);setDifference(null);
    const load=manifest.imported?Promise.resolve(imported.current.find(d=>d.id===column)):api<Dataset>('/runs/'+manifest.run_id+'/history/'+encodeURIComponent(column));
    void load.then(d=>{if(serial===requestSerial.current){if(!d)throw new Error('Imported history dataset is missing');setDataset(d);setIndex(0);target.current=d.time_s[0];}}).catch(e=>{if(serial===requestSerial.current)setError(e.message);});
    return()=>{requestSerial.current++;};
  },[manifest,column]);
  useEffect(()=>{
    if(!playing||!dataset)return;
    let last=performance.now();
    const timer=setInterval(()=>{const now=performance.now();target.current=Math.min(dataset.time_s.at(-1)!,target.current+(now-last)/1000*speed);last=now;setIndex(savedIndex(dataset.time_s,target.current));if(target.current>=dataset.time_s.at(-1)!)setPlaying(false);},100);
    return()=>clearInterval(timer);
  },[playing,dataset,speed]);
  useEffect(()=>{
    if(!manifest||!show3D)return;setSpatial(null);if(manifest.trajectory){setSpatial(manifest.trajectory);return;}
    let current=true;const s=manifest.resolved_scenario;const request=s.definition.trajectory||{bit_md_m:s.mode==='tool_history'?s.definition.bottom_md_m:Math.max(1,...s.definition.columns.map(c=>c.md_m)),survey:{kind:'vertical'}};
    void api<Trajectory>('/trajectories/preview',{method:'POST',body:JSON.stringify(request)}).then(t=>{if(current)setSpatial({...t,qualification:s.definition.trajectory?t.qualification:'Vertical schematic display assumption; no measured trajectory supplied'});}).catch(e=>setError(e.message));return()=>{current=false;};
  },[manifest,show3D]);
  const seek=(i:number)=>{if(!dataset)return;setPlaying(false);const next=Math.max(0,Math.min(i,dataset.time_s.length-1));setIndex(next);target.current=dataset.time_s[next];};
  const busy=pending||!!job&&!terminal.has(job.state),stale=!!manifest&&fingerprint(manifest.resolved_scenario)!==fingerprint(scenario);
  const loadPreset=async(name:string)=>{try{draft(await api<Scenario>('/presets/'+name));setError('');setNotice('Preset loaded. Existing results retain their original inputs.');}catch(e){setError((e as Error).message);}};
  const validate=async()=>{if(!scenario)return;setPending(true);try{const result=await api<{resolved_scenario:Scenario;warnings:unknown[]}>('/scenarios/validate',{method:'POST',body:JSON.stringify(scenario)});setResolved(result);setValidated(fingerprint(scenario));setError('');}catch(e){setError((e as Error).message);}finally{setPending(false);}};
  const run=async()=>{if(!scenario)return;setPending(true);setError('');setPlaying(false);try{
    const submitted=await api<Job>('/runs',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({scenario})});
    active.current=submitted.id;frozen.current=fingerprint(scenario);localStorage.setItem(ACTIVE,JSON.stringify({id:submitted.id,fingerprint:frozen.current}));setJob(submitted);
  }catch(e){setError((e as Error).message);}finally{setPending(false);}};
  const openRun=async(id:string)=>{try{const full=await api<Job>('/runs/'+id);if(full.state!=='completed'||!full.manifest)throw new Error(`Saved run ${full.state}: ${full.failure_kind||'no completed history'}`);active.current='';localStorage.removeItem(ACTIVE);setJob(full);draft(full.manifest.resolved_scenario);adopt(full.manifest);setError('');}catch(e){setError((e as Error).message);}};
  const importFile=async(file:File)=>{try{const response=await fetch('/api/v1/imports',{method:'POST',headers:{'Content-Type':file.name.endsWith('.zip')?'application/zip':'application/json'},body:file});const value=await response.json();if(!response.ok)throw new Error(formatApiError(value));if(!['wall_history','tool_history'].includes(value.scenario.mode))throw new Error('Open this scenario in the circulation or assessment workspace.');applyImport(value);setError('');}catch(e){setError((e as Error).message);}};
  const compare=async()=>{if(!manifest||!dataset||!baseline)return;try{if(manifest.imported||baseline.manifest.imported)throw new Error('Imported bundles support absolute overlays. Save fresh runs to request a verified difference.');const value=await api<Difference>('/history-comparisons',{method:'POST',body:JSON.stringify({baseline_run_id:baseline.manifest.run_id,baseline_dataset_id:baseline.dataset.id,variant_run_id:manifest.run_id,variant_dataset_id:dataset.id})});setDifference(value);setError('');}catch(e){setDifference(null);setError((e as Error).message);}};
  if(!scenario)return <div className="history-loading" role="status">{error||'Loading thermal history workspace…'}</div>;
  const field=(label:string,value:number,update:(n:number)=>void,unit:string,min?:number)=><label className="history-field"><span>{label} <small>{unit}</small></span><input type="number" aria-label={label} value={value} min={min} step="any" onChange={e=>{if(Number.isFinite(e.currentTarget.valueAsNumber))update(e.currentTarget.valueAsNumber);}} /></label>;
  return <div className={`history-workspace ${reduceMotion?'reduce-motion':''}`}>
    <aside className="history-controls">
      <div className="history-section-heading"><Clock3 size={17}/><strong>Physical thermal histories</strong><span>Prescribed boundaries</span></div>
      <label className="history-field"><span>START FROM A PRESET</span><select aria-label="History preset" value={scenario.mode==='wall_history'?(scenario.intervention?'scheduled-intervention':'open-hole-history'):scenario.definition.trajectory?'inclined-tool-history':'tool-trip-history'} onChange={e=>void loadPreset(e.target.value)}><option value="scheduled-intervention">Scheduled intervention · pause, resume & branch</option><option value="open-hole-history">Open-hole · circulation, connection, restart</option><option value="tool-trip-history">Tool · run-in, staging and pump-off</option><option value="inclined-tool-history">Tool · inclined path and saved playback</option></select></label>
      <label className="history-field"><span>Scenario title</span><input value={scenario.title} aria-label="History scenario title" onChange={e=>edit(s=>{s.title=e.target.value;})}/></label>
      <PhysicsChoice model={scenario.model} message={scenario.metadata?.schema_loading} change={physics=>edit(s=>{s.model.physics=physics;delete s.metadata.schema_loading;})}/><div className="history-input-scroll">
        <div className="history-scope"><FlaskConical size={17}/><div><strong>{scenario.mode==='wall_history'?'Independent radial columns':'Two-node tool history'}</strong><p>{scenario.mode==='wall_history'?'Open hole · explicit rock state · carried between events. No axial rock conduction.':'Prescribed surrounding column. No outside feedback or coolant transport delay.'}</p></div></div>
        {scenario.mode==='wall_history'?<>
          <InterventionSettings scenario={scenario} change={draft}/><h3>Ordered boundary states</h3>
          {scenario.definition.states.map((state,i)=><fieldset className="history-state-input" key={state.id} disabled={i<Number((scenario.intervention?.continuation as {checkpoint?:{next_event_index?:number}}|null)?.checkpoint?.next_event_index||0)}><div><b>{String(i+1).padStart(2,'0')} · {state.label}</b><span>{state.kind}</span></div>
            {field(`${state.label} duration`,state.duration_s,n=>edit(s=>{if(s.mode==='wall_history')s.definition.states[i].duration_s=n;}),'s',.001)}
            {state.kind==='circulating'&&field(`${state.label} fluid temperature`,state.fluid_temperature_C,n=>edit(s=>{if(s.mode==='wall_history'&&s.definition.states[i].kind==='circulating')s.definition.states[i].fluid_temperature_C=n;}),'°C')}
            {'film_W_m2_K' in state&&field(`${state.label} film`,state.film_W_m2_K,n=>edit(s=>{if(s.mode==='wall_history'&&'film_W_m2_K' in s.definition.states[i])s.definition.states[i].film_W_m2_K=n;}),'W/(m² K)',.001)}
            {state.kind==='static'&&<small>Fluid capacity {fmt(state.fluid_capacity_J_m_K)} J/(m K). Initial fluid {state.initial_fluid_temperature_C==null?(scenario.intervention?'carried accepted node / preceding circulation':'from preceding circulation'):`${state.initial_fluid_temperature_C} °C`}.</small>}
          </fieldset>)}
          <h3>Solved locations</h3>
          {scenario.definition.columns.map((c,i)=><fieldset className="history-state-input" key={c.id} disabled={!!scenario.intervention?.continuation}><b>{c.id} · MD {fmt(c.md_m,0)} m</b>{field(`${c.id} initial rock`,c.initial_rock_temperature_C,n=>edit(s=>{if(s.mode==='wall_history')s.definition.columns[i].initial_rock_temperature_C=n;}),'°C')}<small>k {c.conductivity_W_m_K} W/(m K) · local boundary overrides: {Object.keys(c.boundary_overrides||{}).length}</small></fieldset>)}
        </>:<>
          <h3>Run-in and initial nodes</h3>
          {field('Trip speed',scenario.definition.speed_m_s,n=>edit(s=>{if(s.mode==='tool_history')s.definition.speed_m_s=n;}),'m/s',.001)}
          {field('Initial steel temperature',scenario.definition.initial_steel_temperature_C,n=>edit(s=>{if(s.mode==='tool_history')s.definition.initial_steel_temperature_C=n;}),'°C')}
          {field('Initial internal fluid temperature',scenario.definition.initial_fluid_temperature_C,n=>edit(s=>{if(s.mode==='tool_history')s.definition.initial_fluid_temperature_C=n;}),'°C')}
          {field('Staging duration at each depth',scenario.definition.stage_duration_s,n=>edit(s=>{if(s.mode==='tool_history')s.definition.stage_duration_s=n;}),'s',0)}
          <p className="history-small">Staging depths: {scenario.definition.stage_depths_m.map(z=>`${fmt(z,0)} m`).join(', ')}. Staging retains the core's 10 s interior steps.</p>
          <h3>Bottom events</h3>{scenario.definition.stops.map((stop,i)=><section className="history-state-input" key={i}><b>{stop.label} · {stop.kind}</b>{field(`${stop.label} duration`,stop.duration_s,n=>edit(s=>{if(s.mode==='tool_history')s.definition.stops[i].duration_s=n;}),'s',.001)}{field(`${stop.label} outside temperature`,stop.outside_temperature_C,n=>edit(s=>{if(s.mode==='tool_history')s.definition.stops[i].outside_temperature_C=n;}),'°C')}</section>)}
        </>}
        <button className="history-text-button" aria-expanded={sourceOpen} onClick={()=>setSourceOpen(!sourceOpen)}>Edit complete scenario JSON</button>
        {sourceOpen&&<><p className="history-small">Geometry, grids, capacities, local boundary overrides and source notes use the validated scenario contract. Cased columns are unavailable.</p><textarea aria-label="Complete history scenario JSON" value={source} onChange={e=>setSource(e.target.value)} rows={18}/><button onClick={()=>void (async()=>{try{const next=JSON.parse(source);if(!['wall_history','tool_history'].includes(next.mode))throw new Error('Choose a history mode');const checked=await api<{resolved_scenario:Scenario}>('/scenarios/validate',{method:'POST',body:JSON.stringify(next)});draft(checked.resolved_scenario);setError('');}catch(e){setError((e as Error).message);}})()}>Apply JSON draft</button></>}
      </div>
      <div className="history-run-actions"><button onClick={()=>void validate()} disabled={busy}><Check size={15}/>{validated===fingerprint(scenario)?'Validated':'Validate inputs'}</button><button className="history-primary" onClick={()=>void run()} disabled={busy||!ready}><Play size={15}/>{busy?'Calculating…':'Run history'}</button>{busy&&job&&<button onClick={()=>void api<Job>('/runs/'+job.id+'/cancel',{method:'POST'}).then(setJob).catch(e=>setError(e.message))}><Square size={14}/>Cancel</button>}</div>
      {!ready&&<p className="history-small" role="status">History engine or prepared property cache is unavailable. See API readiness.</p>}
    </aside>
    <main className="history-main">
      <header className="history-page-heading"><div><span className="eyebrow">THERMAL HISTORY WORKBENCH</span><h1>{scenario.mode==='wall_history'?'Rock memory, event by event':'Tool exposure through the trip'}</h1><p>Physical time · saved numerical samples · prescribed thermal boundaries</p></div><div className="history-file-actions"><button onClick={()=>upload.current?.click()}><Upload size={15}/>Import</button><button onClick={()=>save('history-scenario.json',scenario)}><Download size={15}/>Scenario</button>{manifest&&!manifest.imported&&<a href={'/api/v1/runs/'+manifest.run_id+'/export'} download><Download size={15}/>Run ZIP</a>}<input ref={upload} type="file" accept=".json,.zip" hidden onChange={e=>{const file=e.target.files?.[0];if(file)void importFile(file);e.currentTarget.value='';}}/></div></header>
      <div className="history-qualification"><TriangleAlert size={16}/><span>Results have not been compared with field measurements. {scenario.mode==='wall_history'?'Independent open-hole radial columns; no axial rock conduction.':'Tool surroundings are prescribed and frozen; no coolant transport delay.'} {scenario.mode==='wall_history'&&scenario.intervention?'Fixed-domain event checkpoints only.':'Checkpoint restart is unavailable for this history mode.'}</span></div>
      {error&&<div className="history-error" role="alert">{error}<button aria-label="Dismiss history error" onClick={()=>setError('')}>×</button></div>}
      {notice&&<div className="history-notice" role="status">{notice}<button aria-label="Dismiss history notice" onClick={()=>setNotice('')}>×</button></div>}
      {busy&&<div className="history-notice" role="status">{job?.state||'Submitting'} · {job?.id||'freezing inputs'} · one isolated numerical worker</div>}
      {stale&&<div className="history-stale">Draft changed. Displayed history belongs to "{manifest?.title}"; run again to calculate these inputs.</div>}
      {!!resolved&&<details className="history-resolved"><summary>Validated, resolved inputs and qualifications</summary><pre>{JSON.stringify(resolved,null,2)}</pre></details>}
      {!manifest&&<div className="history-empty"><Clock3 size={36}/><h2>One history, carried through every event</h2><p>Validate the explicit initial state and boundary schedule, then run. Saved fields will appear here for playback and inspection.</p><div><span>01 · Set initial state</span><span>02 · Solve full history</span><span>03 · Inspect saved steps</span></div></div>}
      {manifest&&<>
        <div className="history-result-bar"><div><strong>{manifest.title}</strong><small>{manifest.run_id} · qualified · {manifest.imported?'verified imported bundle':'saved run'}</small></div><label>Location <select aria-label="History location" value={column} onChange={e=>setColumn(e.target.value)}>{manifest.history.columns.map(c=><option key={c.id} value={c.id}>{c.kind==='wall'?`MD ${fmt(c.md_m,0)} m · ${c.id}`:'Tool along prescribed trip'} · {c.samples} records</option>)}</select></label></div>
        {dataset?<>
          <div className="history-metrics"><div><span>PHYSICAL TIME</span><b>{fmt(dataset.time_s[index])}<small> s</small></b><small>of {fmt(dataset.time_s.at(-1))} s · sample {index+1}/{dataset.time_s.length}</small></div><div><span>{dataset.kind==='wall'?'WALL':'STEEL'} TEMPERATURE</span><b>{fmt(dataset.kind==='wall'?dataset.wall_temperature_C![index]:dataset.steel_temperature_C![index])}<small> °C</small></b><small>MD {fmt(dataset.md_m[index],0)} m</small></div><div><span>{dataset.kind==='wall'?'STATIC NODE':'INTERNAL FLUID'}</span><b>{fmt(dataset.kind==='wall'?dataset.fluid_node_temperature_C![index]:dataset.internal_fluid_temperature_C![index])}<small> °C</small></b><small>{dataset.kind==='wall'&&dataset.fluid_node_temperature_C![index]==null?'No static node in this state':'Saved numerical temperature'}</small></div><div><span>ACTIVE STATE</span><strong>{dataset.state_label[index]}</strong><small>{dataset.event_side[index]} · {dataset.sample_kind[index]}</small></div></div>
<InterventionPanel manifest={manifest} busy={busy} onError={setError} onDraft={s=>{draft(s);setNotice('Continuation draft prepared. Historical inputs are fixed; validate and run to calculate the future interval.');}}/>
          <section className="history-playback" aria-label="Physical time playback">
            <div className="history-playback-buttons"><button aria-label="History start" onClick={()=>seek(0)}><SkipBack size={16}/></button><button aria-label="Previous event side" onClick={()=>seek(eventIndex(dataset.events,index,-1))}><StepBack size={16}/></button><button aria-label="Previous saved sample" onClick={()=>seek(index-1)}>−1</button><button className="history-primary" aria-label={playing?'Pause history':'Play history'} onClick={()=>{if(index===dataset.time_s.length-1){setIndex(0);target.current=dataset.time_s[0];}setPlaying(!playing);}}>{playing?<Pause size={16}/>:<Play size={16}/>}</button><button aria-label="Next saved sample" onClick={()=>seek(index+1)}>+1</button><button aria-label="Next event side" onClick={()=>seek(eventIndex(dataset.events,index,1))}><StepForward size={16}/></button><button aria-label="History end" onClick={()=>seek(dataset.time_s.length-1)}><SkipForward size={16}/></button><label>Speed <select aria-label="Physical playback speed" value={speed} onChange={e=>setSpeed(+e.target.value)}>{[1,10,60,600].map(s=><option key={s} value={s}>{s}×</option>)}</select></label><label className="history-motion"><input type="checkbox" checked={reduceMotion} onChange={e=>setReduceMotion(e.target.checked)}/>Reduced motion</label></div>
            <input type="range" min={0} max={dataset.time_s.length-1} step={1} value={index} aria-label="Saved history sample" onChange={e=>seek(+e.target.value)}/>
            <div className="history-time-labels"><span>{fmt(dataset.time_s[0])} s</span><span>Saved samples only · irregular timestep spacing · event sides remain explicit</span><span>{fmt(dataset.time_s.at(-1))} s</span></div>
            <label className="history-seek">Seek physical time (s) <input aria-label="Seek physical time seconds" type="number" min={dataset.time_s[0]} max={dataset.time_s.at(-1)} step="any" onChange={e=>{if(Number.isFinite(e.currentTarget.valueAsNumber))seek(savedIndex(dataset.time_s,e.currentTarget.valueAsNumber));}}/><small>Displays the last saved sample at or before the requested time.</small></label>
          </section>
          <PhysicsRecord physics={manifest.physics} settings={manifest.physics_settings}/><div className="history-result-tabs" role="tablist" aria-label="History result views">{(['history','data','provenance'] as const).map(v=><button key={v} role="tab" aria-selected={view===v} onClick={()=>setView(v)}>{v==='history'?'Thermal fields':v==='data'?'Numerical records':'Sources & validity'}</button>)}</div>
          {view==='history'&&<><div className="scene-mode-controls"><button aria-pressed={!show3D} onClick={()=>setShow3D(false)}>History plots</button><button aria-pressed={show3D} onClick={()=>setShow3D(true)}>3D history view</button><span>Saved physical time drives the scene; no solve or invented time frames.</span></div>{show3D&&spatial&&<Suspense fallback={<p role="status">Loading 3D history…</p>}><Scene trajectory={spatial} identity={manifest.run_id+' · '+dataset.state_id[index]+' · '+dataset.time_s[index]+' s'} depth={dataset.md_m[index]} onDepth={md=>{if(dataset.kind==='wall'){const nearest=manifest.history.columns.reduce((a,b)=>Math.abs((a.md_m||0)-md)<Math.abs((b.md_m||0)-md)?a:b);setColumn(nearest.id);setNotice(`Selected nearest solved column at MD ${nearest.md_m} m. No interpolation between columns.`);}}} range={{minimum:20,maximum:180}} radial={dataset.kind==='wall'?{md:dataset.md_m[index],radii:dataset.grid.radius_m as number[],temperatures:dataset.rock_temperature_C![index]}:undefined} tool={dataset.kind==='tool'?{md:dataset.md_m[index],temperature_C:dataset.internal_fluid_temperature_C![index],radius_m:manifest.resolved_scenario.mode==='tool_history'?manifest.resolved_scenario.definition.lump.outer_perimeter_m/(2*Math.PI):.08}:undefined}/></Suspense>}<div className={`history-charts ${dataset.kind==='tool'?'single':''}`}><section><h2>{difference?'Difference at common physical times':'Temperature through physical time'}</h2><Plot dataset={dataset} index={index} baseline={baseline?.dataset} difference={difference}/><p className="history-small">{difference?difference.qualification:dataset.kind==='wall'?'Lines break at every state boundary. Static fluid is absent outside static states; no interpolation across events.':'Lines break at every state boundary. Tool phases start at their first saved numerical step; no interpolation across events.'}</p></section>{dataset.kind==='wall'&&<section><h2>Radial rock field · {fmt(dataset.time_s[index])} s</h2><Plot dataset={dataset} index={index} radial/><p className="history-small">Actual radial coordinates · no depth interpolation between independent columns.</p></section>}</div><div className="history-snapshot"><strong>Saved snapshot</strong><span>{dataset.state_id[index]} · {dataset.event_side[index]} · {dataset.sample_kind[index]}</span><small>Source step: {JSON.stringify(dataset.snapshots[index].source_timestep_bracket_s)} s</small><small>Grid SHA-256: {String(dataset.grid.sha256)}</small><small>Field SHA-256: {String(dataset.snapshots[index].field_sha256)}</small></div>
          <div className="history-compare"><button onClick={()=>{setBaseline({manifest,dataset});setDifference(null);}}><Pin size={14}/>Set comparison history</button>{baseline&&<><span>Baseline: {baseline.manifest.title} · {baseline.dataset.id}</span><button onClick={()=>void compare()}>Show difference</button><button onClick={()=>setDifference(null)}>Absolute overlay</button><button onClick={()=>{setBaseline(null);setDifference(null);}}>Clear baseline</button><details><summary>Input changes and build identities</summary><pre>{JSON.stringify({input_changes:inputChanges(baseline.manifest.resolved_scenario,manifest.resolved_scenario),builds:[baseline.manifest.build,manifest.build]},null,2)}</pre></details></>}</div></>}
          {view==='data'&&<section className="history-data"><h2>Saved numerical records · around current sample</h2><p className="history-small">No display resampling. Export contains all {dataset.time_s.length} records, radial fields, event identities and snapshot checksums.</p><button onClick={()=>save(dataset.id+'-history.json',{physics:manifest.physics,physics_settings:manifest.physics_settings,dataset})}>Download dataset with physics</button><table><thead><tr><th>Sample</th><th>Time (s)</th><th>MD (m)</th><th>State / side</th><th>{dataset.kind==='wall'?'Wall':'Steel'} (°C)</th><th>Fluid (°C)</th></tr></thead><tbody>{dataset.time_s.slice(Math.max(0,index-8),Math.min(dataset.time_s.length,index+12)).map((t,j)=>{const k=Math.max(0,index-8)+j;return <tr key={k} className={k===index?'selected':''}><td><button onClick={()=>seek(k)}>{k+1}</button></td><td>{fmt(t,6)}</td><td>{fmt(dataset.md_m[k],3)}</td><td>{dataset.state_label[k]} · {dataset.event_side[k]}</td><td>{fmt(dataset.kind==='wall'?dataset.wall_temperature_C![k]:dataset.steel_temperature_C![k],6)}</td><td>{fmt(dataset.kind==='wall'?dataset.fluid_node_temperature_C![k]:dataset.internal_fluid_temperature_C![k],6)}</td></tr>;})}</tbody></table><h3>Statistics from numerical records</h3><pre>{JSON.stringify(dataset.statistics,null,2)}</pre></section>}
          {view==='provenance'&&<section className="history-provenance"><h2>Declared boundaries, validity and numerical identity</h2><p>{manifest.history.boundary_source}</p><p>{manifest.history.coupling}</p>{manifest.warnings.map((w,i)=><p key={i}><b>{w.code}</b> · {w.message}</p>)}<pre>{JSON.stringify({validity:manifest.validity,statistics:dataset.statistics,numerical_profile:manifest.numerical_profile,build:manifest.build,limitations:manifest.limitations,resolved_scenario:manifest.resolved_scenario},null,2)}</pre></section>}
        </>:<p role="status">Loading verified history dataset…</p>}
      </>}
      <section className="history-saved"><h2>Saved history runs <button onClick={()=>void refresh().catch(e=>setError(e.message))}>Refresh</button></h2>{jobs.length?jobs.map(j=><button key={j.id} disabled={busy} onClick={()=>void openRun(j.id)}><span>{j.manifest?.title||j.id}</span><small>{j.mode} · {j.state} · {j.id}</small></button>):<p>No saved history runs yet.</p>}</section>
    </main>
  </div>;
}
