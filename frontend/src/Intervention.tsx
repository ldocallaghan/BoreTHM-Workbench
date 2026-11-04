import {formatApiError} from './apiErrors';
import React,{useEffect,useState} from 'react';
import type {components} from './api.generated';

type WallScenario=components['schemas']['WallHistoryScenario'];
export type InterventionRecord={status:string;time_origin_s:number;time_end_s:number;schedule_hash:string;
  parent?:{run_id:string;action:string;checkpoint_id:string;checkpoint_sha256:string}|null;
  checkpoints:{id:string;time_s:number;next_event_index:number;sha256:string;has_future:boolean}[];qualification:string};
type Manifest={run_id:string;title:string;resolved_scenario:WallScenario|components['schemas']['ToolHistoryScenario'];intervention?:InterventionRecord;imported?:boolean};

export function InterventionSettings({scenario,change}:{scenario:WallScenario;change:(s:WallScenario)=>void}){
  const p=scenario.intervention;if(!p)return null;
  const continuation=p.continuation as {checkpoint?:{next_event_index?:number;time_s?:number}}|null;
  const start=continuation?.checkpoint?.next_event_index||0;
  return <section className="intervention-settings"><h3>Scheduled intervention</h3>
    <p className="history-small">Fixed open-hole geometry · prescribed water boundaries · no circulation feedback or pressure dynamics. Results have not been compared with field measurements.</p>
    <label className="history-field"><span>Pause after event</span><select aria-label="Pause after event" value={p.stop_after_event_id||''} onChange={e=>{const s=structuredClone(scenario);s.intervention!.stop_after_event_id=e.target.value||null;change(s);}}><option value="">Complete all future events</option>{scenario.definition.states.slice(start,-1).map(s=><option key={s.id} value={s.id}>{s.label}</option>)}</select></label>
    <p className="history-small">Fixed domain horizon: {p.maximum_time_s.toLocaleString()} s. Geometry, static capacities and historical events remain fixed.</p>
    {continuation&&<p className="history-notice">Continuation starts at {continuation.checkpoint?.time_s} s. Earlier input controls are locked. The parent retains its earlier history.</p>}
  </section>;
}

export function InterventionPanel({manifest,busy,onDraft,onError}:{manifest:Manifest;busy:boolean;onDraft:(s:WallScenario)=>void;onError:(s:string)=>void}){
  const record=manifest.intervention;
  const [selected,setSelected]=useState(''),[future,setFuture]=useState(''),[pending,setPending]=useState(false);
  useEffect(()=>{setSelected(record?.checkpoints.filter(c=>c.has_future).at(-1)?.id||'');},[manifest.run_id]);
  const cp=record?.checkpoints.find(c=>c.id===selected);
  useEffect(()=>{if(cp&&manifest.resolved_scenario.mode==='wall_history')setFuture(JSON.stringify(manifest.resolved_scenario.definition.states.slice(cp.next_event_index),null,2));},[manifest.run_id,selected]);
  if(!record)return null;
  const prepare=async(action:'resume'|'branch')=>{
    if(!cp)return;setPending(true);onError('');
    try{
      const body={checkpoint_id:cp.id,action,...(action==='branch'?{future_states:JSON.parse(future)}:{})};
      const response=await fetch(`/api/v1/runs/${manifest.run_id}/branches`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const result=await response.json();if(!response.ok)throw new Error(formatApiError(result));
      onDraft(result.scenario);
    }catch(e){onError((e as Error).message);}finally{setPending(false);}
  };
  return <section className="intervention-panel" aria-label="Saved intervention checkpoints"><div className="intervention-heading"><div><span className="eyebrow">EVENT CHECKPOINTS · thermal history DEVELOPMENT</span><h2>{record.status==='paused_at_event'?'Paused at a complete event boundary':'Scheduled events finished'}</h2></div><strong>{record.time_origin_s.toLocaleString()}–{record.time_end_s.toLocaleString()} s</strong></div>
    <p>Carry the complete rock field, static-fluid memory and energy on the same radial grid. Only listed event boundaries support continuation.</p>
    {record.parent&&<p className="history-small">{record.parent.action} from {record.parent.run_id} · {record.parent.checkpoint_id}. This view contains the newly calculated interval at absolute physical time.</p>}
    <label className="history-field"><span>Accepted checkpoint</span><select aria-label="Accepted event checkpoint" value={selected} onChange={e=>setSelected(e.target.value)}><option value="">Choose an event with remaining schedule</option>{record.checkpoints.filter(c=>c.has_future).map(c=><option key={c.id} value={c.id}>{c.id} · {c.time_s.toLocaleString()} s</option>)}</select></label>
    {cp&&<small className="intervention-checksum">Checkpoint SHA-256: {cp.sha256}</small>}
    {manifest.imported?<p>Imported checkpoints can be reproduced with their matching package. Creating a branch requires a verified local parent run.</p>:<><div className="intervention-actions"><button disabled={!cp||busy||pending} onClick={()=>void prepare('resume')}>Prepare resume draft</button><span>Retains the original full schedule. Inspect the draft, then run explicitly.</span></div>
    <details><summary>Edit future events and prepare a branch</summary><p>Replace only events after the chosen checkpoint. Keep geometry, capacities and prior controls fixed. Local column overrides remain visible in complete scenario JSON.</p><textarea aria-label="Branch future events JSON" value={future} onChange={e=>setFuture(e.target.value)} rows={12}/><button disabled={!cp||busy||pending} onClick={()=>void prepare('branch')}>Prepare branch draft</button></details></>}
    <p className="history-small">No saved frame interpolation becomes physical state. No checkpoint is exposed from a failed or interrupted worker. Results have not been compared with field measurements.</p>
  </section>;
}
