import {api} from './apiClient';
import {PhysicsChoice, PhysicsRecord} from './PhysicsChoice';
import {formatApiError} from './apiErrors';
import React, { useEffect, useRef, useState } from 'react';
import Plotly from 'plotly.js-dist-min';
import { ArrowDownToLine, Check, ChevronDown, FileJson, FlaskConical, LoaderCircle, Play, ShieldCheck, Square, TriangleAlert, Upload, X } from 'lucide-react';
import type { components } from './api.generated';
import './assessment.css';

type AssessmentScenario = components['schemas']['AssessmentScenario'];
type Warning = { code: string; message: string; path: string };
type Programme = { pipe: string; flow: number; flow_unit: string; mass_flow_kg_s: number; bit_C: number; return_C: number; spp_Pa: number; backpressure_Pa: number; fluid: null | { rho_surface: number; sg: number }; hydraulic_configuration: unknown; drilling_prescribed_rho_surface: number; trip_prescribed_rho_surface: number };
type Window = { open: boolean; lo: number; hi: number; fluid: number | null; note?: string; lo_by: { state: string; case: string; md: number | null }; hi_by: { state: string; case: string; md: number | null } };
type Report = { status: { label: string; reasons: string[] }; programme: Programme; site: { name: string; target_depth_m: number; target_T_C: number; tier: string }; evaluation: string; reading: { calculation: string; scenarios_at_target: string; whole_programme: string; equipment: string; controlling_constraint: string }; components: Record<string, unknown>; windows: { drilling_fluid: Window; trip_fluid: Window; refinement?: { converged: boolean } }; cycle: { cases: Record<string, unknown>[]; site_verdict: string; deciding_case: string; verdicts: Record<string, string> }; tools: unknown };
type AssessmentManifest = { physics?:string; physics_settings?:unknown; mode: 'site_assessment'; run_id: string; title: string; acceptance: string; resolved_scenario: AssessmentScenario; assessment: Report; numerical_profile: { effective_coupled: boolean }; warnings: Warning[]; validity: { solver_converged: boolean; energy_status: string; domain_status: string; assessment_status: string }; profile: { md_m: number[]; bore_temperature_C: number[]; annulus_temperature_C: number[]; formation_temperature_C: number[] }; wall_clock_duration_s: number; failure?: { code: string; message: string }; diagnostics?: { assessment?: Report }; imported?: boolean };
type Job = { id: string; mode?: string; state: string; scenario_id: string; scenario_revision: number; created_at: string; manifest: AssessmentManifest | null };
const finished = new Set(['completed', 'failed', 'cancelled', 'timed_out']);
const fmt = (n: number | null | undefined, decimals = 2) => n == null ? 'Unavailable' : n.toLocaleString('en-GB', { maximumFractionDigits: decimals, minimumFractionDigits: decimals });
const identity = (value: unknown): string => {
  const normal = (x: unknown): unknown => Array.isArray(x) ? x.map(normal) : x && typeof x === 'object' ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => [k, normal(v)])) : x;
  return JSON.stringify(normal(value));
};


function SelectedProfile({ manifest }: { manifest: AssessmentManifest }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = ref.current!;
    const p = manifest.profile;
    void Plotly.react(node, [
      { name: 'Selected bore', x: p.bore_temperature_C, y: p.md_m, mode: 'lines', type: 'scatter', line: { color: '#188e87', width: 2.5 } },
      { name: 'Selected annulus', x: p.annulus_temperature_C, y: p.md_m, mode: 'lines', type: 'scatter', line: { color: '#e78d57', width: 2.5 } },
      { name: 'Formation', x: p.formation_temperature_C, y: p.md_m, mode: 'lines', type: 'scatter', line: { color: '#9ba7ab', dash: 'dash', width: 1.5 } },
    ], { font: { family: 'Segoe UI, sans-serif', size: 11, color: '#647b70' }, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', margin: { t: 30, l: 70, b: 55, r: 30 }, xaxis: { title: { text: 'Temperature (°C)' }, gridcolor: '#edf0eb' }, yaxis: { title: { text: 'Measured depth (m)' }, autorange: 'reversed', gridcolor: '#edf0eb' }, legend: { orientation: 'h', x: 0, y: 1.12 } }, { displayModeBar: false, responsive: true });
    const observer = new ResizeObserver(() => Plotly.Plots.resize(node)); observer.observe(node);
    return () => { observer.disconnect(); Plotly.purge(node); };
  }, [manifest]);
  return <div className="assessment-plot" ref={ref} aria-label="Evaluator-selected circulation temperature profile" />;
}

