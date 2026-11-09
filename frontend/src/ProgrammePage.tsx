import React, {useEffect, useMemo, useRef, useState} from 'react';
import type {components} from './api.generated';
import {api} from './apiClient';
import {NumberInput, UnitInput, useFormat} from './UnitsContext';
import {format as formatUnit, fromSI, roundSignificant, symbol as unitSymbol, toSI, type UnitSystem} from './units';
import {WellView} from './WellView';
import type {Quantity} from './units';
import type {SiteCheck, SiteDocument} from './SiteEditor';

type Scenario = components['schemas']['AssessmentScenario'];
type Programme = components['schemas']['ProgrammeDefinition'];
type Segment = components['schemas']['Segment'];
type Hole = components['schemas']['Hole'];
type Layer = components['schemas']['Layer'];
type Issue = {field: string; message: string};
export type FieldRow = {name: string; label: string; quantity: string; unit: string | null; rule: string; source: string; default: unknown};
type Fields = {fields: FieldRow[]; groups: Record<string, string[]>; pipe_names: string[]};
type ModelPipe = {name: string; layers: Layer[]; thermal_joint_fraction: number; source: string; commercial: boolean; coating_rating_C: number | null};
type Drawing = {bit_md_m: number; string: Segment[]; hole: Hole[]};
type ProgrammeCheck = {valid: boolean; errors: string[]; drawing: Drawing | null; toml_table: Record<string, unknown> | null; toml_note: string | null; target_depth_m: number | null};
type OperationsCheck = {errors: Issue[]; warnings: Issue[]; stated: string[]};
type Operations = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const UNIT_TABLE_QUANTITIES: Record<string, true> = {temperature: true, pressure: true, velocity: true, minutes: true, hours: true, length: true, rate_of_penetration: true, density: true, mass_flow: true, volume_flow: true};
const TOOL_CLASSES = ['mwd_pulser', 'rotary_steerable', 'mud_motor', 'filter_sub', 'float_sub'];

export const defaultReference = (): Programme => ({kind: 'reference', string: [], hole: [], transitions: 'abrupt', surface_loss: {kind: 'api_case', case: 5}, joints: true, tools: true, bha_tools: []});

// The operations table without empty entries, so a page with nothing stated
// leaves the site file as it was.
export function cleanOperations(table: Operations): Operations | null {
  const next: Operations = {...table};
  if (next.sources && !Object.keys(next.sources).length) delete next.sources;
  return Object.keys(next).length ? next : null;
}

function Messages({items, kind}: {items: Issue[]; kind: 'error' | 'warning'}) {
  return <>{items.map((m, i) => <p key={i} className={kind === 'error' ? 'field-error' : 'field-warning'} role={kind === 'error' ? 'alert' : undefined}>{m.message}</p>)}</>;
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], {type: 'application/toml'}));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
}

