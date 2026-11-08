import React, {useEffect, useMemo, useRef, useState} from 'react';
import {api} from './apiClient';
import {DepthPlot, type DepthSeries} from './DepthPlot';
import {UnitInput, useFormat} from './UnitsContext';
import {series, toSI, type Quantity} from './units';

// A site document has the keys and SI values of a BoreTHM site TOML file.
export type SiteDocument = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
type Issue = {field: string; message: string};
type StressProfile = {label: string; Sv_Pa: number[]; Shmin_Pa: number[]; SHmax_Pa: number[]; Pp_Pa: number[]; z_data_m: number[] | null};
export type SiteCheck = {toml: string | null; valid: boolean; errors: Issue[]; warnings: Issue[]; tier: string | null; missing: {input: string; basis: string}[]; profiles: null | {depth_m: number[]; temperature_C: (number | null)[]; stress: StressProfile[]; target_depth_m: number; target_T_C: number}};
type Template = {key: string; name: string};

export const PROVENANCE_KEYS = ['stress', 'strength', 'pore pressure', 'temperature', 'well check', 'conductivity', 'stiffness'];
const CASE_FIELDS: [string, string, Quantity | null][] = [
  ['Sv_grad', 'Sv gradient', 'pressure_gradient'], ['Sv_int', 'Sv at surface', 'pressure'],
  ['Shmin_grad', 'Shmin gradient', 'pressure_gradient'], ['Shmin_int', 'Shmin at surface', 'pressure'],
  ['SHmax_grad', 'SHmax gradient', 'pressure_gradient'], ['SHmax_int', 'SHmax at surface', 'pressure'],
  ['Pp_grad', 'Pore-pressure gradient', 'pressure_gradient'], ['Pp_datum', 'Static fluid level depth', 'length'],
  ['mu', 'Friction coefficient for this case', null],
];

export function blankSite(): SiteDocument {
  return {
    name: '', target_depth: null, target_T: null,
    Sv_grad: null, K0_min: null, SHmax_over_Sv: null, rho_fluid_grad: null,
    k_rock: null, E_rock: null, UCS: null, T_inj: 40, mu: 0.8,
    geotherm: {form: 'points', points: [[0, null]], gradient_below: null},
    stress_cases: [],
    data_basis: Object.fromEntries(PROVENANCE_KEYS.map(key => [key, ['none', '']])),
  };
}

const issuesFor = (issues: Issue[], field: string, prefix = false) => issues.filter(i => prefix ? i.field === field || i.field.startsWith(field + '.') || i.field.startsWith(field + '[') : i.field === field);

function Messages({errors, warnings}: {errors: Issue[]; warnings: Issue[]}) {
  if (!errors.length && !warnings.length) return null;
  return <div className="field-messages">{errors.map((e, i) => <p key={'e' + i} className="field-error" role="alert">{e.message}</p>)}{warnings.map((w, i) => <p key={'w' + i} className="field-warning">{w.message}</p>)}</div>;
}

function parseRows(text: string, width: number): number[][] {
  const rows = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => line.split(/[\t,;]+|\s{2,}|\s+/).filter(Boolean).map(Number));
  if (!rows.length || rows.some(r => r.length !== width || r.some(n => !Number.isFinite(n)))) throw new Error(`Each row needs ${width} numbers separated by tabs, commas or spaces.`);
  return rows;
}

