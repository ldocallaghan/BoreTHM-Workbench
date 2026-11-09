import React, {useEffect, useState} from 'react';
import {api} from './apiClient';
import {UnitInput, useFormat} from './UnitsContext';
import type {Quantity} from './units';

type Lever = {key: string; label: string; quantity: string | null; kind: 'number' | 'pipe'; value: number | string | null; unit?: string | null; pipe?: string | null; available: boolean; reason: string | null};
type Options = {levers: Lever[]; interpretation: string; max_cases: number; base_duration_s: number | null};
type Outcome = {verdict: string | null; controlling_constraint: string | null; tool_margin_C: number | null; tool_margin_state: string | null; drilling_window_width_kg_m3: number | null; trip_window_width_kg_m3: number | null; pump_margin_Pa: number | null; pump_margin_state: string | null; flow: number | null; flow_unit: string | null};
type Case = {index: number; run_id: string; levers: Record<string, number | string>; state: string; outcome: Outcome | null; change_from_base: Record<string, number | null>; duration_s: number | null; failure: {code: string; message: string} | null};
type Cell = {run_id: string; state: string; verdict: string | null; controlling_constraint: string | null} | null;
export type Study = {id: string; title: string; mode: 'one_at_a_time' | 'grid'; base_run_id: string; base_outcome: Outcome | null; levers: {lever: string; label: string; values: (number | string)[]}[]; state: string; total_cases: number; finished_cases: number; state_counts: Record<string, number>; estimate: {base_duration_s: number | null; total_s: number | null; note: string}; remaining_estimate_s: number | null; cases: Case[]; grid: {rows: Study['levers'][number]; columns: Study['levers'][number]; cells: Cell[][]} | null; interpretation: string; differences: string; figures?: Record<string, string>};
type Plan = {case_count: number; estimate: Study['estimate']; cases: {index: number; levers: Record<string, number | string>; title: string}[]};
type Choice = {lever: string; values: (number | string | null)[]};
const METRICS: [keyof Outcome, string, Quantity][] = [['tool_margin_C', 'Tool margin', 'temperature_difference'], ['drilling_window_width_kg_m3', 'Drilling fluid window width', 'density'], ['trip_window_width_kg_m3', 'Trip fluid window width', 'density'], ['pump_margin_Pa', 'Pump margin', 'pressure']];
const finished = new Set(['completed', 'failed', 'cancelled', 'timed_out']);
const verdictClass = (v: string | null | undefined) => v === 'GO' ? 'go' : v === 'NO-GO' ? 'no-go' : v === 'CONDITIONAL' ? 'conditional' : 'neutral';
const minutes = (s: number | null | undefined) => s == null ? 'unknown' : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;

function leverValue(lever: Lever | undefined, value: number | string | null | undefined, format: (v: number, q: Quantity) => string) {
  if (value == null) return 'Not returned';
  if (!lever || lever.kind === 'pipe' || typeof value === 'string') return String(value);
  if (!lever.quantity) return `${value} ${lever.unit ?? ''}`.trim();
  return format(value, lever.quantity as Quantity);
}

function ValueInputs({lever, choice, change, pipes}: {lever: Lever; choice: Choice; change: (c: Choice) => void; pipes: string[]}) {
  const set = (i: number, v: number | string) => change({...choice, values: choice.values.map((x, j) => j === i ? v : x)});
  return <div className="lever-values">{choice.values.map((v, i) => <span key={i} className="lever-value">
    {lever.kind === 'pipe'
      ? <select aria-label={`${lever.label} value ${i + 1}`} value={String(v ?? '')} onChange={e => set(i, e.target.value)}><option value="">Choose</option>{pipes.map(p => <option key={p} value={p}>{p}</option>)}</select>
      : lever.quantity
        ? <UnitInput label={`${lever.label} value ${i + 1}`} value={v as number | null} quantity={lever.quantity as Quantity} change={x => set(i, x)} />
        : <input aria-label={`${lever.label} value ${i + 1}`} type="number" step="any" value={v == null ? '' : String(v)} onChange={e => Number.isFinite(e.target.valueAsNumber) && set(i, e.target.valueAsNumber)} />}
    <button className="text-button" disabled={choice.values.length <= 2} onClick={() => change({...choice, values: choice.values.filter((_, j) => j !== i)})} aria-label={`Remove ${lever.label} value ${i + 1}`}>Remove</button>
  </span>)}
    <button className="text-button" disabled={choice.values.length >= 5} onClick={() => change({...choice, values: [...choice.values, null]})}>Add a value</button>
  </div>;
}

