import React, {useEffect, useMemo, useRef, useState} from 'react';
import {api} from './apiClient';
import {DepthPlot} from './DepthPlot';
import {UnitInput, useFormat} from './UnitsContext';
import {series} from './units';

type Geotherm = {kind: 'points'; points_tvd_m_temperature_C: [number, number][]; gradient_below_K_m: number};

// Formation temperature against vertical depth for a circulation scenario.
// The plotted line comes from the model's own geotherm function.
export function GeothermEditor({geotherm, depth, change}: {geotherm: Geotherm; depth: number; change: (next: Geotherm) => void}) {
  const {system, symbol} = useFormat();
  const [preview, setPreview] = useState<{depth_m: number[]; temperature_C: number[]} | null>(null);
  const [error, setError] = useState('');
  const points = geotherm.points_tvd_m_temperature_C;
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api<{depth_m: number[]; temperature_C: number[]}>('/geotherm/preview', {method: 'POST', signal: controller.signal, body: JSON.stringify({points_tvd_m_temperature_C: points, gradient_below_K_m: geotherm.gradient_below_K_m, depth_m: Math.max(depth, 1)})})
        .then(value => { setPreview(value); setError(''); }).catch(e => { if ((e as Error).name !== 'AbortError') setError((e as Error).message); });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [geotherm, depth]);
  const latest = useRef(geotherm);
  latest.current = geotherm;
  const update = (edit: (draft: Geotherm) => void) => { const next = structuredClone(latest.current); edit(next); latest.current = next; change(next); };
  const plotted = useMemo(() => preview ? [
    {name: 'Formation temperature', x: series(preview.temperature_C, 'temperature', system), y: series(preview.depth_m, 'length', system), color: '#a1501f'},
    {name: 'Entered points', x: series(points.map(p => p[1]), 'temperature', system), y: series(points.map(p => p[0]), 'length', system), color: '#25313b', markers: true, dash: 'dot' as const},
  ] : [], [preview, system, points]);
  return <div className="geotherm-editor">
    <span className="eyebrow">FORMATION TEMPERATURE · VERTICAL DEPTH</span>
    <table className="edit-table"><thead><tr><th>Vertical depth ({symbol('length')})</th><th>Temperature ({symbol('temperature')})</th><th /></tr></thead><tbody>
      {points.map((point, index) => <tr key={index}>
        <td>{index === 0 ? <span className="fixed-cell">0</span> : <UnitInput label={`Geotherm point ${index + 1} depth`} value={point[0]} quantity="length" change={v => update(g => { g.points_tvd_m_temperature_C[index][0] = v; })} />}</td>
        <td><UnitInput label={`Geotherm point ${index + 1} temperature`} value={point[1]} quantity="temperature" change={v => update(g => { g.points_tvd_m_temperature_C[index][1] = v; })} /></td>
        <td><button className="row-button" aria-label={`Remove geotherm point ${index + 1}`} disabled={index === 0 || points.length <= 2} onClick={() => update(g => { g.points_tvd_m_temperature_C.splice(index, 1); })}>×</button></td>
      </tr>)}
    </tbody></table>
    <button className="secondary-button small-button" onClick={() => update(g => { const last = g.points_tvd_m_temperature_C.at(-1)!; g.points_tvd_m_temperature_C.push([last[0] + 500, last[1]]); })}>Add a deeper point</button>
    <label className="field"><span className="field-label">Gradient below the last point<span className="field-unit">{symbol('temperature_gradient')}</span></span>
      <div className="number-row"><UnitInput label="Gradient below the last point" value={geotherm.gradient_below_K_m} quantity="temperature_gradient" change={v => update(g => { g.gradient_below_K_m = v; })} /><span>{symbol('temperature_gradient')}</span></div></label>
    <small>Depths must increase from zero. The scenario check reports any other problem.</small>
    {error && <p className="field-error" role="alert">{error}</p>}
    {preview && <DepthPlot series={plotted} xLabel={`Temperature (${symbol('temperature')})`} yLabel={`Vertical depth (${symbol('length')})`} label="Formation temperature against vertical depth" height={260} />}
  </div>;
}