export function SiteEditor({document, change, onCheck}: {document: SiteDocument; change: (next: SiteDocument) => void; onCheck: (check: SiteCheck | null) => void}) {
  const {system, symbol, format} = useFormat();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [bases, setBases] = useState<string[]>([]);
  const [check, setCheck] = useState<SiteCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  const [paste, setPaste] = useState<{target: 'points' | 'segments' | 'strength'; text: string} | null>(null);
  const [stressCase, setStressCase] = useState(-1);
  const upload = useRef<HTMLInputElement>(null);
  useEffect(() => { void api<{templates: Template[]; bases: string[]}>('/sites/templates').then(v => { setTemplates(v.templates); setBases(v.bases); }).catch(e => setError((e as Error).message)); }, []);
  useEffect(() => {
    setChecking(true);
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api<SiteCheck>('/sites/check', {method: 'POST', body: JSON.stringify({document}), signal: controller.signal})
        .then(value => { setCheck(value); onCheck(value); setChecking(false); setError(''); })
        .catch(e => { if ((e as Error).name !== 'AbortError') { setError((e as Error).message); setChecking(false); onCheck(null); } });
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [document]);
  // Edits apply to the latest document, so keystrokes that arrive before the
  // next render are not lost.
  const latest = useRef(document);
  latest.current = document;
  const edit = (update: (draft: SiteDocument) => void) => { const next = structuredClone(latest.current); update(next); latest.current = next; change(next); };
  const errors = check?.errors || [], warnings = check?.warnings || [];
  const scalar = (key: string, label: string, quantity: Quantity | null, hint?: string, optional = false) => {
    const own = [issuesFor(errors, key), issuesFor(warnings, key)] as const;
    const set = (value: number | null) => edit(d => { if (value == null) delete d[key]; else d[key] = value; });
    return <label className="field site-field" key={key}>
      <span className="field-label">{label}{quantity && <span className="field-unit">{symbol(quantity)}</span>}</span>
      <div className="number-row">{quantity ? <UnitInput label={label} value={document[key]} quantity={quantity} change={set} invalid={own[0].length > 0} /> : <input type="number" step="any" aria-label={label} value={document[key] ?? ''} aria-invalid={own[0].length > 0 || undefined} onChange={e => { const n = e.currentTarget.valueAsNumber; if (Number.isFinite(n)) set(n); }} />}
        {quantity && <span>{symbol(quantity)}</span>}
        {optional && document[key] != null && <button className="row-button" aria-label={`Clear ${label}`} onClick={() => set(null)}>×</button>}</div>
      {hint && <small>{hint}</small>}
      <Messages errors={own[0]} warnings={own[1]} />
    </label>;
  };
  const loadTemplate = async (key: string) => {
    if (key === 'blank') { change(blankSite()); return; }
    try { const value = await api<{document: SiteDocument}>('/sites/templates/' + key); change(value.document); } catch (e) { setError((e as Error).message); }
  };
  const importToml = async (file: File) => {
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error('The site file is larger than 5 MiB.');
      const value = await api<{document: SiteDocument}>('/sites/import', {method: 'POST', body: JSON.stringify({toml: await file.text()})});
      change(value.document); setError('');
    } catch (e) { setError((e as Error).message); }
  };
  const exportToml = async () => {
    try {
      const response = await fetch('/api/v1/sites/export', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({document})});
      if (!response.ok) throw new Error('Complete the marked fields before exporting.');
      const text = await response.text();
      const href = URL.createObjectURL(new Blob([text], {type: 'application/toml'}));
      const link = window.document.createElement('a'); link.href = href; link.download = (document.name || 'site').replace(/[^A-Za-z0-9-]+/g, '-').replace(/^-|-$/g, '').toLowerCase() + '.toml'; link.click();
      setTimeout(() => URL.revokeObjectURL(href), 1000);
    } catch (e) { setError((e as Error).message); }
  };
  const applyPaste = () => {
    if (!paste) return;
    try {
      if (paste.target === 'strength') {
        const rows = paste.text.split(/\r?\n/).map(l => l.trim()).filter(Boolean).map(line => { const parts = line.split(/\t|,/); const value = Number(parts.at(-1)); if (parts.length < 2 || !Number.isFinite(value)) throw new Error('Each row needs a label and a strength.'); return [parts.slice(0, -1).join(',').trim(), toSI(value, 'pressure', system)]; });
        edit(d => { d.strength_cases = rows; });
      } else if (paste.target === 'points') {
        const rows = parseRows(paste.text, 2).map(([z, t]) => [toSI(z, 'length', system), toSI(t, 'temperature', system)]);
        edit(d => { d.geotherm.points = rows; });
      } else {
        const rows = parseRows(paste.text, 3).map(([z, t, g]) => [toSI(z, 'length', system), toSI(t, 'temperature', system), toSI(g, 'temperature_gradient', system)]);
        edit(d => { d.geotherm.segments = rows; });
      }
      setPaste(null); setError('');
    } catch (e) { setError((e as Error).message); }
  };

  const geothermTable = (key: 'geotherm' | 'geotherm_v11') => {
    const g = document[key];
    if (!g) return null;
    const own = [issuesFor(errors, key, true), issuesFor(warnings, key, true)] as const;
    const setG = (update: (g: SiteDocument) => void) => edit(d => update(d[key]));
    return <div className="site-table-block">
      <div className="inline-controls"><label>Form<select aria-label={`${key} form`} value={g.form} onChange={e => setG(x => { if (e.target.value === 'points') { x.form = 'points'; x.points = x.segments ? x.segments.map((s: number[]) => [s[0], s[1]]) : [[0, null]]; x.gradient_below = x.segments?.at(-1)?.[2] ?? null; delete x.segments; delete x.boundary; } else { x.form = 'segments'; x.segments = (x.points || [[0, null]]).map((p: number[]) => [p[0], p[1], x.gradient_below ?? null]); x.boundary = 'below'; delete x.points; delete x.gradient_below; } })}><option value="points">Points</option><option value="segments">Segments</option></select></label>
        {g.form === 'segments' && <label>Value at a boundary<select aria-label={`${key} boundary`} value={g.boundary || 'below'} onChange={e => setG(x => { x.boundary = e.target.value; })}><option value="below">Segment starting there</option><option value="above">Segment ending there</option></select></label>}</div>
      {g.form === 'points' ? <>
        <div className="table-scroll"><table className="edit-table"><thead><tr><th>Depth ({symbol('length')})</th><th>Temperature ({symbol('temperature')})</th><th /></tr></thead><tbody>
          {(g.points || []).map((p: (number | null)[], i: number) => <tr key={i}><td><UnitInput label={`${key} point ${i + 1} depth`} value={p[0]} quantity="length" change={v => setG(x => { x.points[i][0] = v; })} /></td><td><UnitInput label={`${key} point ${i + 1} temperature`} value={p[1]} quantity="temperature" change={v => setG(x => { x.points[i][1] = v; })} /></td><td><button className="row-button" aria-label={`Remove ${key} point ${i + 1}`} disabled={(g.points || []).length <= 1} onClick={() => setG(x => { x.points.splice(i, 1); })}>×</button></td></tr>)}
        </tbody></table></div>
        <div className="row-actions"><button className="secondary-button small-button" onClick={() => setG(x => { x.points = [...(x.points || []), [null, null]]; })}>Add point</button>{key === 'geotherm' && <button className="secondary-button small-button" onClick={() => setPaste({target: 'points', text: ''})}>Paste rows</button>}</div>
        <label className="field"><span className="field-label">Gradient below the deepest point<span className="field-unit">{symbol('temperature_gradient')}</span></span><div className="number-row"><UnitInput label={`${key} gradient below`} value={g.gradient_below} quantity="temperature_gradient" change={v => setG(x => { x.gradient_below = v; })} /><span>{symbol('temperature_gradient')}</span></div></label>
      </> : <>
        <div className="table-scroll"><table className="edit-table"><thead><tr><th>Top depth ({symbol('length')})</th><th>Temperature at top ({symbol('temperature')})</th><th>Gradient ({symbol('temperature_gradient')})</th><th /></tr></thead><tbody>
          {(g.segments || []).map((s: (number | null)[], i: number) => <tr key={i}>{(['length', 'temperature', 'temperature_gradient'] as const).map((q, j) => <td key={j}><UnitInput label={`${key} segment ${i + 1} ${['top depth', 'temperature', 'gradient'][j]}`} value={s[j]} quantity={q} change={v => setG(x => { x.segments[i][j] = v; })} /></td>)}<td><button className="row-button" aria-label={`Remove ${key} segment ${i + 1}`} disabled={(g.segments || []).length <= 1} onClick={() => setG(x => { x.segments.splice(i, 1); })}>×</button></td></tr>)}
        </tbody></table></div>
        <div className="row-actions"><button className="secondary-button small-button" onClick={() => setG(x => { x.segments = [...(x.segments || []), [null, null, null]]; })}>Add segment</button>{key === 'geotherm' && <button className="secondary-button small-button" onClick={() => setPaste({target: 'segments', text: ''})}>Paste rows</button>}</div>
      </>}
      <Messages errors={own[0]} warnings={own[1]} />
    </div>;
  };

  const profile = check?.profiles;
  const temperatureSeries: DepthSeries[] = useMemo(() => profile ? [{name: 'Rock temperature used by the evaluation', x: series(profile.temperature_C as number[], 'temperature', system), y: series(profile.depth_m, 'length', system), color: '#a1501f'}, {name: 'Target', x: series([profile.target_T_C], 'temperature', system), y: series([profile.target_depth_m], 'length', system), color: '#25313b', markers: true}] : [], [profile, system]);
  const stressSeries: DepthSeries[] = useMemo(() => {
    if (!profile) return [];
    const y = series(profile.depth_m, 'length', system);
    const cases = profile.stress.map((c, i) => [c, i] as const).filter(([, i]) => stressCase < 0 || i === stressCase);
    return cases.flatMap(([c, i]) => ([['Sv', c.Sv_Pa, '#344c63'], ['Shmin', c.Shmin_Pa, '#18807a'], ['SHmax', c.SHmax_Pa, '#d0703c'], ['Pore pressure', c.Pp_Pa, '#5554a4']] as const).map(([name, values, color]) => ({name: cases.length > 1 ? `${name} · ${i + 1}` : name, x: series(values, 'pressure', system), y, color, dash: i % 3 === 1 ? 'dash' as const : i % 3 === 2 ? 'dot' as const : undefined})));
  }, [profile, system, stressCase]);

  const stressMode = document.stress_rule ? 'rule' : 'cases';
  const basisRows: [string, [string, string]][] = Object.entries(document.data_basis || {});
  const unattached = [...errors, ...warnings].filter(i => !i.field || !['name', 'target_depth', 'target_T', 'target_depth_v11', 'Sv_grad', 'K0_min', 'SHmax_over_Sv', 'rho_fluid_grad', 'k_rock', 'E_rock', 'UCS', 'T_inj', 'mu', 'temperature_data_to', 'stress_basis', 'coupled'].includes(i.field) && !/^(geotherm|geotherm_v11|stress_cases|stress_rule|strength_cases|data_basis)/.test(i.field));

  return <div className="site-editor">
    <div className="site-toolbar">
      <label>Start from<select aria-label="Start site from" value="" onChange={e => { if (e.target.value) void loadTemplate(e.target.value); }}><option value="">Choose…</option><option value="blank">Blank form</option>{templates.map(t => <option key={t.key} value={t.key}>{t.name}</option>)}</select></label>
      <input ref={upload} type="file" accept=".toml" hidden onChange={e => { if (e.target.files?.[0]) void importToml(e.target.files[0]); e.target.value = ''; }} />
      <button className="secondary-button small-button" onClick={() => upload.current?.click()}>Import TOML</button>
      <button className="secondary-button small-button" onClick={() => void exportToml()} disabled={!check?.toml}>Export TOML</button>
    </div>
    <div className={`site-status ${check?.valid ? 'ok' : 'attention'}`} aria-live="polite">
      <strong>{checking ? 'Checking the site…' : check?.valid ? 'Site inputs accepted by the model' : 'Site inputs need attention'}</strong>
      {check?.tier && <span>Evidence tier: <b>{check.tier}</b>{check.missing.length ? ` · held back by ${check.missing.map(m => `${m.input} (${m.basis})`).join(', ')}` : ''}</span>}
      {!!warnings.length && <span>{warnings.length} warning{warnings.length === 1 ? '' : 's'}</span>}
    </div>
    {error && <p className="field-error" role="alert">{error}</p>}
    <Messages errors={unattached.filter(i => errors.includes(i))} warnings={unattached.filter(i => warnings.includes(i))} />
    {paste && <div className="paste-box"><label>Paste rows in {paste.target === 'strength' ? `label and strength (${symbol('pressure')})` : paste.target === 'points' ? `depth (${symbol('length')}) and temperature (${symbol('temperature')})` : `top depth (${symbol('length')}), temperature (${symbol('temperature')}) and gradient (${symbol('temperature_gradient')})`}<textarea aria-label="Rows to paste" value={paste.text} onChange={e => setPaste({...paste, text: e.target.value})} /></label><div className="row-actions"><button className="secondary-button small-button" onClick={applyPaste}>Replace rows</button><button className="secondary-button small-button" onClick={() => setPaste(null)}>Cancel</button></div></div>}

    <section className="site-section"><h3>Site and target</h3>
      <label className="field site-field"><span className="field-label">Site name</span><input aria-label="Site name" value={document.name ?? ''} onChange={e => edit(d => { d.name = e.target.value; })} /><Messages errors={issuesFor(errors, 'name')} warnings={issuesFor(warnings, 'name')} /></label>
      <div className="field-grid">{scalar('target_depth', 'Target depth', 'length')}{scalar('target_T', 'Target temperature', 'temperature', 'Must match the temperature profile within 5 °C (9 °F) at the target.')}</div>
    </section>

    <section className="site-section"><h3>Formation temperature</h3>
      {geothermTable('geotherm')}
      <div className="field-grid">{scalar('temperature_data_to', 'Deepest measured temperature', 'length', 'Leave empty when unknown.', true)}</div>
      {profile && <DepthPlot series={temperatureSeries} xLabel={`Temperature (${symbol('temperature')})`} yLabel={`Depth (${symbol('length')})`} label="Rock temperature against depth" height={280} />}
      <details className="optional-block" open={!!document.geotherm_v11 || document.target_depth_v11 != null}><summary>Replacement profile for current evaluations (optional)</summary>
        <p className="small muted">When present, the current evaluation uses this profile and target depth in place of the ones above.</p>
        {document.geotherm_v11 ? <>{geothermTable('geotherm_v11')}<button className="secondary-button small-button" onClick={() => edit(d => { delete d.geotherm_v11; })}>Remove replacement profile</button></> : <button className="secondary-button small-button" onClick={() => edit(d => { d.geotherm_v11 = structuredClone(d.geotherm); })}>Add replacement profile</button>}
        <div className="field-grid">{scalar('target_depth_v11', 'Replacement target depth', 'length', undefined, true)}</div>
      </details>
    </section>

    <section className="site-section"><h3>In-situ stress and pore pressure</h3>
      <div className="inline-controls"><label>Stress input<select aria-label="Stress input" value={stressMode} onChange={e => edit(d => { if (e.target.value === 'rule') { delete d.stress_cases; d.stress_rule = {form: 'transitional', Sv_grad: d.Sv_grad ?? null, source: ''}; } else { delete d.stress_rule; d.stress_cases = []; } })}><option value="cases">Stress cases</option><option value="rule">Normal to strike-slip transition rule</option></select></label></div>
      {stressMode === 'cases' ? <>
        {(document.stress_cases || []).map((c: SiteDocument, i: number) => {
          const path = `stress_cases[${i}]`;
          const setC = (update: (c: SiteDocument) => void) => edit(d => update(d.stress_cases[i]));
          return <fieldset key={i} className="stress-case"><legend>Case {i + 1}</legend>
            <label className="field"><span className="field-label">Label</span><input aria-label={`Stress case ${i + 1} label`} value={c.label ?? ''} onChange={e => setC(x => { x.label = e.target.value; })} /></label>
            <div className="field-grid">{CASE_FIELDS.map(([key, label, q]) => <label className="field" key={key}><span className="field-label">{label}{q && <span className="field-unit">{symbol(q)}</span>}</span><div className="number-row">{q ? <UnitInput label={`Stress case ${i + 1} ${label}`} value={c[key]} quantity={q} change={v => setC(x => { x[key] = v; })} /> : <input type="number" step="any" aria-label={`Stress case ${i + 1} ${label}`} value={c[key] ?? ''} onChange={e => { const n = e.currentTarget.valueAsNumber; setC(x => { if (Number.isFinite(n)) x[key] = n; else delete x[key]; }); }} />}{key === 'mu' || key.endsWith('_int') || key.startsWith('Pp') ? <button className="row-button" aria-label={`Clear stress case ${i + 1} ${label}`} onClick={() => setC(x => { delete x[key]; })}>×</button> : null}</div></label>)}
              <label className="field"><span className="field-label">Faulting regime</span><select aria-label={`Stress case ${i + 1} regime`} value={c.regime ?? ''} onChange={e => setC(x => { if (e.target.value) x.regime = e.target.value; else delete x.regime; })}><option value="">Not stated</option><option value="normal">Normal</option><option value="strike-slip">Strike-slip</option><option value="reverse">Reverse</option></select></label>
              <label className="field"><span className="field-label">Measured from<span className="field-unit">{symbol('length')}</span></span><div className="number-row"><UnitInput label={`Stress case ${i + 1} data top`} value={c.z_data?.[0]} quantity="length" change={v => setC(x => { x.z_data = [v, x.z_data?.[1] ?? null]; })} /><button className="row-button" aria-label={`Clear stress case ${i + 1} data range`} onClick={() => setC(x => { delete x.z_data; })}>×</button></div></label>
              <label className="field"><span className="field-label">Measured to<span className="field-unit">{symbol('length')}</span></span><div className="number-row"><UnitInput label={`Stress case ${i + 1} data bottom`} value={c.z_data?.[1]} quantity="length" change={v => setC(x => { x.z_data = [x.z_data?.[0] ?? null, v]; })} /></div></label>
            </div>
            <label className="field"><span className="field-label">Source</span><input aria-label={`Stress case ${i + 1} source`} value={c.source ?? ''} onChange={e => setC(x => { x.source = e.target.value; })} /></label>
            <Messages errors={issuesFor(errors, path, true)} warnings={issuesFor(warnings, path, true)} />
            <button className="secondary-button small-button" onClick={() => edit(d => { d.stress_cases.splice(i, 1); })}>Remove case {i + 1}</button>
          </fieldset>;
        })}
        <button className="secondary-button small-button" onClick={() => edit(d => { d.stress_cases = [...(d.stress_cases || []), {label: `Case ${(d.stress_cases || []).length + 1}`, Sv_grad: null, Shmin_grad: null, SHmax_grad: null, source: ''}]; })}>Add stress case</button>
        {!(document.stress_cases || []).length && <p className="small muted">With no stress case, the model builds one from the historical ratios below.</p>}
        <Messages errors={issuesFor(errors, 'stress_cases')} warnings={issuesFor(warnings, 'stress_cases')} />
      </> : <fieldset className="stress-case"><legend>Transition rule</legend>
        <p className="small muted">SHmax equals Sv. Each fraction places Shmin between the frictional floor (0) and Sv (1).</p>
        <div className="field-grid">
          {([['Sv_grad', 'Sv gradient', 'pressure_gradient'], ['Sv_int', 'Sv at surface', 'pressure'], ['Pp_grad', 'Pore-pressure gradient', 'pressure_gradient']] as const).map(([key, label, q]) => <label className="field" key={key}><span className="field-label">{label}<span className="field-unit">{symbol(q)}</span></span><UnitInput label={`Rule ${label}`} value={document.stress_rule[key]} quantity={q} change={v => edit(d => { d.stress_rule[key] = v; })} /></label>)}
          <label className="field"><span className="field-label">Friction coefficient for the rule</span><input type="number" step="any" aria-label="Rule friction coefficient" value={document.stress_rule.mu ?? ''} onChange={e => { const n = e.currentTarget.valueAsNumber; edit(d => { if (Number.isFinite(n)) d.stress_rule.mu = n; else delete d.stress_rule.mu; }); }} /></label>
          <label className="field"><span className="field-label">Fractions</span><input aria-label="Rule fractions" defaultValue={(document.stress_rule.fractions || []).join(', ')} placeholder="0, 0.25, 0.5, 0.75, 1" onBlur={e => { const parts = e.currentTarget.value.split(',').map(s => s.trim()).filter(Boolean).map(Number); edit(d => { if (parts.length && parts.every(Number.isFinite)) d.stress_rule.fractions = parts; else delete d.stress_rule.fractions; }); }} /></label>
        </div>
        <label className="field"><span className="field-label">Source</span><input aria-label="Rule source" value={document.stress_rule.source ?? ''} onChange={e => edit(d => { d.stress_rule.source = e.target.value; })} /></label>
        <Messages errors={issuesFor(errors, 'stress_rule', true)} warnings={issuesFor(warnings, 'stress_rule', true)} />
      </fieldset>}
      {profile && !!profile.stress.length && <>
        <label className="inline-controls">Show<select aria-label="Stress case shown" value={stressCase} onChange={e => setStressCase(+e.target.value)}><option value={-1}>All cases</option>{profile.stress.map((c, i) => <option key={i} value={i}>{i + 1}: {c.label}</option>)}</select></label>
        <DepthPlot series={stressSeries} xLabel={`Stress and pore pressure, gauge (${symbol('pressure')})`} yLabel={`Depth (${symbol('length')})`} label="Stresses and pore pressure against depth" height={320} />
      </>}
    </section>

    <section className="site-section"><h3>Rock and fluid</h3>
      <div className="field-grid">
        {scalar('k_rock', 'Rock thermal conductivity', 'conductivity')}
        {scalar('E_rock', "Rock stiffness (Young's modulus)", 'pressure')}
        {scalar('mu', 'Friction coefficient', null)}
        {scalar('T_inj', 'Pump inlet temperature', 'temperature')}
      </div>
      <h4>Strength cases</h4>
      <div className="table-scroll"><table className="edit-table"><thead><tr><th>Label</th><th>UCS at 25 °C ({symbol('pressure')})</th><th /></tr></thead><tbody>
        {(document.strength_cases || []).map((row: [string, number | null], i: number) => <tr key={i}><td><input aria-label={`Strength case ${i + 1} label`} value={row[0]} onChange={e => edit(d => { d.strength_cases[i][0] = e.target.value; })} /></td><td><UnitInput label={`Strength case ${i + 1} UCS`} value={row[1]} quantity="pressure" change={v => edit(d => { d.strength_cases[i][1] = v; })} /></td><td><button className="row-button" aria-label={`Remove strength case ${i + 1}`} onClick={() => edit(d => { d.strength_cases.splice(i, 1); if (!d.strength_cases.length) delete d.strength_cases; })}>×</button></td></tr>)}
      </tbody></table></div>
      <div className="row-actions"><button className="secondary-button small-button" onClick={() => edit(d => { d.strength_cases = [...(d.strength_cases || []), [`Case ${(d.strength_cases || []).length + 1}`, null]]; })}>Add strength case</button><button className="secondary-button small-button" onClick={() => setPaste({target: 'strength', text: ''})}>Paste rows</button></div>
      <Messages errors={issuesFor(errors, 'strength_cases', true)} warnings={issuesFor(warnings, 'strength_cases', true)} />
      <details className="optional-block"><summary>Historical inputs required by the site file</summary>
        <p className="small muted">The original evaluation modes use these. Current evaluations use the stress and strength cases above.</p>
        <div className="field-grid">
          {scalar('Sv_grad', 'Overburden gradient', 'pressure_gradient')}
          {scalar('K0_min', 'Shmin / Sv ratio', null)}
          {scalar('SHmax_over_Sv', 'SHmax / Sv ratio', null)}
          {scalar('rho_fluid_grad', 'Fluid pressure gradient', 'pressure_gradient')}
          {scalar('UCS', 'Single UCS at 25 °C', 'pressure')}
          <label className="field"><span className="field-label">Stress basis label</span><input aria-label="Stress basis label" value={document.stress_basis ?? ''} onChange={e => edit(d => { if (e.target.value) d.stress_basis = e.target.value; else delete d.stress_basis; })} /></label>
          <label className="check-row"><input type="checkbox" checked={!!document.coupled} onChange={e => edit(d => { d.coupled = e.target.checked; })} /><span>Use the site's stiffness in the coupled calculation</span></label>
        </div>
      </details>
    </section>

    <section className="site-section"><h3>Provenance</h3>
      <p className="small muted">Stress, strength and well-check provenance decide the evidence tier. A stated basis does not verify the cited source.</p>
      <div className="table-scroll"><table className="edit-table provenance-table"><thead><tr><th>Input</th><th>Basis</th><th>Source</th><th /></tr></thead><tbody>
        {basisRows.map(([key, [basis, source]]) => { const path = `data_basis.${key}`; const own = [issuesFor(errors, path), issuesFor(warnings, path)] as const; return <tr key={key}><td>{key}</td><td><select aria-label={`${key} basis`} value={basis} onChange={e => edit(d => { d.data_basis[key][0] = e.target.value; })}>{(bases.includes(basis) ? bases : [basis, ...bases]).map(b => <option key={b} value={b}>{b}</option>)}</select></td><td><input aria-label={`${key} source`} value={source} onChange={e => edit(d => { d.data_basis[key][1] = e.target.value; })} /><Messages errors={own[0]} warnings={own[1]} /></td><td><button className="row-button" aria-label={`Remove ${key} provenance`} onClick={() => edit(d => { delete d.data_basis[key]; })}>×</button></td></tr>; })}
      </tbody></table></div>
      <div className="row-actions">{PROVENANCE_KEYS.filter(k => !(k in (document.data_basis || {}))).map(k => <button key={k} className="secondary-button small-button" onClick={() => edit(d => { d.data_basis = {...(d.data_basis || {}), [k]: ['none', '']}; })}>Add {k}</button>)}</div>
      {(() => { const orphan = (i: Issue) => i.field === 'data_basis' || (i.field.startsWith('data_basis.') && !(i.field.slice('data_basis.'.length) in (document.data_basis || {}))); return <Messages errors={errors.filter(orphan)} warnings={warnings.filter(orphan)} />; })()}
    </section>
    {document.programme && <section className="site-section"><h3>Hydraulic programme</h3><p className="small muted">This site file supplies its own hydraulic programme. The editor keeps it unchanged.</p><details><summary>Programme values</summary><pre>{JSON.stringify(document.programme, null, 2)}</pre></details></section>}
    {profile && <p className="small muted">Plots sample the site loaded by the model: {format(profile.target_depth_m, 'length')} target, {format(profile.target_T_C, 'temperature')}.</p>}
  </div>;
}
