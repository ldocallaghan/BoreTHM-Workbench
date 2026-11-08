import {useEffect,useState} from 'react';
import type {components} from './api.generated';
import {api} from './apiClient';
import {allowanceFor,type Allowance} from './toolAllowance';
import {UnitInput,useFormat} from './UnitsContext';
import type {Quantity} from './units';

type Scenario=components['schemas']['Scenario'];
type Segment=components['schemas']['Segment'];
type Joint=components['schemas']['Joint'];
type Tool=components['schemas']['ToolLoss'];
const toolNames:Record<string,string>={mwd_pulser:'Measurement while drilling pulser',rotary_steerable:'Rotary steerable tool',mud_motor:'Mud motor',filter_sub:'Filter sub',float_sub:'Float sub'};

export function GeometryEditor({scenario,change}:{scenario:Scenario;change:(update:(draft:Scenario)=>void)=>void}) {
  const [rows,setRows]=useState<Allowance[]>([]),[error,setError]=useState('');
  useEffect(()=>{let active=true;void api<{tool_allowances:Allowance[]}>('/capabilities').then(value=>{if(active)setRows(value.tool_allowances);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[]);
  const set=(i:number,update:(s:Segment)=>void)=>change(draft=>update(draft.definition.well.string[i]));
  const {symbol}=useFormat();
  const number=(label:string,value:number,update:(n:number)=>void,min=0)=><label className="field"><span>{label}</span><input aria-label={label} type="number" step="any" min={min} value={value} onChange={e=>{const n=e.currentTarget.valueAsNumber;if(Number.isFinite(n)&&n>=min)update(n);}}/></label>;
  const measure=(label:string,quantity:Quantity,value:number,update:(n:number)=>void,min=0)=><label className="field"><span>{label} ({symbol(quantity)})</span><UnitInput label={label} ariaLabel={label} value={value} quantity={quantity} change={n=>{if(n>=min)update(n);}}/></label>;
  return <details className="geometry-editor"><summary>String joints and tool losses</summary>{error&&<p role="alert">Tool allowances unavailable: {error}</p>}{scenario.definition.well.string.map((segment,i)=>{
    const joint=segment.pipe.hydraulic_joint,tool=segment.tool_loss;
    const allowance=segment.tool_class&&segment.collar_in?allowanceFor(rows,segment.tool_class,segment.collar_in):undefined;
    const setJoint=(update:(j:Joint)=>void)=>set(i,s=>{if(s.pipe.hydraulic_joint)update(s.pipe.hydraulic_joint);});
    const setTool=(update:(t:Tool)=>void)=>set(i,s=>{if(s.tool_loss)update(s.tool_loss);});
    return <fieldset key={segment.id}><legend>{segment.id}: {segment.pipe.name}</legend>
      <label><input type="checkbox" checked={!!joint} onChange={e=>set(i,s=>{s.pipe.hydraulic_joint=e.target.checked?{outer_diameter_m:s.pipe.layers.at(-1)!.r_out_m*2.1,bore_diameter_m:s.pipe.layers[0].r_in_m*1.9,restriction_length_m:0.2,spacing_m:9.5,transition:null,kind:'ordinary',source:'User declared geometry; confirm dimensions'}:null;})}/>Repeated hydraulic joint</label>
      {joint&&<>
        <label className="field">Joint kind<select aria-label={`${segment.id} joint kind`} value={joint.kind} onChange={e=>setJoint(j=>{j.kind=e.target.value as Joint['kind'];})}><option value="ordinary">Ordinary drill pipe</option><option value="insulated">Insulated pipe</option><option value="heavy-weight">Heavy-weight pipe</option><option value="bha">Bottom-hole assembly</option></select></label>
        <label className="field">Joint transition<select aria-label={`${segment.id} joint transition`} value={joint.transition||'physics'} onChange={e=>setJoint(j=>{j.transition=e.target.value==='physics'?null:e.target.value as Joint['transition'];j.taper_angle_deg=j.transition==='tapered'?11.5:null;})}><option value="physics">Selected physics decides</option><option value="abrupt">Abrupt</option><option value="gradual">Gradual</option><option value="tapered">Tapered</option></select></label>
        {joint.transition==='tapered'&&number(`${segment.id} included taper angle (degrees)`,joint.taper_angle_deg!,n=>setJoint(j=>{j.taper_angle_deg=n;}),0.001)}
        {measure(`${segment.id} joint outside diameter`,'diameter',joint.outer_diameter_m,n=>setJoint(j=>{j.outer_diameter_m=n;}),0.001)}
        {measure(`${segment.id} joint bore diameter`,'diameter',joint.bore_diameter_m,n=>setJoint(j=>{j.bore_diameter_m=n;}),0.001)}
        {measure(`${segment.id} joint restriction length`,'length',joint.restriction_length_m,n=>setJoint(j=>{j.restriction_length_m=n;}))}
        {measure(`${segment.id} joint spacing`,'length',joint.spacing_m,n=>setJoint(j=>{j.spacing_m=n;}),0.001)}
        <label className="field">Joint source<input value={joint.source} onChange={e=>setJoint(j=>{j.source=e.target.value;})}/></label>
      </>}
      <label className="field">Tool class<select aria-label={`${segment.id} tool class`} value={segment.tool_class||''} onChange={e=>set(i,s=>{s.tool_class=(e.target.value||null) as Segment['tool_class'];s.collar_in=s.tool_class?(s.collar_in||6.75):null;})}><option value="">No named tool</option>{Object.entries(toolNames).map(([key,name])=><option key={key} value={key}>{name}</option>)}</select></label>
      {segment.tool_class&&number(`${segment.id} collar size (inches)`,segment.collar_in!,n=>set(i,s=>{s.collar_in=n;}),0.001)}
      {segment.tool_class&&<><label className="field">Generic allowance basis<select value={segment.tool_basis} onChange={e=>set(i,s=>{s.tool_basis=e.target.value as Segment['tool_basis'];})}><option value="additional">Additional loss</option><option value="total_internal">Total internal loss over the declared span</option></select></label>{number(`${segment.id} allowance span (segments)`,segment.tool_span_segments,n=>set(i,s=>{s.tool_span_segments=n;}),1)}</>}
      {allowance&&<p className="small">Generic allowance: {allowance.dp_ref_psi.toFixed(2)} pounds per square inch at {allowance.flow_ref_gpm} US gallons per minute, water at 8.34 pounds per US gallon. Flow exponent {allowance.exponent.toFixed(3)}. {allowance.sources.length?`${allowance.sources.length} published sources.`:'No usable published figure; zero allowance.'} {scenario.model.physics==='v1.7.0'?'Previous physics applies no generic tool allowance.':''} Enter the maker's figure where known.</p>}
      <label><input type="checkbox" checked={!!tool} onChange={e=>set(i,s=>{s.tool_loss=e.target.checked?{reference_pressure_Pa:0,reference_flow_m3_s:0.03,reference_density_kg_m3:1000,basis:'additional',span_segments:1,include_boundary_changes:true,unresolved:false,source:'User supplied figure'}:null;})}/>Use a supplied tool-loss figure</label>
      {tool&&<><p className="small">The supplied figure overrides the generic allowance for this segment.</p>
        {measure(`${segment.id} supplied pressure loss`,'pressure',tool.reference_pressure_Pa,n=>setTool(t=>{t.reference_pressure_Pa=n;}))}
        {measure(`${segment.id} reference flow`,'volume_flow',tool.reference_flow_m3_s,n=>setTool(t=>{t.reference_flow_m3_s=n;}),0.000001)}
        {measure(`${segment.id} reference density`,'density',tool.reference_density_kg_m3,n=>setTool(t=>{t.reference_density_kg_m3=n;}),0.001)}
        <label className="field">Loss basis<select value={tool.basis} onChange={e=>setTool(t=>{t.basis=e.target.value as Tool['basis'];})}><option value="additional">Additional loss</option><option value="total_internal">Total internal loss over the declared span</option></select></label>
        {number(`${segment.id} tool span (segments)`,tool.span_segments,n=>setTool(t=>{t.span_segments=n;}),1)}
        <label className="field">Figure source<input value={tool.source} onChange={e=>setTool(t=>{t.source=e.target.value;})}/></label>
      </>}
    </fieldset>;
  })}</details>;
}
