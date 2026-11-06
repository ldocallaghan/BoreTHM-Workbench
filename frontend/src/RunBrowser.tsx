import React,{useEffect,useState} from 'react';
import {formatApiError} from './apiErrors';
type SavedRun={id:string;state:string;mode?:string;created_at:string;manifest?:{title?:string;mode?:string;physics?:string;physics_settings?:unknown}|null};
export function RunBrowser({onOpen,onClose}:{onOpen:(id:string)=>Promise<void>;onClose:()=>void}){
  const [items,setItems]=useState<SavedRun[]>([]),[offset,setOffset]=useState(0),[more,setMore]=useState(false);
  const [lookup,setLookup]=useState(''),[error,setError]=useState('');
  const pageSize=10;
  useEffect(()=>{
    let active=true;
    void fetch(`/api/v1/runs?mode=circulation_state&limit=${pageSize}&offset=${offset}`).then(async response=>{
      const value=await response.json();if(!response.ok)throw new Error(formatApiError(value));
      if(active){setItems(value.items);setMore(value.has_more);setError('');}
    }).catch(e=>{if(active)setError(e.message);});
    return()=>{active=false;};
  },[offset]);
  async function find(){
    try{
      const response=await fetch('/api/v1/runs/'+encodeURIComponent(lookup.trim()));const value=await response.json();
      if(!response.ok)throw new Error(formatApiError(value));
      if(value.manifest?.mode!=='circulation_state')throw new Error('This run uses another calculation mode. Open it from its workspace.');
      await onOpen(value.id);
    }catch(e){setError((e as Error).message);}
  }
  return <div className="modal-backdrop"><section className="json-modal" role="dialog" aria-modal="true" aria-labelledby="saved-runs-title">
    <div className="section-heading"><h2 id="saved-runs-title">All circulation runs</h2><button className="secondary-button" onClick={onClose}>Close</button></div>
    <label>Run ID<input aria-label="Find run by ID" value={lookup} onChange={e=>setLookup(e.target.value)}/></label>
    <button className="secondary-button" disabled={!lookup.trim()} onClick={()=>void find()}>Open run ID</button>
    {error&&<p role="alert">{error}</p>}
    <div className="data-scroll"><table><thead><tr><th>Saved result</th><th>Physics</th><th>State</th><th>Run ID</th></tr></thead><tbody>{items.map(item=><tr key={item.id}>
      <td><button className="text-button" onClick={()=>void onOpen(item.id)}>{item.manifest?.title||item.id}</button></td><td><details><summary>{item.manifest?.physics||'Unavailable'}</summary><pre>{JSON.stringify(item.manifest?.physics_settings,null,2)}</pre></details></td><td>{item.state}</td><td>{item.id}</td>
    </tr>)}</tbody></table></div>
    <div className="modal-actions"><button className="secondary-button" disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-pageSize))}>Previous page</button>
    <span>Page {1+offset/pageSize}</span><button className="secondary-button" disabled={!more} onClick={()=>setOffset(offset+pageSize)}>Next page</button></div>
  </section></div>;
}