// ------------------------------------------------------------ hydraulic programme
function SectionRow({segment, index, count, pipes, change, remove, move, split}: {segment: Segment; index: number; count: number; pipes: ModelPipe[]; change: (s: Segment) => void; remove: () => void; move: (by: number) => void; split: (top: number) => void}) {
  const {symbol} = useFormat();
  const [splitAt, setSplitAt] = useState<number | null>(null);
  const named = pipes.find(p => p.name === segment.pipe.name && JSON.stringify(p.layers) === JSON.stringify(segment.pipe.layers));
  const setPipe = (name: string) => {
    if (name === '__layers') { change({...segment, pipe: {...segment.pipe, name: segment.pipe.name + ' (user layers)', layers: segment.pipe.layers.slice(0, 1)}}); return; }
    const p = pipes.find(x => x.name === name);
    if (p) change({...segment, pipe: {...segment.pipe, name: p.name, layers: p.layers, thermal_joint_fraction: p.thermal_joint_fraction, source: p.source}});
  };
  const setLayer = (i: number, key: keyof Layer, value: number) => change({...segment, pipe: {...segment.pipe, layers: segment.pipe.layers.map((l, j) => j === i ? {...l, [key]: value} : l)}});
  return <tr>
    <td>{index + 1}</td>
    <td><UnitInput label={`Section ${index + 1} length`} value={segment.length_m} quantity="length" change={v => change({...segment, length_m: v})} /></td>
    <td>
      <select aria-label={`Section ${index + 1} pipe`} value={named ? named.name : '__current'} onChange={e => setPipe(e.target.value)}>
        {!named && <option value="__current">{segment.pipe.name}</option>}
        {pipes.map(p => <option key={p.name} value={p.name}>{p.name}{p.commercial ? '' : ' (not on sale)'}</option>)}
        <option value="__layers">User layers</option>
      </select>
      {!named && <div className="layer-table">{segment.pipe.layers.map((l, i) => <div key={i} className="number-row small">
        <span>Layer {i + 1}: inner radius</span><UnitInput label={`Section ${index + 1} layer ${i + 1} inner radius`} value={l.r_in_m} quantity="diameter" change={v => setLayer(i, 'r_in_m', v)} />
        <span>outer radius</span><UnitInput label={`Section ${index + 1} layer ${i + 1} outer radius`} value={l.r_out_m} quantity="diameter" change={v => setLayer(i, 'r_out_m', v)} />
        <span>conductivity</span><UnitInput label={`Section ${index + 1} layer ${i + 1} conductivity`} value={l.conductivity_W_m_K} quantity="conductivity" change={v => setLayer(i, 'conductivity_W_m_K', v)} />
      </div>)}<small>Radii in {symbol('diameter')}; conductivity in {symbol('conductivity')}.</small></div>}
    </td>
    <td>{segment.pipe.hydraulic_joint ? `${segment.pipe.hydraulic_joint.kind} joints` : 'No joints'}</td>
    <td><select aria-label={`Section ${index + 1} tool`} value={segment.tool_class ?? ''} onChange={e => change(e.target.value ? {...segment, tool_class: e.target.value as Segment['tool_class'], collar_in: segment.collar_in ?? 6.75} : {...segment, tool_class: null, collar_in: null})}>
      <option value="">None</option>{TOOL_CLASSES.map(t => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}</select>
      {segment.tool_class && <NumberInput ariaLabel={`Section ${index + 1} collar size (in)`} value={segment.collar_in} change={v => change({...segment, collar_in: v})} />}</td>
    <td className="row-actions">
      <button className="text-button" disabled={index === 0} onClick={() => move(-1)} aria-label={`Move section ${index + 1} up`}>Up</button>
      <button className="text-button" disabled={index === count - 1} onClick={() => move(1)} aria-label={`Move section ${index + 1} down`}>Down</button>
      <button className="text-button" disabled={count === 1} onClick={remove} aria-label={`Remove section ${index + 1}`}>Remove</button>
      {splitAt == null ? <button className="text-button" onClick={() => setSplitAt(segment.length_m / 2)} aria-label={`Split section ${index + 1}`}>Split</button> : <span className="split-row">
        <span>Upper part</span><UnitInput label={`Section ${index + 1} upper part length`} value={splitAt} quantity="length" change={setSplitAt} />
        <button className="text-button" onClick={() => { if (splitAt > 0 && splitAt < segment.length_m) split(splitAt); setSplitAt(null); }}>Split here</button></span>}
    </td>
  </tr>;
}

function HydraulicProgramme({programme, set, inSite, pipes, check}: {programme: Programme | null; set: (next: Programme | null) => void; inSite: boolean; pipes: ModelPipe[]; check: ProgrammeCheck | null}) {
  const {format} = useFormat();
  const kind = programme?.kind ?? 'site';
  const string = programme?.string ?? [];
  const setString = (next: Segment[]) => programme && set({...programme, string: next.map((s, i) => ({...s, id: `section-${i + 1}`}))});
  const target = check?.target_depth_m;
  const total = string.reduce((a, s) => a + s.length_m, 0);
  const choose = async (next: string) => {
    if (next === 'site') set(null);
    else if (next === 'reference') set(defaultReference());
    else {
      // A custom string starts from the well the model builds for the reference programme.
      const base = check?.drawing && programme?.kind !== 'custom' ? check.drawing : null;
      set({...defaultReference(), kind: 'custom', string: base ? base.string : [], hole: base ? base.hole : [], joints: true, tools: true});
    }
  };
  const surface = programme?.surface_loss ?? {kind: 'api_case', case: 5};
  return <section className="result-section programme-section" aria-labelledby="programme-hydraulic">
    <div className="section-heading"><h2 id="programme-hydraulic">Hydraulic programme</h2></div>
    <div className="choice-row" role="radiogroup" aria-label="Hydraulic programme">
      {([['site', "The model's reference programme"], ['reference', 'Reference programme with options'], ['custom', 'Custom string and hole']] as [string, string][]).map(([key, label]) =>
        <label key={key} className="choice"><input type="radio" name="programme-kind" checked={kind === key} onChange={() => void choose(key)} />{label}</label>)}
    </div>
    {kind === 'site' && <p className="small">The assessment uses the model's reference string and casing scenario, and the site file has no [programme] table.</p>}
    {programme && check?.valid && <p className="small">{inSite ? "Stored in the site file's [programme] table." : check.toml_note}</p>}
    {programme && <div className="programme-options">
      <label className="field"><span className="field-label">Surface losses</span>
        <select aria-label="Surface losses" value={surface.kind === 'api_case' ? `case-${surface.case}` : surface.kind} onChange={e => {
          const v = e.target.value;
          set({...programme, surface_loss: v === 'none' ? {kind: 'none'} : v === 'coefficient' ? {kind: 'coefficient', coefficient: 0, unit_convention: 'psi_ppg_US_gpm', source: 'User-supplied surface coefficient'} : {kind: 'api_case', case: Number(v.split('-')[1])}});
        }}>{[1, 2, 3, 4, 5].map(c => <option key={c} value={`case-${c}`}>API RP 13D surface case {c}</option>)}<option value="coefficient">Stated coefficient</option><option value="none">No surface loss</option></select></label>
      {surface.kind === 'coefficient' && <label className="field"><span className="field-label">Surface coefficient (psi, ppg and US gal/min convention)</span><NumberInput ariaLabel="Surface coefficient" value={surface.coefficient} change={v => set({...programme, surface_loss: {...surface, coefficient: v}})} /></label>}
      <label className="field"><span className="field-label">Changes between sections</span><select aria-label="Section transitions" value={programme.transitions} onChange={e => set({...programme, transitions: e.target.value as Programme['transitions']})}><option value="abrupt">Abrupt</option><option value="gradual">Gradual</option></select></label>
      {programme.kind === 'reference' && <>
        <label className="choice"><input type="checkbox" checked={programme.joints} onChange={e => set({...programme, joints: e.target.checked})} />Tool joints on the reference pipe</label>
        <label className="choice"><input type="checkbox" checked={programme.tools} onChange={e => set({...programme, tools: e.target.checked})} />Tool allowances in the reference assembly</label>
        <div className="field"><span className="field-label">Tools in the collar section</span>
          {programme.bha_tools.map((t, i) => <div className="number-row" key={i}>
            <select aria-label={`Collar tool ${i + 1}`} value={t.tool_class} onChange={e => set({...programme, bha_tools: programme.bha_tools.map((x, j) => j === i ? {...x, tool_class: e.target.value as typeof t.tool_class} : x)})}>{TOOL_CLASSES.map(c => <option key={c} value={c}>{c.replace('_', ' ')}</option>)}</select>
            <NumberInput ariaLabel={`Collar tool ${i + 1} size (in)`} value={t.collar_in} change={v => set({...programme, bha_tools: programme.bha_tools.map((x, j) => j === i ? {...x, collar_in: v} : x)})} /><span>in</span>
            <button className="text-button" onClick={() => set({...programme, bha_tools: programme.bha_tools.filter((_, j) => j !== i)})}>Remove</button></div>)}
          <button className="text-button" onClick={() => set({...programme, bha_tools: [...programme.bha_tools, {tool_class: 'rotary_steerable', collar_in: 6.75}]})}>Add a tool</button></div>
      </>}
    </div>}
    {programme?.kind === 'custom' && <>
      <h3>String, surface to bit</h3>
      <p className="small">The section lengths must add up to the target depth{target != null ? `, ${format(target, 'length')}` : ''}. Now: {format(total, 'length')}.</p>
      <div className="table-scroll"><table className="result-table programme-string"><thead><tr><th>#</th><th>Length</th><th>Pipe</th><th>Joints</th><th>Tool</th><th></th></tr></thead><tbody>
        {string.map((s, i) => <SectionRow key={s.id + i} segment={s} index={i} count={string.length} pipes={pipes}
          change={next => setString(string.map((x, j) => j === i ? next : x))}
          remove={() => setString(string.filter((_, j) => j !== i))}
          move={by => { const next = [...string]; const [item] = next.splice(i, 1); next.splice(i + by, 0, item); setString(next); }}
          split={top => setString([...string.slice(0, i), {...s, length_m: top}, {...s, length_m: s.length_m - top}, ...string.slice(i + 1)])} />)}
      </tbody></table></div>
      <button className="text-button" onClick={() => setString([...string, {id: 'new', length_m: 100, pipe: {name: pipes[3]?.name ?? 'conventional', layers: pipes[3]?.layers ?? [{r_in_m: 0.05, r_out_m: 0.064, conductivity_W_m_K: 45}], thermal_joint_fraction: 0, source: pipes[3]?.source ?? '', hydraulic_joint: null}, local_loss: null, tool_loss: null, tool_class: null, collar_in: null, tool_basis: 'additional', tool_span_segments: 1}])}>Add a section at the bit</button>
      <h3>Hole and casing intervals</h3>
      {programme.hole.length ? <div className="table-scroll"><table className="result-table"><thead><tr><th>Top</th><th>Bottom</th><th>Wall radius</th><th>Casing and cement layers</th><th></th></tr></thead><tbody>{programme.hole.map((h, i) => <tr key={h.id + i}>
        <td><UnitInput label={`Interval ${i + 1} top`} value={h.md_top_m} quantity="length" change={v => set({...programme, hole: programme.hole.map((x, j) => j === i ? {...x, md_top_m: v} : x)})} /></td>
        <td><UnitInput label={`Interval ${i + 1} bottom`} value={h.md_bottom_m} quantity="length" change={v => set({...programme, hole: programme.hole.map((x, j) => j === i ? {...x, md_bottom_m: v} : x)})} /></td>
        <td>{format(h.r_wall_m, 'diameter')}</td><td>{h.layers.length ? `${h.layers.length} layers` : 'Open hole'}</td>
        <td><button className="text-button" onClick={() => set({...programme, hole: programme.hole.filter((_, j) => j !== i)})}>Remove</button></td></tr>)}</tbody></table></div>
        : <p className="small">No intervals stated: the model's reference casing scenario applies.</p>}
    </>}
    {check && !check.valid && <Messages kind="error" items={check.errors.map(m => ({field: '', message: m}))} />}
    {check?.drawing && <div className="programme-drawing">
      <WellView scenario={{definition: {well: check.drawing}} as never} manifest={null} depth={0} onDepth={() => undefined} range={{minimum: 0, maximum: 1}} />
      <div className="small"><p>The well the model builds at the target{kind !== 'custom' ? ', with the first candidate pipe; the model chooses the pipe when it runs' : ''}.</p>
        <ul>{check.drawing.string.map(s => <li key={s.id}>{s.pipe.name}: {format(s.length_m, 'length')}{s.tool_class ? `, ${s.tool_class.replace('_', ' ')}` : ''}</li>)}</ul></div>
    </div>}
  </section>;
}

// ------------------------------------------------------------ operations
// Volume flows are L/s in the model's operations and m3/s in the volume_flow quantity.
const SCALE: Record<string, number> = {volume_flow_L_s: 1000, volume_flows_L_s: 1000};
const toQuantity = (name: string, v: number) => v / (SCALE[name] ?? 1);
const fromQuantity = (name: string, v: number) => roundSignificant(v * (SCALE[name] ?? 1));

function GridInput({row, value, change}: {row: FieldRow; value: number[] | null; change: (v: number[]) => void}) {
  const {system} = useFormat();
  const q = row.quantity as Quantity;
  const shown = (value ?? []).map(v => Number(fromSI(toQuantity(row.name, v), q, system).toPrecision(10))).join(', ');
  const [text, setText] = useState(shown);
  useEffect(() => setText(shown), [system]);
  return <><input aria-label={`${row.label} (${unitSymbol(q, system)})`} value={text} onChange={e => {
    setText(e.target.value);
    const v = e.target.value.split(/[,\s]+/).filter(Boolean).map(Number);
    if (v.length && v.every(Number.isFinite)) change(v.map(x => fromQuantity(row.name, toSI(x, q, system))));
  }} /><small>{unitSymbol(q, system)}, rising, separated by commas.</small></>;
}

function OperationInput({row, value, change, pipeNames}: {row: FieldRow; value: unknown; change: (v: unknown) => void; pipeNames: string[]}) {
  if (row.name === 'pipes') {
    const chosen = (value as string[]) ?? [];
    return <div className="pipe-order">{pipeNames.map(name => <label key={name} className="choice"><input type="checkbox" checked={chosen.includes(name)} onChange={e => change(e.target.checked ? [...chosen, name] : chosen.filter(n => n !== name))} />{name}{chosen.includes(name) ? ` (${chosen.indexOf(name) + 1})` : ''}</label>)}<small>Tried in the order ticked.</small></div>;
  }
  if (row.name.endsWith('_flows_kg_s') || row.name.endsWith('_flows_L_s')) return <GridInput row={row} value={value as number[] | null} change={change} />;
  if (row.name === 'coating_ratings_C') {
    const ratings = (value as Record<string, number>) ?? {};
    return <div>{pipeNames.map(name => <div key={name} className="number-row small"><label className="choice"><input type="checkbox" checked={name in ratings} onChange={e => { const next = {...ratings}; if (e.target.checked) next[name] = 204; else delete next[name]; change(next); }} />{name}</label>
      {name in ratings && <UnitInput label={`Coating rating for ${name}`} value={ratings[name]} quantity="temperature" change={v => change({...ratings, [name]: v})} />}</div>)}<small>Pipes not ticked have no rating. {Object.keys(ratings).length ? '' : 'None ticked: no pipe has a rating.'}</small></div>;
  }
  if (row.quantity in UNIT_TABLE_QUANTITIES) return <UnitInput label={row.label} value={value == null ? null : toQuantity(row.name, value as number)} quantity={row.quantity as Quantity} change={v => change(fromQuantity(row.name, v))} />;
  return <NumberInput ariaLabel={row.label} step={row.quantity === 'count' ? 1 : 'any'} integer={row.quantity === 'count'} value={value as number | null} change={change} />;
}

// The value a row takes when it is first stated: the model's default, or a
// starting value where the model searches.
function initial(row: FieldRow): unknown {
  if (row.default != null) return row.default;
  const start: Record<string, unknown> = {mass_flow_kg_s: 30, volume_flow_L_s: 30, trip_hours: 20, rop_m_h: 5, drilling_fluid_kg_m3: 1050, trip_fluid_kg_m3: 1050};
  return start[row.name] ?? null;
}

function defaultText(row: FieldRow, system: UnitSystem): string {
  const v = row.default;
  if (v == null) return '';
  const q = row.quantity in UNIT_TABLE_QUANTITIES ? row.quantity as Quantity : null;
  if (Array.isArray(v)) return q ? v.map(x => formatUnit(toQuantity(row.name, x as number), q, system, 1)).join(', ') : v.join(', ');
  if (typeof v === 'object') return Object.entries(v as Record<string, number>).map(([k, x]) => `${k} ${formatUnit(x, 'temperature', system)}`).join('; ');
  return q ? formatUnit(toQuantity(row.name, v as number), q, system) : String(v);
}

function OperationsTable({site, editSite, siteCheck, bases, fields}: {site: SiteDocument; editSite: (next: SiteDocument) => void; siteCheck: SiteCheck | null; bases: string[]; fields: Fields}) {
  const {system} = useFormat();
  const operations: Operations = (site.operations as Operations) ?? {};
  const [check, setCheck] = useState<OperationsCheck | null>(null);
  const [choice, setChoice] = useState<Record<string, string>>({});
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => { void api<OperationsCheck>('/operations/check', {method: 'POST', body: JSON.stringify({operations}), signal: controller.signal}).then(setCheck).catch(() => undefined); }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [JSON.stringify(operations)]);
  const write = (next: Operations) => {
    const cleaned = cleanOperations(next);
    const document = {...site};
    if (cleaned) document.operations = cleaned; else delete document.operations;
    editSite(document);
  };
  const sources: Record<string, [string, string]> = operations.sources ?? {};
  // Messages are attached by the field names in the model's validation records.
  const issues = (name: string) => {
    const match = (i: Issue) => i.field === name || i.field === `operations.${name}`;
    const errors = [...(check?.errors ?? []).filter(match)];
    const warnings = [...(check?.warnings ?? []).filter(match)];
    for (const w of siteCheck?.warnings ?? []) if (match(w) && !warnings.some(x => x.message === w.message)) warnings.push(w);
    return {errors, warnings};
  };
  const general = [...(check?.errors ?? []).filter(i => !i.field || i.field === 'operations' || i.field === 'sources'),
    ...(siteCheck?.errors ?? []).filter(i => i.field === 'operations' || i.field === 'operations.operations' || i.field === 'operations.sources')];
  const byName = Object.fromEntries(fields.fields.map(f => [f.name, f]));
  const grouped = new Set(Object.values(fields.groups).flat());
  const rows: {key: string; members: FieldRow[]}[] = [];
  for (const f of fields.fields) {
    if (!grouped.has(f.name)) rows.push({key: f.name, members: [f]});
    else if (Object.values(fields.groups).find(g => g[0] === f.name)) rows.push({key: f.name, members: Object.values(fields.groups).find(g => g[0] === f.name)!.map(n => byName[n])});
  }
  return <section className="result-section" aria-labelledby="programme-operations">
    <div className="section-heading"><h2 id="programme-operations">Operations</h2><span>{check ? `${check.stated.length} stated` : ''}</span></div>
    <p className="small">Each row is either chosen by the model, by the rule given, or stated here. A stated value is used as given: if it fails a check, the result reports the failure and the model does not look for another value. Defaults, rules and sources are the model's own.</p>
    <Messages kind="error" items={general} />
    <div className="table-scroll"><table className="result-table operations-table"><thead><tr><th>Choice</th><th>Model chooses</th><th>Stated value</th><th>Basis and source</th></tr></thead><tbody>
      {rows.map(({key, members}) => {
        const statedName = members.find(m => m.name in operations)?.name;
        const row = byName[statedName ?? choice[key] ?? members[0].name];
        const stated = !!statedName;
        const label = members.length > 1 ? (key === 'mass_flow_kg_s' ? 'Flow' : 'Flows the search tries') : row.label;
        const msgs = issues(row.name);
        const pair = sources[row.name] ?? ['assumed', ''];
        const others = (next: Operations) => { for (const m of members) if (m.name !== row.name) { delete next[m.name]; if (next.sources) { const s2 = {...next.sources}; delete s2[m.name]; next.sources = s2; } } return next; };
        const shown = defaultText(row, system);
        return <tr key={key} className={stated ? 'stated' : ''} data-field={row.name}>
          <th scope="row">{label}
            {members.length > 1 && <div><select aria-label={`${label}: mass or volume`} value={row.name} onChange={e => {
              const name = e.target.value; setChoice({...choice, [key]: name});
              if (stated) { const next = others({...operations}); delete next[row.name]; next[name] = initial(byName[name]); if (sources[row.name]) next.sources = {...(next.sources ?? {}), [name]: sources[row.name]}; if (next.sources) delete next.sources[row.name]; write(next); }
            }}>{members.map(m => <option key={m.name} value={m.name}>{m.quantity === 'mass_flow' ? 'Mass flow' : 'Volume flow at surface'}</option>)}</select></div>}</th>
          <td><label className="choice"><input type="checkbox" aria-label={`${label}: model chooses`} checked={!stated} onChange={e => {
            const next = others({...operations});
            if (e.target.checked) { delete next[row.name]; if (next.sources) { const s2 = {...next.sources}; delete s2[row.name]; next.sources = s2; } }
            else next[row.name] = initial(row);
            write(next);
          }} />Model chooses</label><p className="small muted">{row.rule}{shown ? ` Default: ${shown}.` : ''}</p><p className="small muted">Source: {row.source}</p></td>
          <td>{stated ? <OperationInput row={row} value={operations[row.name]} pipeNames={fields.pipe_names} change={v => write({...operations, [row.name]: v})} /> : <span className="muted">Not stated</span>}
            <Messages kind="error" items={msgs.errors} /><Messages kind="warning" items={msgs.warnings} /></td>
          <td>{stated && <div className="basis-row">
            <select aria-label={`${label} basis`} value={pair[0]} onChange={e => write({...operations, sources: {...sources, [row.name]: [e.target.value, pair[1]]}})}>{bases.map(b => <option key={b} value={b}>{b}</option>)}</select>
            <input aria-label={`${label} source`} placeholder="Source" value={pair[1]} onChange={e => write({...operations, sources: {...sources, [row.name]: [pair[0], e.target.value]}})} />
          </div>}</td>
        </tr>;
      })}
    </tbody></table></div>
  </section>;
}

// ------------------------------------------------------------ page
export function ProgrammePage({scenario, draft, site, editSite, siteCheck}: {scenario: Scenario; draft: (s: Scenario) => void; site: SiteDocument; editSite: (next: SiteDocument) => void; siteCheck: SiteCheck | null}) {
  const [fields, setFields] = useState<Fields | null>(null);
  const [pipes, setPipes] = useState<ModelPipe[]>([]);
  const [bases, setBases] = useState<string[]>([]);
  const [check, setCheck] = useState<(ProgrammeCheck & {request: string}) | null>(null);
  const [error, setError] = useState('');
  const [notes, setNotes] = useState<string[]>([]);
  // The programme being edited. It is stored in the site file's [programme]
  // table when that table can hold it, and in the scenario otherwise.
  const [form, setForm] = useState<Programme | null | undefined>(undefined);
  const written = useRef('');
  const upload = useRef<HTMLInputElement>(null);
  useEffect(() => { void api<Fields>('/operations/fields').then(setFields).catch(e => setError((e as Error).message)); }, []);
  useEffect(() => {
    void api<{pipes: ModelPipe[]}>('/programme/pipes').then(v => setPipes(v.pipes)).catch(e => setError((e as Error).message));
    void api<{bases: string[]}>('/sites/templates').then(v => setBases(v.bases)).catch(() => undefined);
  }, []);
  const content = scenario.definition.site.content;
  const siteTable = JSON.stringify(site.programme ?? null);
  // Load the form from the scenario or the site file, unless the table is the one this page wrote.
  useEffect(() => {
    if (scenario.definition.programme) { if (form === undefined) setForm(scenario.definition.programme); return; }
    if (!site.programme) { if (form === undefined) setForm(null); return; }
    if (siteTable === written.current && form !== undefined) return;
    void api<{programme: Programme | null; notes: string[]}>('/programme/import', {method: 'POST', body: JSON.stringify({toml: content})})
      .then(v => { written.current = siteTable; setForm(v.programme); setNotes(v.notes); })
      .catch(e => setError((e as Error).message));
  }, [siteTable]);
  const request = useMemo(() => JSON.stringify({programme: form ?? defaultReference(), site_toml: content}), [form, content]);
  useEffect(() => {
    if (form === undefined) return;
    const controller = new AbortController();
    const timer = setTimeout(() => { void api<ProgrammeCheck>('/programme/check', {method: 'POST', body: request, signal: controller.signal}).then(c => setCheck({...c, request})).catch(e => { if ((e as Error).name !== 'AbortError') setError((e as Error).message); }); }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [request, form === undefined]);
  // Store the checked form: in the site file where its table can hold it.
  useEffect(() => {
    if (form === undefined || !check || check.request !== request) return;
    const table = form && check.valid ? check.toml_table : null;
    const nextSite = {...site};
    if (table) nextSite.programme = table; else delete nextSite.programme;
    if (JSON.stringify(nextSite.programme ?? null) !== siteTable) { written.current = JSON.stringify(nextSite.programme ?? null); editSite(nextSite); }
    const keep = form && !table ? form : null;
    if (JSON.stringify(scenario.definition.programme ?? null) !== JSON.stringify(keep)) {
      const definition = {...scenario.definition};
      if (keep) definition.programme = keep; else delete definition.programme;
      draft({...scenario, definition});
    }
  }, [check]);
  const programme = form ?? null;
  const importFile = async (file: File) => {
    try {
      const text = await file.text();
      const value = await api<{programme: Programme | null; notes: string[]; operations: Operations | null}>('/programme/import', {method: 'POST', body: JSON.stringify({toml: text})});
      const next = {...site};
      delete next.operations;
      if (value.operations) next.operations = value.operations;
      editSite(next);
      setForm(value.programme);
      setNotes([`Read the programme and operations tables from ${file.name}. The site inputs on the Site page are unchanged.`, ...value.notes]);
    } catch (e) { setError((e as Error).message); }
  };
  const exportFile = async () => {
    try {
      const document = {...site};
      if (programme && !check?.toml_table) { setError(check?.toml_note || 'Check the programme first.'); return; }
      const response = await fetch('/api/v1/sites/export', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({document})});
      if (!response.ok) throw new Error(await response.text());
      download(`${String(site.name || 'site').replace(/[^A-Za-z0-9_-]+/g, '-').toLowerCase()}.toml`, await response.text());
    } catch (e) { setError((e as Error).message); }
  };
  return <div className="programme-page">
    <div className="programme-actions">
      <button className="secondary-button" onClick={() => upload.current?.click()}>Import programme and operations from a site file</button>
      <input ref={upload} type="file" accept=".toml,text/plain" hidden onChange={e => { const f = e.target.files?.[0]; if (f) void importFile(f); e.target.value = ''; }} />
      <button className="secondary-button" onClick={() => void exportFile()}>Export site file with these tables</button>
    </div>
    {error && <p className="field-error" role="alert">{error}</p>}
    {notes.map((n, i) => <p key={i} className="notice">{n}</p>)}
    {form === undefined ? <p role="status">Loading the programme…</p> : <HydraulicProgramme programme={programme} set={setForm} inSite={!!site.programme} pipes={pipes} check={check} />}
    {fields ? <OperationsTable site={site} editSite={editSite} siteCheck={siteCheck} bases={bases} fields={fields} /> : <p role="status">Loading the operating choices…</p>}
  </div>;
}