function StudyView({study, levers, onOpen, refresh}: {study: Study; levers: Lever[]; onOpen: (rid: string) => void; refresh: () => void}) {
  const {system, format} = useFormat();
  const byKey = Object.fromEntries(levers.map(l => [l.key, l]));
  const running = study.state !== 'finished';
  const cancel = () => void api(`/levers/${study.id}/cancel`, {method: 'POST'}).then(refresh);
  const metric = (o: Outcome | null, key: keyof Outcome, q: Quantity) => {
    const v = o?.[key] as number | null | undefined;
    return v == null ? (o ? 'Not returned' : '-') : format(v, q);
  };
  const change = (c: Case, key: string, q: Quantity) => {
    const v = c.change_from_base[key];
    return v == null ? null : <small className={v < 0 ? 'fail' : ''}> ({v >= 0 ? '+' : ''}{format(v, q)})</small>;
  };
  return <section className="result-section lever-study" aria-labelledby={`study-${study.id}`}>
    <div className="section-heading"><h2 id={`study-${study.id}`}>{study.title}</h2>
      <span>{study.finished_cases} of {study.total_cases} cases finished{running && study.remaining_estimate_s != null ? `; about ${minutes(study.remaining_estimate_s)} left` : ''}</span></div>
    <p className="notice">{study.interpretation}</p>
    {running && <div className="lever-progress"><progress max={study.total_cases} value={study.finished_cases} aria-label="Cases finished" /><button className="secondary-button" onClick={cancel}>Cancel the remaining cases</button><span className="small muted">Finished cases are kept.</span></div>}
    {!running && <p className="lever-exports"><a className="secondary-button" href={`/api/v1/levers/${study.id}/report?units=${system}`} download>Export the study report</a> <a className="secondary-button" href={`/api/v1/levers/${study.id}/export?units=${system}`} download>Export the study archive</a></p>}
    <p className="small">Base run: <strong className={verdictClass(study.base_outcome?.verdict)}>{study.base_outcome?.verdict ?? 'Not returned'}</strong>. {study.base_outcome?.controlling_constraint}</p>
    <div className="table-scroll"><table className="result-table lever-table"><thead><tr><th>#</th><th>Lever and value</th><th>State</th><th>Verdict</th><th>Controlling constraint, in the model's wording</th>{METRICS.map(([, label]) => <th key={label}>{label}</th>)}<th></th></tr></thead><tbody>
      {study.cases.map(c => <tr key={c.index}>
        <td>{c.index + 1}</td>
        <td>{Object.entries(c.levers).map(([k, v]) => <div key={k}>{byKey[k]?.label ?? k}: {leverValue(byKey[k], v, format)}</div>)}</td>
        <td>{c.state}{c.failure ? <small> · {c.failure.message}</small> : null}</td>
        <td className={verdictClass(c.outcome?.verdict)}>{c.outcome?.verdict ?? '-'}</td>
        <td>{c.outcome?.controlling_constraint ?? '-'}</td>
        {METRICS.map(([key, , q]) => <td key={key} className="n">{metric(c.outcome, key, q)}{change(c, key, q)}</td>)}
        <td>{c.state === 'completed' && <button className="text-button" onClick={() => onOpen(c.run_id)}>Open</button>}</td>
      </tr>)}
    </tbody></table></div>
    <p className="small muted">{study.differences} Changes in brackets. The tool margin is the smallest of the drilling, connection and staged run-in margins; the pump margin is the smaller of drilling and the trip.</p>
    {study.figures && Object.keys(study.figures).length > 0 && <div className="tornado-grid">{Object.entries(study.figures).map(([key, svg]) => <figure key={key} className="result-figure"><div className="svg-figure" dangerouslySetInnerHTML={{__html: svg}} /></figure>)}</div>}
    {study.grid && <div className="table-scroll"><table className="result-table lever-grid"><thead><tr><th>{study.grid.rows.label} \ {study.grid.columns.label}</th>{study.grid.columns.values.map((v, i) => <th key={i}>{leverValue(byKey[study.grid!.columns.lever], v, format)}</th>)}</tr></thead><tbody>
      {study.grid.cells.map((row, i) => <tr key={i}><th>{leverValue(byKey[study.grid!.rows.lever], study.grid!.rows.values[i], format)}</th>{row.map((cell, j) => <td key={j} className={`grid-cell ${verdictClass(cell?.verdict)}`}>
        <strong>{cell?.verdict ?? cell?.state ?? '-'}</strong>{cell?.controlling_constraint && <small>{cell.controlling_constraint}</small>}
        {cell?.state === 'completed' && <button className="text-button" onClick={() => onOpen(cell.run_id)}>Open</button>}</td>)}</tr>)}
    </tbody></table><p className="small muted">Each cell is a computed case; no value between cells is implied.</p></div>}
  </section>;
}

