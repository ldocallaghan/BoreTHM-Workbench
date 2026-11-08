import React, {useEffect, useState} from 'react';
import {api} from './apiClient';
import {useFormat} from './UnitsContext';
import type {Quantity} from './units';
import './results.css';

type Range = [number, number];
type Comparison = {
  id: string; title: string; well: string; model: string; measurement: string; samples: number;
  runs: {run: string; position_ft?: number; samples: number; measured_mean_C: number; model_mean_C: number | Range; bias_C: number | Range; rmse_C: number | Range; revised_bias_C?: number | null}[];
  pooled: unknown; return?: {samples: number; bias_C: number; rmse_C: number};
  modelled_range_note: string; revision_note?: string; covers: string[]; does_not_cover: string[]; figure: string;
};
type RangeRow = {input: string; label: string; quantity?: Quantity; range?: Range | null; values?: string[]; notes?: Record<string, string>; sources: string[] | Record<string, string[]>; basis?: Record<string, string | null>};
type Evidence = {comparisons: Comparison[]; tested_range: {rows: RangeRow[]; conditions: string; pipe_rule: string}; sources: {tag: string; commit: string; repository: string}};

const names: Record<string, string> = {forge16a: 'FORGE 16A', forge16b: 'FORGE 16B'};

export function FieldComparisons() {
  const {system, format} = useFormat();
  const [data, setData] = useState<Evidence | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { void api<Evidence>('/evidence?units=' + system).then(setData).catch(e => setError((e as Error).message)); }, [system]);
  // Published comparison statistics keep two decimal places.
  const temperature = (value: number | Range, quantity: Quantity = 'temperature') => Array.isArray(value) ? (value[0] === value[1] ? format(value[0], quantity, 2) : `${format(value[0], quantity, 2)} to ${format(value[1], quantity, 2)}`) : format(value, quantity, 2);
  if (!data) return <main className="main evidence-page"><p>{error || 'Loading the field comparisons…'}</p></main>;
  return <main className="main evidence-page">
    <div className="breadcrumb">Workspace <span>/</span> Field comparisons</div>
    <div className="page-heading"><div><div className="eyebrow"><span className="tiny-line" />PUBLISHED WITH BORETHM {data.sources.tag}</div><h1>Field comparisons</h1><div className="page-subtitle"><span>Measured downhole temperatures against the model, as published in BoreTHM's validation guide. Commit <code>{data.sources.commit.slice(0, 12)}</code>.</span></div></div></div>
    <section className="result-section"><div className="section-heading"><h2>Conditions the comparisons cover</h2></div>
      <p>{data.tested_range.conditions} Every circulation run and assessment lists its inputs outside these ranges. The list never blocks a run.</p>
      <div className="table-scroll"><table className="result-table"><thead><tr><th>Input</th><th>Field-tested range</th><th>From</th><th>Basis</th></tr></thead><tbody>
        {data.tested_range.rows.map(row => <tr key={row.input}><td>{row.label}</td>
          <td>{row.values ? row.values.map(v => row.notes?.[v] ? `${v} (only ${row.notes[v]})` : v).join('; ') : row.range ? `${format(row.range[0], row.quantity!)} to ${format(row.range[1], row.quantity!)}` : 'Not recorded'}</td>
          <td>{Array.isArray(row.sources) ? row.sources.map(s => names[s] || s).join(', ') : Object.entries(row.sources).map(([value, list]) => `${value}: ${list.map(s => names[s] || s).join(', ')}`).join('; ')}</td>
          <td className="small">{row.basis ? Object.entries(row.basis).map(([k, v]) => `${names[k] || k}: ${v}`).join('. ') : ''}</td></tr>)}
      </tbody></table></div>
      <p className="small muted">{data.tested_range.pipe_rule}</p>
    </section>
    {data.comparisons.map(c => <section className="result-section" key={c.id} aria-labelledby={`evidence-${c.id}`}>
      <div className="section-heading"><h2 id={`evidence-${c.id}`}>{c.title}</h2><span>{c.well}</span></div>
      <p>{c.measurement}. {c.samples} samples. {c.model}.</p>
      <div className="evidence-columns">
        <div><h3>Covers</h3><ul>{c.covers.map(t => <li key={t}>{t}</li>)}</ul></div>
        <div><h3>Does not cover</h3><ul>{c.does_not_cover.map(t => <li key={t}>{t}</li>)}</ul></div>
      </div>
      <figure className="result-figure"><div className="svg-figure" dangerouslySetInnerHTML={{__html: c.figure}} /><figcaption>{c.modelled_range_note}</figcaption></figure>
      <div className="table-scroll"><table className="result-table"><thead><tr><th>Run</th>{c.id === 'forge16b' && <th>Sensor above bit</th>}<th>Samples</th><th>Measured mean</th><th>Modelled mean</th><th>Bias (modelled minus measured)</th><th>RMSE</th>{c.id === 'forge16b' && <th>Revised bias, version 2</th>}</tr></thead><tbody>
        {c.runs.map((r, i) => <tr key={i}><td>{r.run}</td>{c.id === 'forge16b' && <td>{r.position_ft} ft</td>}<td className="n">{r.samples}</td><td className="n">{temperature(r.measured_mean_C)}</td><td className="n">{temperature(r.model_mean_C)}</td><td className="n">{temperature(r.bias_C, 'temperature_difference')}</td><td className="n">{temperature(r.rmse_C, 'temperature_difference')}</td>{c.id === 'forge16b' && <td className="n">{r.revised_bias_C == null ? '' : temperature(r.revised_bias_C, 'temperature_difference')}</td>}</tr>)}
      </tbody></table></div>
      {c.id === 'forge16a' && <p>Pooled bit-box comparison: RMSE {temperature((c.pooled as {rmse_C: Range}).rmse_C, 'temperature_difference')}, bias {temperature((c.pooled as {bias_C: Range}).bias_C, 'temperature_difference')}. Surface return: bias {format(c.return!.bias_C, 'temperature_difference', 2)}, RMSE {format(c.return!.rmse_C, 'temperature_difference', 2)} over {c.return!.samples} samples.</p>}
      {c.id === 'forge16b' && <><p>{c.revision_note}</p><div className="table-scroll"><table className="result-table"><thead><tr><th>Pooled group</th><th>Samples</th><th>Bias</th><th>RMSE</th></tr></thead><tbody>{(c.pooled as {group: string; samples: number; bias_C: number; rmse_C: number}[]).map(p => <tr key={p.group}><td>{p.group}</td><td className="n">{p.samples}</td><td className="n">{format(p.bias_C, 'temperature_difference', 2)}</td><td className="n">{format(p.rmse_C, 'temperature_difference', 2)}</td></tr>)}</tbody></table></div></>}
    </section>)}
  </main>;
}
