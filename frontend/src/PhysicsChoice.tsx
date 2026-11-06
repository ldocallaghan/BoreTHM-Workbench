import type {components} from './api.generated';

type Model = components['schemas']['Model'];
export function PhysicsChoice({model, message, change}: {model:Model; message?:unknown; change:(physics:Model['physics'])=>void}) {
  return <div className="field"><label><span className="field-label">Model physics</span><select aria-label="Model physics" value={model.physics} onChange={e=>change(e.target.value as Model['physics'])}>
    <option value="v1.8.0">v1.8.0 (current defaults)</option>
    <option value="v1.7.0">v1.7.0 (previous defaults)</option>
  </select></label>{typeof message==='string'&&<p role="status">{message}</p>}</div>;
}

export function PhysicsRecord({physics,settings}: {physics?:string;settings?:unknown}) {
  return <details className="physics-record"><summary>Recorded model physics: {physics||'Unavailable'}</summary><pre>{JSON.stringify(settings,null,2)}</pre></details>;
}