export function LeversPage({base, onOpen}: {base: {run_id: string; wall_clock_duration_s?: number; imported?: boolean}; onOpen: (rid: string) => void}) {
  const {format, system} = useFormat();
  const [options, setOptions] = useState<Options | null>(null);
  const [pipes, setPipes] = useState<string[]>([]);
  const [mode, setMode] = useState<'one_at_a_time' | 'grid'>('one_at_a_time');
  const [choices, setChoices] = useState<Choice[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState('');
  const [study, setStudy] = useState<Study | null>(null);
  const [previous, setPrevious] = useState<{id: string; title: string; created_at: string; base_run_id: string}[]>([]);
  useEffect(() => {
    if (base.imported) return;
    void api<Options>('/levers/options?base_run_id=' + base.run_id).then(setOptions).catch(e => setError((e as Error).message));
    void api<{pipes: {name: string}[]}>('/programme/pipes').then(v => setPipes(v.pipes.map(p => p.name))).catch(() => undefined);
    void api<{items: typeof previous}>('/levers').then(v => setPrevious(v.items.filter(x => x.base_run_id === base.run_id))).catch(() => undefined);
  }, [base.run_id]);
  const load = (id: string) => api<Study>(`/levers/${id}?units=${system}`).then(setStudy).catch(e => setError((e as Error).message));
  useEffect(() => {
    if (!study || study.state === 'finished') return;
    const timer = setInterval(() => void load(study.id), 3000);
    return () => clearInterval(timer);
  }, [study?.id, study?.state]);
  useEffect(() => { if (study) void load(study.id); }, [system]);
  const request = {base_run_id: base.run_id, mode, levers: choices.map(c => ({lever: c.lever, values: c.values}))};
  const complete = choices.length > 0 && choices.every(c => c.values.length >= 2 && c.values.every(v => v != null && v !== '')) && (mode === 'one_at_a_time' || choices.length === 2);
  useEffect(() => {
    setPlan(null);
    if (!complete) return;
    const controller = new AbortController();
    const timer = setTimeout(() => void api<Plan>('/levers/plan', {method: 'POST', body: JSON.stringify(request), signal: controller.signal}).then(p => { setPlan(p); setError(''); }).catch(e => { if ((e as Error).name !== 'AbortError') setError((e as Error).message); }), 400);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [JSON.stringify(request), complete]);
  if (base.imported) return <p className="notice">Design levers start from an assessment run in this application. Open the base assessment from the history.</p>;
  if (!options) return <p role="status">{error || 'Loading the design levers…'}</p>;
  const toggle = (lever: Lever, on: boolean) => setChoices(on ? [...choices, {lever: lever.key, values: lever.kind === 'pipe' ? [String(lever.value ?? ''), ''] : [lever.value as number | null, null]}] : choices.filter(c => c.lever !== lever.key));
  const start = async () => {
    try {
      const next = await api<Study>('/levers', {method: 'POST', headers: {'Idempotency-Key': crypto.randomUUID()}, body: JSON.stringify(request)});
      setStudy(next); setChoices([]); void load(next.id);
    } catch (e) { setError((e as Error).message); }
  };
  return <div className="levers-page">
    <section className="result-section" aria-labelledby="levers-heading">
      <div className="section-heading"><h2 id="levers-heading">What changes the verdict</h2></div>
      <p className="notice">{options.interpretation}</p>
      <div className="choice-row" role="radiogroup" aria-label="Study kind">
        <label className="choice"><input type="radio" checked={mode === 'one_at_a_time'} onChange={() => setMode('one_at_a_time')} />One lever at a time</label>
        <label className="choice"><input type="radio" checked={mode === 'grid'} onChange={() => setMode('grid')} />Two levers as a grid</label>
      </div>
      <div className="table-scroll"><table className="result-table lever-choices"><thead><tr><th>Lever</th><th>Base value</th><th>Values to compute (2 to 5)</th></tr></thead><tbody>
        {options.levers.map(lever => {
          const choice = choices.find(c => c.lever === lever.key);
          return <tr key={lever.key} className={lever.available ? '' : 'muted'}>
            <th scope="row"><label className="choice"><input type="checkbox" disabled={!lever.available || (mode === 'grid' && !choice && choices.length >= 2)} checked={!!choice} onChange={e => toggle(lever, e.target.checked)} />{lever.label}</label>{!lever.available && <small>{lever.reason}</small>}</th>
            <td>{leverValue(lever, lever.value, format)}{lever.key === 'insulated_length' && lever.pipe ? <small> ({lever.pipe})</small> : null}</td>
            <td>{choice ? <ValueInputs lever={lever} choice={choice} pipes={pipes} change={next => setChoices(choices.map(c => c.lever === lever.key ? next : c))} /> : null}</td>
          </tr>;
        })}
      </tbody></table></div>
      {error && <p className="field-error" role="alert">{error}</p>}
      {plan && <div className="lever-plan">
        <p><strong>{plan.case_count} assessments</strong>, run one at a time. Estimate: about {minutes(plan.estimate.total_s)}, from the base run's {minutes(plan.estimate.base_duration_s)}. {plan.estimate.note}</p>
        <button className="run-button" onClick={() => void start()}>Start the study</button></div>}
      {!plan && choices.length > 0 && !error && <p className="small muted">Give each chosen lever at least two values{mode === 'grid' ? ', and choose exactly two levers for a grid' : ''}.</p>}
    </section>
    {study && <StudyView study={study} levers={options.levers} onOpen={onOpen} refresh={() => void load(study.id)} />}
    {previous.length > 0 && <section className="result-section"><div className="section-heading"><h2>Earlier studies from this assessment</h2></div>
      <ul>{previous.map(p => <li key={p.id}><button className="text-button" onClick={() => void load(p.id)}>{p.title}</button> <small>{new Date(p.created_at).toLocaleString('en-GB')}</small></li>)}</ul></section>}
  </div>;
}