export function Assessment() {
  const [scenario, setScenario] = useState<AssessmentScenario | null>(null);
  const scenarioRef = useRef<AssessmentScenario | null>(null);
  const [manifest, setManifest] = useState<AssessmentManifest | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const [error, setError] = useState('');
  const [validation, setValidation] = useState('');
  const [warnings, setWarnings] = useState<Warning[]>([]);
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [stage, setStage] = useState('');
  const [notice, setNotice] = useState('');
  const [showSource, setShowSource] = useState(false);
  const [showWarnings, setShowWarnings] = useState(false);
  const [view, setView] = useState('overview');
  const frozen = useRef('');
  const active = useRef('');
  const upload = useRef<HTMLInputElement>(null);
  const draft = (next: AssessmentScenario) => { scenarioRef.current = next; setScenario(next); };
  const refresh = () => api<{ items: Job[] }>('/runs?limit=100').then(r => setHistory(r.items.filter(x => x.mode === 'site_assessment')));
  const busy = submitting || !!job && !finished.has(job.state);
  const stale = !!manifest && identity(manifest.resolved_scenario) !== identity(scenario);
  useEffect(() => {
    void (async () => {
      try {
        const saved = localStorage.getItem('borethm-assessment-draft-v1');
        const initial=saved?JSON.parse(saved):await api<AssessmentScenario>('/presets/newberry-assessment');
        const checked=await api<{resolved_scenario:AssessmentScenario}>('/scenarios/validate',{method:'POST',body:JSON.stringify(initial)});
        draft(checked.resolved_scenario);
        const imported = sessionStorage.getItem('borethm-assessment-import-v1');
        if (imported) {
          setManifest({ ...JSON.parse(imported), imported: true });
          sessionStorage.removeItem('borethm-assessment-import-v1');
          setNotice('Opened a verified assessment bundle. Original engine, programme and status retained.');
          await refresh();
          return;
        }
        const stored = localStorage.getItem('borethm-assessment-active-v1');
        if (stored) {
          const remembered = JSON.parse(stored);
          active.current = remembered.id; frozen.current = remembered.snapshot;
          const record = await api<Job>('/runs/' + remembered.id); setJob(record);
          if (record.state === 'completed' && record.manifest) setManifest(record.manifest);
        }
        await refresh();
      } catch (e) { setError((e as Error).message); }
    })();
  }, []);
  useEffect(() => {
    if (!scenario) return;
    localStorage.setItem('borethm-assessment-draft-v1', JSON.stringify(scenario));
    setChecking(true);
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api<{ warnings: Warning[] }>('/scenarios/validate', { method: 'POST', body: JSON.stringify(scenario), signal: controller.signal }).then(r => { setWarnings(r.warnings); setValidation(''); setChecking(false); }).catch(e => { if (e.name !== 'AbortError') { setValidation(e.message); setChecking(false); } });
    }, 350);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [scenario]);
  useEffect(() => {
    if (!job || finished.has(job.state)) return;
    const rid = job.id;
    const source = new EventSource(`/api/v1/runs/${rid}/events`);
    let sequence = 0;
    const update = async () => {
      try {
        const next = await api<Job>('/runs/' + rid);
        if (active.current !== rid) return;
        setJob(next);
        if (finished.has(next.state)) {
          source.close();
          if (next.state === 'completed' && next.manifest) {
            if (identity(scenarioRef.current) === frozen.current) { setManifest(next.manifest); setNotice(''); }
            else setNotice('Assessment finished for an earlier draft. Open it from assessment history.');
          } else setError(`${next.manifest?.failure?.code}: ${next.manifest?.failure?.message}`);
          await refresh();
        }
      } catch { setNotice('Connection interrupted; retaining the last known state and reconnecting.'); }
    };
    const listener = (e: MessageEvent) => {
      const event = JSON.parse(e.data) as components['schemas']['JobEvent'];
      if (active.current !== rid || event.run_id !== rid || event.scenario_id !== job.scenario_id || event.scenario_revision !== job.scenario_revision) return;
      if (event.type !== 'reset' && event.sequence <= sequence) return;
      sequence = event.sequence;
      setStage(event.type === 'reset' ? 'Restoring job state' : event.name || event.type);
      void update();
    };
    ['stage_started', 'completed', 'failed', 'cancelled', 'timed_out', 'reset'].forEach(name => source.addEventListener(name, listener as EventListener));
    source.onerror = () => void update();
    const timer = setInterval(update, 2000); void update();
    return () => { source.close(); clearInterval(timer); };
  }, [job?.id, job ? finished.has(job.state) : true]);

  const run = async () => {
    if (!scenario || busy || checking || validation) return;
    setSubmitting(true); setError(''); setNotice(''); setStage('Submitting complete assessment');
    try {
      frozen.current = identity(scenario);
      const next = await api<Job>('/runs', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ scenario }) });
      active.current = next.id; setJob(next);
      localStorage.setItem('borethm-assessment-active-v1', JSON.stringify({ id: next.id, snapshot: frozen.current }));
    } catch (e) { setError((e as Error).message); }
    finally { setSubmitting(false); }
  };
  const editSource = (content: string) => { if (scenario) draft({ ...scenario, definition: { ...scenario.definition, site: { ...scenario.definition.site, content } } }); };
  const importSource = async (file: File) => {
    try {
      if (file.size > 5*1024*1024) throw new Error('Site upload exceeds 5 MiB');
      const content = await file.text();
      if (!scenario) return;
      const next = { ...scenario, title: file.name.replace(/\.toml$/i, '') + ' - complete assessment', definition: { ...scenario.definition, site: { kind: 'toml' as const, content, source: file.name } } };
      await api('/scenarios/validate', { method: 'POST', body: JSON.stringify(next) }); draft(next); setError(''); setShowSource(true);
    } catch (e) { setError((e as Error).message); }
  };
  if (!scenario) return <div className="assessment-loading"><FlaskConical size={28} /><p>{error || 'Loading the full assessment workspace…'}</p></div>;
  const report = manifest?.assessment;
  const programme = report?.programme;
  const status = report?.status.label;
  const badgeClass = status === 'GO' ? 'go' : status === 'NO-GO' ? 'no-go' : 'conditional';
  const bounds = (w: Window) => <>{fmt(w.lo, 1)} – {fmt(w.hi, 1)} <span>kg/m³ at 20 °C</span></>;
  const boundSource = (bound: Window['lo_by']) => `${bound.state} · ${bound.case}${bound.md == null ? '' : ` · ${fmt(bound.md, 0)} m MD`}`;

  return <div className="assessment-workspace">
    <aside className="controls assessment-controls"><div className="control-heading"><span><ShieldCheck size={16} />Full site assessment</span></div><div className="assessment-inputs"><PhysicsChoice model={scenario.model} message={scenario.metadata?.schema_loading} change={physics=>draft({...scenario,model:{...scenario.model,physics},metadata:{...scenario.metadata,schema_loading:null}})}/><div className="mode-box"><span className="eyebrow">PROGRAMME ASSESSMENT</span><div><FlaskConical size={16} /><strong>Complete existing evaluator</strong></div><p>Circulation, connections, trips, whole-hole limits and equipment.</p></div><label className="field"><span className="field-label">Frozen site source</span><div className="frozen-site-source">{scenario.definition.site.source}</div></label><div className="inline-note"><span>The evaluator selects pipe, flow and fluid. Operating-state slider values are not imposed on this calculation.</span></div><label className="field"><span className="field-label">Coupling policy</span><select value={scenario.definition.options.coupling} onChange={e => draft({ ...scenario, definition: { ...scenario.definition, options: { ...scenario.definition.options, coupling: e.target.value as 'evaluator_default' | 'coupled' | 'uncoupled' } } })}><option value="evaluator_default">Evaluator default</option><option value="coupled">Explicitly coupled</option><option value="uncoupled">Explicitly uncoupled</option></select></label><div className="context-row"><span>Package</span><strong>BoreTHM 1.8.0</strong></div><div className="context-row"><span>Calculation mode</span><strong>v1.3.4</strong></div><p className="small muted">The calculation-mode identifier is independent of the package version. The original evaluator policy is retained.</p><button className="json-button" onClick={() => setShowSource(!showSource)}><FileJson size={15} />{showSource ? 'Hide' : 'Edit'} site TOML<ChevronDown size={14} /></button><input ref={upload} type="file" accept=".toml" hidden onChange={e => { if (e.target.files?.[0]) void importSource(e.target.files[0]); e.target.value=''; }} /><button className="secondary-button" onClick={() => upload.current?.click()}><Upload size={14} />Import strict site TOML</button>{warnings.length > 0 && <div className="input-qualifications"><span>{warnings.length} site-input qualifications</span>{warnings.map((w,i) => <p key={i}>{w.message}</p>)}</div>}<div className="assessment-resource-note">One local worker · 30-minute assessment deadline. Progress is indeterminate during the core evaluation. Cancellation terminates this job's process.</div></div><div className="control-footer"><button className="run-button" disabled={!busy && (checking || !!validation)} onClick={() => busy && job ? void api(`/runs/${job.id}/cancel`, { method:'POST' }).then(() => setStage('Cancellation requested')) : void run()}>{busy ? <Square size={14} /> : <Play size={14} fill="currentColor" />}{busy ? 'Cancel assessment' : 'Run full assessment'}{busy && <LoaderCircle size={15} className="spin" />}</button><div className="control-footer-meta"><span>{checking ? 'Validating site…' : validation ? 'Check site inputs' : 'Site inputs validated'}</span><span>Explicit run</span></div></div></aside>
    <main className="main assessment-main"><div className="breadcrumb">Workspace <span>/</span> Programme assessment</div><div className="page-heading"><div><div className="eyebrow"><span className="tiny-line" />COMPLETE SITE EVALUATION</div><h1>{report?.site.name || scenario.title}</h1><div className="page-subtitle"><span className="tag">FULL ASSESSMENT</span><span>Original core programme selection</span><span>·</span><span>Surface densities at 20 °C</span></div></div>{manifest && !manifest.imported && <a className="secondary-button" href={`/api/v1/runs/${manifest.run_id}/export`}><ArrowDownToLine size={14} />Export full assessment</a>}</div>
      {(validation || error) && <div className="error" role="alert"><TriangleAlert size={17} /><pre>{validation || error}</pre><button aria-label="Dismiss assessment error" onClick={() => setError('')}><X size={14} /></button></div>}
      {notice && <div className="notice">{notice}</div>}
      <PhysicsRecord physics={manifest?.physics} settings={manifest?.physics_settings}/><div className="result-status"><div><span className={`status-light ${busy ? 'working' : manifest ? 'solved' : ''}`} /><strong>{busy ? stage || job?.state : stale ? 'Assessment out of date; inputs changed' : manifest ? 'Complete assessment calculated' : 'Ready for a full site assessment'}</strong>{manifest && <span>{fmt(manifest.wall_clock_duration_s)} s · frozen revision</span>}</div>{manifest && <button className="qualification-badge" onClick={() => setShowWarnings(!showWarnings)}><TriangleAlert size={12} />Qualified · {manifest.warnings.length}<ChevronDown size={12} /></button>}</div>
      {showWarnings && manifest && <div className="qualification-panel"><h3>Assessment qualifications</h3>{manifest.warnings.map((w, i) => <div key={i}><span>{w.code}</span><p>{w.message}</p></div>)}</div>}
      {showSource && <section className="assessment-source"><div className="section-heading"><h2>Frozen data-only site definition</h2><span>Strict TOML · unknown keys rejected</span></div><textarea aria-label="Assessment site TOML" spellCheck={false} value={scenario.definition.site.content} onChange={e => editSource(e.target.value)} /><p>Edits create a new draft. Previous assessment status becomes out of date; rerun deliberately.</p></section>}
      <div className={`assessment-metrics ${stale ? 'stale' : ''}`}><div className="metric"><div className="metric-label">Programme status<ShieldCheck size={15} /></div><div className={`assessment-status ${stale ? 'outdated' : badgeClass}`}>{stale ? 'Out of date' : status || 'Not evaluated'}</div><div className="metric-foot">{stale ? 'Previous status retained in run history' : 'Original core status and reasons'}</div></div><div className="metric"><div className="metric-label">Numerical validity</div><div className="assessment-status neutral">{manifest ? manifest.validity.solver_converged ? 'Converged' : 'Unsuccessful' : 'Awaiting run'}</div><div className="metric-foot">Separate from programme status</div></div><div className="metric"><div className="metric-label">Chosen mass flow</div><div className="metric-value">{fmt(programme?.mass_flow_kg_s, 1)} <span>kg/s</span></div><div className="metric-foot">Selected by evaluator</div></div><div className="metric"><div className="metric-label">Standpipe pressure</div><div className="metric-value">{fmt(programme ? programme.spp_Pa/1e6 : undefined, 3)} <span>MPa</span></div><div className="metric-foot">Pressure difference · hydraulic_budget_v160</div></div></div>
      {report && manifest ? <><section className="assessment-status-panel"><div className="section-heading"><h2>Controlling reasons</h2><span>{stale ? 'Previous assessment' : report.status.label}</span></div><div className="assessment-reasons">{report.status.reasons.length ? report.status.reasons.map(reason => <p key={reason}><TriangleAlert size={14} />{reason}</p>) : <p><Check size={14} />The core status lists no additional constraint.</p>}<p className="small muted">{report.reading.controlling_constraint}</p></div></section><div className="assessment-tabs tabs" role="tablist">{[['overview','Assessment'],['windows','Whole-hole limits'],['cases','Target cases'],['profile','Selected circulation'],['equipment','Equipment & inputs']].map(([key,label]) => <button key={key} role="tab" aria-selected={view === key} onClick={() => setView(key)}>{label}</button>)}</div>
      {view === 'overview' && <div className="assessment-overview"><section className="assessment-programme"><div className="section-heading"><h2>Evaluator-selected programme</h2><span>Actual result</span></div><dl><dt>Pipe</dt><dd>{programme?.pipe}</dd><dt>Selected flow</dt><dd>{programme?.flow} {programme?.flow_unit}</dd><dt>Fluid</dt><dd>{programme?.fluid ? `Barite mud · ${fmt(programme.fluid.rho_surface, 1)} kg/m³ at surface` : 'Water · IAPWS-95'}</dd><dt>Backpressure</dt><dd>{fmt((programme?.backpressure_Pa || 0)/1e6,3)} MPa gauge</dd><dt>Bit bore delivery</dt><dd>{fmt(manifest.profile.bore_temperature_C.at(-1))} °C</dd><dt>Effective coupling</dt><dd>{manifest.numerical_profile.effective_coupled ? 'Coupled' : 'Uncoupled'}</dd></dl><details><summary>Effective hydraulic configuration</summary><pre>{JSON.stringify(programme?.hydraulic_configuration,null,2)}</pre></details></section><section className="assessment-reading"><div className="section-heading"><h2>Interpretation of the existing checks</h2></div>{Object.entries(report.reading).filter(([key]) => key !== 'controlling_constraint').map(([key,text]) => <div key={key}><h3>{key.replaceAll('_',' ')}</h3><p>{text}</p></div>)}</section></div>}
      {view === 'windows' && <section className="assessment-details"><h2>Common fluid windows at every evaluated depth, state and case</h2><p>Surface density at 20 °C. These ranges differ from target-depth SG-equivalents. Closed windows remain closed; bounds are not extrapolated through unevaluated depths.</p><table><thead><tr><th>Programme fluid</th><th>Window</th><th>Required surface-density range</th></tr></thead><tbody>{(['drilling_fluid','trip_fluid'] as const).map(key => <tr key={key}><td>{key.replace('_',' ')}</td><td>{report.windows[key].open ? 'Open' : 'Closed'}</td><td>{bounds(report.windows[key])}</td></tr>)}</tbody></table>{(['drilling_fluid','trip_fluid'] as const).map(key => <div className="window-evidence" key={key}><h3>{key.replace('_',' ')}</h3><p>Lower bound: {boundSource(report.windows[key].lo_by)}</p><p>Upper bound: {boundSource(report.windows[key].hi_by)}</p>{report.windows[key].note && <p>{report.windows[key].note}</p>}</div>)}<details><summary>Full evaluated whole-hole constraints and refinement evidence</summary><pre>{JSON.stringify(report.windows,null,2)}</pre></details></section>}
      {view === 'cases' && <section className="assessment-details"><h2>Stress and strength cases at the target</h2><p>Deciding case: {report.cycle.deciding_case}. The vertical-hole formulation is not a general directional-well stability prediction.</p><table><thead><tr><th>Case</th><th>Overall</th><th>Drilling / connection / trip evidence</th></tr></thead><tbody>{report.cycle.cases.map((item,index) => <tr key={index}><td>{String(item.label)}</td><td>{String(item.verdict)}</td><td><pre>{JSON.stringify(item.states,null,2)}</pre></td></tr>)}</tbody></table><details><summary>Target case values, pressures and SG-equivalents</summary><pre>{JSON.stringify(report.cycle,null,2)}</pre></details></section>}
      {view === 'profile' && <section className="assessment-details"><h2>Circulation selected by the programme evaluator</h2><p>These temperatures belong to this completed assessment's selected programme. They do not inherit the editable circulation scenario's inputs.</p><SelectedProfile manifest={manifest} /><details><summary>Numeric table alternative</summary><table><thead><tr><th>MD (m)</th><th>Bore (°C)</th><th>Annulus (°C)</th><th>Formation (°C)</th></tr></thead><tbody>{manifest.profile.md_m.map((md,index) => <tr key={index}><td>{fmt(md,1)}</td><td>{fmt(manifest.profile.bore_temperature_C[index],3)}</td><td>{fmt(manifest.profile.annulus_temperature_C[index],3)}</td><td>{fmt(manifest.profile.formation_temperature_C[index],3)}</td></tr>)}</tbody></table></details></section>}
      {view === 'equipment' && <section className="assessment-details"><h2>Equipment and evidence checks</h2><p>{report.reading.equipment}</p>{Object.entries(report.components).map(([key, value]) => <details key={key}><summary>{key.replaceAll('_',' ')}</summary><pre>{JSON.stringify(value,null,2)}</pre></details>)}<details><summary>Full tool and coating checks</summary><pre>{JSON.stringify(report.tools,null,2)}</pre></details><details><summary>Frozen input site definition and provenance</summary><pre>{manifest.resolved_scenario.definition.site.content}</pre></details></section>}
      </> : <section className="assessment-empty"><ShieldCheck size={34} /><h2>A full programme, with its evidence.</h2><p>Run the complete evaluator to inspect its chosen programme, whole-hole constraints, target cases, equipment checks and controlling reasons. Assessments can take tens of seconds or longer.</p></section>}
      <section className="assessment-history history-panel"><div className="section-heading"><h2>Assessment history</h2><span>{history.length} runs</span></div>{history.length ? history.slice(0,6).map(record => <div className="run-row" key={record.id}><button className="run-select" onClick={async () => { const selected = await api<Job>('/runs/' + record.id); active.current = selected.id; setJob(selected); if (selected.state === 'completed' && selected.manifest) { setManifest(selected.manifest); draft(selected.manifest.resolved_scenario); setError(''); } else setError(selected.manifest?.failure?.message || `Assessment is ${selected.state}`); }}><span className="run-symbol">{record.state === 'completed' ? <Check size={12} /> : <TriangleAlert size={12} />}</span><span><strong>{record.manifest?.title || 'Full site assessment'}</strong><small>{record.id.slice(-8)} · {new Date(record.created_at).toLocaleTimeString('en-GB')}</small></span></button><span className="run-result">{record.manifest?.validity?.assessment_status || record.state}</span></div>) : <p className="empty-history">Each assessment retains its exact site inputs, effective programme and model identity.</p>}</section><footer className="page-footer"><span>Qualified model results · original status preserved</span><span>Results have not been compared with field measurements</span></footer>
    </main>
  </div>;
}
