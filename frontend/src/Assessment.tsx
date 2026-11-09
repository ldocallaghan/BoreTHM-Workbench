import {api} from './apiClient';
import {PhysicsChoice, PhysicsRecord} from './PhysicsChoice';
import React, { useEffect, useRef, useState } from 'react';
import Plotly from 'plotly.js-dist-min';
import { ArrowDownToLine, Check, ChevronDown, FlaskConical, LoaderCircle, Play, ShieldCheck, Square, TriangleAlert, X } from 'lucide-react';
import type { components } from './api.generated';
import './assessment.css';
import './results.css';
import './programme.css';
import {SiteEditor, type SiteCheck, type SiteDocument} from './SiteEditor';
import {ResultsPage, type DetailSection, type ResultsPayload} from './ResultsPage';
import {ProgrammePage} from './ProgrammePage';
import {LeversPage} from './LeversPage';
import {useFormat} from './UnitsContext';
import {series, number as numberUnit} from './units';

type AssessmentScenario = components['schemas']['AssessmentScenario'];
type Warning = { code: string; message: string; path: string };
type Programme = { pipe: string; flow: number; flow_unit: string; mass_flow_kg_s: number; bit_C: number; return_C: number; spp_Pa: number; backpressure_Pa: number; fluid: null | { rho_surface: number; sg: number }; hydraulic_configuration: unknown; drilling_prescribed_rho_surface: number; trip_prescribed_rho_surface: number };
type Window = { open: boolean; lo: number; hi: number; fluid: number | null; note?: string; lo_by: { state: string; case: string; md: number | null }; hi_by: { state: string; case: string; md: number | null } };
type Report = { status: { label: string; reasons: string[] }; programme: Programme; site: { name: string; target_depth_m: number; target_T_C: number; tier: string }; evaluation: string; reading: { calculation: string; scenarios_at_target: string; whole_programme: string; equipment: string; controlling_constraint: string }; components: Record<string, unknown>; windows: { drilling_fluid: Window; trip_fluid: Window; refinement?: { converged: boolean } }; cycle: { cases: Record<string, unknown>[]; site_verdict: string; deciding_case: string; verdicts: Record<string, string> }; tools: unknown };
type AssessmentManifest = { physics?:string; physics_settings?:unknown; mode: 'site_assessment'; run_id: string; title: string; acceptance: string; resolved_scenario: AssessmentScenario; assessment: Report; numerical_profile: { effective_coupled: boolean }; warnings: Warning[]; validity: { solver_converged: boolean; energy_status: string; domain_status: string; assessment_status: string }; profile: { md_m: number[]; bore_temperature_C: number[]; annulus_temperature_C: number[]; formation_temperature_C: number[] }; wall_clock_duration_s: number; failure?: { code: string; message: string }; diagnostics?: { assessment?: Report }; imported?: boolean };
type Job = { id: string; mode?: string; state: string; scenario_id: string; scenario_revision: number; created_at: string; manifest: AssessmentManifest | null };
type View = 'site' | 'programme' | 'results' | 'details' | 'levers';
type Interface = {available: boolean; message: string | null};
const finished = new Set(['completed', 'failed', 'cancelled', 'timed_out']);
const fmt = (n: number | null | undefined, decimals = 2) => n == null ? 'Unavailable' : n.toLocaleString('en-GB', { maximumFractionDigits: decimals, minimumFractionDigits: decimals });
const identity = (value: unknown): string => {
  const normal = (x: unknown): unknown => Array.isArray(x) ? x.map(normal) : x && typeof x === 'object' ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => [k, normal(v)])) : x;
  return JSON.stringify(normal(value));
};


function SelectedProfile({ manifest }: { manifest: AssessmentManifest }) {
  const ref = useRef<HTMLDivElement>(null);
  const {system, symbol} = useFormat();
  useEffect(() => {
    const node = ref.current!;
    const p = manifest.profile;
    const md = series(p.md_m, 'length', system);
    void Plotly.react(node, [
      { name: 'Bore (down the pipe)', x: series(p.bore_temperature_C, 'temperature', system), y: md, mode: 'lines', type: 'scatter', line: { color: '#188e87', width: 2.5 } },
      { name: 'Annulus (returning)', x: series(p.annulus_temperature_C, 'temperature', system), y: md, mode: 'lines', type: 'scatter', line: { color: '#e78d57', width: 2.5 } },
      { name: 'Formation', x: series(p.formation_temperature_C, 'temperature', system), y: md, mode: 'lines', type: 'scatter', line: { color: '#9ba7ab', dash: 'dash', width: 1.5 } },
    ], { font: { family: 'Segoe UI, sans-serif', size: 11, color: '#647b70' }, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', margin: { t: 30, l: 70, b: 55, r: 30 }, xaxis: { title: { text: `Temperature (${symbol('temperature')})` }, gridcolor: '#edf0eb' }, yaxis: { title: { text: `Measured depth (${symbol('length')})` }, autorange: 'reversed', gridcolor: '#edf0eb' }, legend: { orientation: 'h', x: 0, y: 1.12 } }, { displayModeBar: false, responsive: true });
    const observer = new ResizeObserver(() => Plotly.Plots.resize(node)); observer.observe(node);
    return () => { observer.disconnect(); Plotly.purge(node); };
  }, [manifest, system]);
  return <div className="assessment-plot" ref={ref} aria-label="Circulating temperature profile of the chosen programme" />;
}

export function Assessment() {
  const {system, format, symbol} = useFormat();
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
  const [showWarnings, setShowWarnings] = useState(false);
  const [view, setView] = useState('overview');
  const [page, setPage] = useState<View>('site');
  const [site, setSite] = useState<SiteDocument | null>(null);
  const [siteCheck, setSiteCheck] = useState<SiteCheck | null>(null);
  const [siteEdited, setSiteEdited] = useState(false);
  const [results, setResults] = useState<ResultsPayload | null>(null);
  const [resultsError, setResultsError] = useState('');
  const [programmeInterface, setProgrammeInterface] = useState<Interface | null>(null);
  const [coreVersion, setCoreVersion] = useState('');
  const frozen = useRef('');
  const active = useRef('');
  const draft = (next: AssessmentScenario) => { scenarioRef.current = next; setScenario(next); };
  const refresh = () => api<{ items: Job[] }>('/runs?limit=100').then(r => setHistory(r.items.filter(x => x.mode === 'site_assessment')));
  const busy = submitting || !!job && !finished.has(job.state);
  const stale = !!manifest && identity(manifest.resolved_scenario) !== identity(scenario);
  const siteReady = !siteEdited || !!siteCheck?.valid;
  const loadSite = async (content: string) => {
    try { const value = await api<{document: SiteDocument}>('/sites/import', {method: 'POST', body: JSON.stringify({toml: content})}); setSite(value.document); setSiteEdited(false); }
    catch (e) { setError((e as Error).message); }
  };
  useEffect(() => {
    void api<{programme_interface: Interface; engine: {core_version?: string}}>('/capabilities').then(c => { setProgrammeInterface(c.programme_interface); setCoreVersion(c.engine.core_version || ''); }).catch(() => setProgrammeInterface({available: false, message: 'The service did not report its capabilities.'}));
  }, []);
  useEffect(() => {
    void (async () => {
      try {
        const saved = localStorage.getItem('borethm-assessment-draft-v1');
        const initial=saved?JSON.parse(saved):await api<AssessmentScenario>('/presets/newberry-assessment');
        const checked=await api<{resolved_scenario:AssessmentScenario}>('/scenarios/validate',{method:'POST',body:JSON.stringify(initial)});
        draft(checked.resolved_scenario);
        await loadSite(checked.resolved_scenario.definition.site.content);
        const imported = sessionStorage.getItem('borethm-assessment-import-v1');
        if (imported) {
          setManifest({ ...JSON.parse(imported), imported: true });
          sessionStorage.removeItem('borethm-assessment-import-v1');
          setNotice('Opened a verified assessment archive. Its model version, programme and verdict are unchanged.');
          setPage('results');
          await refresh();
          return;
        }
        const stored = localStorage.getItem('borethm-assessment-active-v1');
        if (stored) {
          const remembered = JSON.parse(stored);
          active.current = remembered.id; frozen.current = remembered.snapshot;
          const record = await api<Job>('/runs/' + remembered.id); setJob(record);
          if (record.state === 'completed' && record.manifest) { setManifest(record.manifest); setPage('results'); }
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
    setResults(null); setResultsError('');
    if (!manifest) return;
    const controller = new AbortController();
    const request = manifest.imported
      ? api<ResultsPayload>('/results?units=' + system, {method: 'POST', body: JSON.stringify({manifest}), signal: controller.signal})
      : api<ResultsPayload>(`/runs/${manifest.run_id}/results?units=${system}`, {signal: controller.signal});
    request.then(setResults).catch(e => { if ((e as Error).name !== 'AbortError') setResultsError((e as Error).message); });
    return () => controller.abort();
  }, [manifest, system]);
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
            if (identity(scenarioRef.current) === frozen.current) { setManifest(next.manifest); setNotice(''); setPage('results'); }
            else setNotice('The assessment finished for earlier inputs. Open it from the assessment history.');
          } else setError(`${next.manifest?.failure?.code}: ${next.manifest?.failure?.message}`);
          await refresh();
        }
      } catch { setNotice('Connection lost. Showing the last known state while reconnecting.'); }
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
    if (!scenario || busy || checking || validation || !siteReady) return;
    setSubmitting(true); setError(''); setNotice(''); setStage('Starting the assessment');
    try {
      frozen.current = identity(scenario);
      const next = await api<Job>('/runs', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ scenario }) });
      active.current = next.id; setJob(next);
      localStorage.setItem('borethm-assessment-active-v1', JSON.stringify({ id: next.id, snapshot: frozen.current }));
    } catch (e) { setError((e as Error).message); }
    finally { setSubmitting(false); }
  };
  // The site form writes the model's TOML into the scenario once the model
  // can load it. Until the user edits the form, the original text is kept.
  const onSiteCheck = (value: SiteCheck | null) => {
    setSiteCheck(value);
    if (!siteEdited || !value?.toml || !scenarioRef.current) return;
    const current = scenarioRef.current;
    if (current.definition.site.content === value.toml) return;
    const name = (site?.name as string) || 'Site';
    draft({ ...current, title: `${name} - site assessment`, definition: { ...current.definition, site: { kind: 'toml', content: value.toml, source: 'Site form' } } });
  };
  const editSite = (next: SiteDocument) => { setSite(next); setSiteEdited(true); };
  const editSource = (content: string) => { if (scenario) { draft({ ...scenario, definition: { ...scenario.definition, site: { ...scenario.definition.site, content } } }); void loadSite(content); } };
  const openDetails = (section: DetailSection) => { setPage('details'); if (section === 'qualifications') setShowWarnings(true); else setView(section); };
  if (!scenario) return <div className="assessment-loading"><FlaskConical size={28} /><p>{error || 'Loading the site assessment…'}</p></div>;
  const report = manifest?.assessment;
  const programme = report?.programme;
  const status = report?.status.label;
  const badgeClass = status === 'GO' ? 'go' : status === 'NO-GO' ? 'no-go' : 'conditional';
  const bounds = (w: Window) => <>{format(w.lo, 'density')} to {format(w.hi, 'density')} <span>at 20 °C</span></>;
  const boundSource = (bound: Window['lo_by']) => `${bound.state} · ${bound.case}${bound.md == null ? '' : ` · ${format(bound.md, 'length')} MD`}`;
  const reportHref = manifest && !manifest.imported ? `/api/v1/runs/${manifest.run_id}/report?units=${system}` : undefined;
  const runDisabledReason = !siteReady ? 'Correct the marked site inputs first.' : validation ? 'Check the site inputs.' : '';

  return <div className="assessment-workspace">
    <aside className="controls assessment-controls"><div className="control-heading"><span><ShieldCheck size={16} />Site assessment</span></div><div className="assessment-inputs"><PhysicsChoice model={scenario.model} message={scenario.metadata?.schema_loading} change={physics=>draft({...scenario,model:{...scenario.model,physics},metadata:{...scenario.metadata,schema_loading:null}})}/><div className="mode-box"><span className="eyebrow">WHAT THE ASSESSMENT CHECKS</span><div><FlaskConical size={16} /><strong>Drilling programme for this site</strong></div><p>{programmeInterface?.available ? 'The model chooses the pipe, flow and fluid unless the Programme page states them, then checks circulation, connections, trips, the mud-weight window and equipment ratings.' : 'The model chooses the pipe, flow and fluid, then checks circulation, connections, trips, the mud-weight window and equipment ratings.'}</p></div><label className="field"><span className="field-label">Site</span><div className="frozen-site-source">{(site?.name as string) || scenario.definition.site.source}</div></label><div className="inline-note"><span>Values on the circulation page are not used here.</span></div><label className="field"><span className="field-label">Thermal stress coupling</span><select value={scenario.definition.options.coupling} onChange={e => draft({ ...scenario, definition: { ...scenario.definition, options: { ...scenario.definition.options, coupling: e.target.value as 'evaluator_default' | 'coupled' | 'uncoupled' } } })}><option value="evaluator_default">Model default</option><option value="coupled">Coupled</option><option value="uncoupled">Uncoupled</option></select></label><div className="context-row"><span>BoreTHM</span><strong>{coreVersion || '1.8'}</strong></div><div className="context-row"><span>Calculation mode</span><strong>v1.3.4</strong></div><p className="small muted">The calculation mode is named separately from the BoreTHM version.</p>{warnings.length > 0 && <div className="input-qualifications"><span>{warnings.length} site input warnings</span>{warnings.map((w,i) => <p key={i}>{w.message}</p>)}</div>}<div className="assessment-resource-note">One assessment runs at a time, with a 30-minute limit. Progress is not reported during the calculation. Cancelling stops it.</div></div><div className="control-footer"><button className="run-button" title={runDisabledReason} disabled={!busy && (checking || !!validation || !siteReady)} onClick={() => busy && job ? void api(`/runs/${job.id}/cancel`, { method:'POST' }).then(() => setStage('Cancellation requested')) : void run()}>{busy ? <Square size={14} /> : <Play size={14} fill="currentColor" />}{busy ? 'Cancel assessment' : 'Run assessment'}{busy && <LoaderCircle size={15} className="spin" />}</button><div className="control-footer-meta"><span>{checking ? 'Checking site…' : runDisabledReason || 'Site inputs accepted'}</span></div></div></aside>
    <main className="main assessment-main"><div className="breadcrumb">Workspace <span>/</span> Site assessment</div><div className="page-heading"><div><div className="eyebrow"><span className="tiny-line" />SITE ASSESSMENT</div><h1>{report?.site.name || (site?.name as string) || scenario.title}</h1><div className="page-subtitle"><span>Programme chosen by the model</span><span>·</span><span>Surface densities at 20 °C</span></div></div><div className="heading-actions">{reportHref && <a className="secondary-button" href={reportHref} download><ArrowDownToLine size={14} />Export report</a>}{manifest && !manifest.imported && <a className="secondary-button" href={`/api/v1/runs/${manifest.run_id}/export`}><ArrowDownToLine size={14} />Export run archive</a>}</div></div>
      {(validation || error) && <div className="error" role="alert"><TriangleAlert size={17} /><pre>{validation || error}</pre><button aria-label="Dismiss assessment error" onClick={() => setError('')}><X size={14} /></button></div>}
      {notice && <div className="notice">{notice}</div>}
      <div className="result-status"><div><span className={`status-light ${busy ? 'working' : manifest ? 'solved' : ''}`} /><strong>{busy ? stage || job?.state : stale ? 'Result is for earlier inputs' : manifest ? 'Assessment complete' : 'No assessment yet'}</strong>{manifest && <span>{fmt(manifest.wall_clock_duration_s)} s</span>}</div>{manifest && <button className="qualification-badge" onClick={() => setShowWarnings(!showWarnings)}><TriangleAlert size={12} />{manifest.warnings.length} qualifications<ChevronDown size={12} /></button>}</div>
      {showWarnings && manifest && <div className="qualification-panel"><h3>Assessment qualifications</h3>{manifest.warnings.map((w, i) => <div key={i}><span>{w.code}</span><p>{w.message}</p></div>)}</div>}
      <div className="page-tabs tabs" role="tablist" aria-label="Assessment pages">{((programmeInterface?.available ? [['site', 'Site'], ['programme', 'Programme'], ['results', 'Results'], ['levers', 'Design levers'], ['details', 'Details']] : [['site', 'Site'], ['results', 'Results'], ['details', 'Details']]) as [View, string][]).map(([key, label]) => <button key={key} role="tab" aria-selected={page === key} disabled={!['site', 'programme'].includes(key) && !manifest} onClick={() => setPage(key)}>{label}</button>)}</div>
      {programmeInterface && !programmeInterface.available && <p className="small muted interface-note">{programmeInterface.message} The Programme and Design levers pages are hidden.</p>}
      <div hidden={page !== 'site'}>{site ? <SiteEditor document={site} change={editSite} onCheck={onSiteCheck} /> : <p>Loading the site…</p>}</div>
      {page === 'programme' && programmeInterface?.available && (site ? <ProgrammePage scenario={scenario} draft={draft} site={site} editSite={editSite} siteCheck={siteCheck} /> : <p>Loading the site…</p>)}
      {page === 'levers' && programmeInterface?.available && manifest && <LeversPage base={manifest} onOpen={async (rid: string) => { const selected = await api<Job>('/runs/' + rid); if (selected.state === 'completed' && selected.manifest) { active.current = selected.id; setJob(selected); setManifest(selected.manifest); draft(selected.manifest.resolved_scenario); void loadSite(selected.manifest.resolved_scenario.definition.site.content); setPage('results'); } }} />}
      {page === 'results' && (manifest ? (results ? <>{stale && <div className="notice">This result is for earlier site inputs. Run the assessment again to update it.</div>}<ResultsPage payload={results} system={system} onDetails={openDetails} reportHref={reportHref} /></> : <p role="status">{resultsError || 'Preparing the results page…'}</p>) : null)}
      {page === 'details' && (report && manifest ? <><PhysicsRecord physics={manifest?.physics} settings={manifest?.physics_settings}/><div className={`assessment-metrics ${stale ? 'stale' : ''}`}><div className="metric"><div className="metric-label">Verdict<ShieldCheck size={15} /></div><div className={`assessment-status ${stale ? 'outdated' : badgeClass}`}>{stale ? 'Out of date' : status || 'Not evaluated'}</div><div className="metric-foot">{stale ? 'Previous verdict kept in the history' : 'Status returned by the model'}</div></div><div className="metric"><div className="metric-label">Calculation</div><div className="assessment-status neutral">{manifest.validity.solver_converged ? 'Converged' : 'Unsuccessful'}</div><div className="metric-foot">Separate from the verdict</div></div><div className="metric"><div className="metric-label">Flow chosen</div><div className="metric-value">{programme ? format(programme.mass_flow_kg_s, 'mass_flow') : 'Unavailable'}</div><div className="metric-foot">Chosen by the model</div></div><div className="metric"><div className="metric-label">Standpipe pressure</div><div className="metric-value">{programme ? format(programme.spp_Pa, 'pressure') : 'Unavailable'}</div><div className="metric-foot">Pump pressure at surface · hydraulic_budget_v160</div></div></div>
      <section className="assessment-status-panel"><div className="section-heading"><h2>Reasons for the verdict</h2><span>{stale ? 'Previous assessment' : report.status.label}</span></div><div className="assessment-reasons">{report.status.reasons.length ? report.status.reasons.map(reason => <p key={reason}><TriangleAlert size={14} />{reason}</p>) : <p><Check size={14} />The model lists no further constraint.</p>}<p className="small muted">{report.reading.controlling_constraint}</p></div></section><div className="assessment-tabs tabs" role="tablist">{[['overview','Summary'],['windows','Mud-weight window'],['cases','Stability cases'],['profile','Circulating temperatures'],['equipment','Equipment and inputs']].map(([key,label]) => <button key={key} role="tab" aria-selected={view === key} onClick={() => setView(key)}>{label}</button>)}</div>
      {view === 'overview' && <div className="assessment-overview"><section className="assessment-programme"><div className="section-heading"><h2>Programme chosen by the model</h2><span>From the result</span></div><dl><dt>Pipe</dt><dd>{programme?.pipe}</dd><dt>Flow</dt><dd>{programme?.flow} {programme?.flow_unit}</dd><dt>Fluid</dt><dd>{programme?.fluid ? `Barite mud · ${format(programme.fluid.rho_surface, 'density')} at surface` : 'Water · IAPWS-95'}</dd><dt>Backpressure</dt><dd>{format(programme?.backpressure_Pa || 0, 'pressure')} gauge</dd><dt>Bit bore delivery</dt><dd>{format(manifest.profile.bore_temperature_C.at(-1), 'temperature')}</dd><dt>Thermal stress coupling used</dt><dd>{manifest.numerical_profile.effective_coupled ? 'Coupled' : 'Uncoupled'}</dd></dl><details><summary>Hydraulic settings used</summary><pre>{JSON.stringify(programme?.hydraulic_configuration,null,2)}</pre></details></section><section className="assessment-reading"><div className="section-heading"><h2>What the checks found</h2></div>{Object.entries(report.reading).filter(([key]) => key !== 'controlling_constraint').map(([key,text]) => <div key={key}><h3>{key.replaceAll('_',' ')}</h3><p>{text}</p></div>)}</section></div>}
      {view === 'windows' && <section className="assessment-details"><h2>Mud-weight window at every evaluated depth, state and case</h2><p>Surface density at 20 °C. These ranges differ from target-depth SG-equivalents. Closed windows remain closed; bounds are not extrapolated through unevaluated depths.</p><table><thead><tr><th>Fluid</th><th>Window</th><th>Surface density range</th></tr></thead><tbody>{(['drilling_fluid','trip_fluid'] as const).map(key => <tr key={key}><td>{key.replace('_',' ')}</td><td>{report.windows[key].open ? 'Open' : 'Closed'}</td><td>{bounds(report.windows[key])}</td></tr>)}</tbody></table>{(['drilling_fluid','trip_fluid'] as const).map(key => <div className="window-evidence" key={key}><h3>{key.replace('_',' ')}</h3><p>Lower bound: {boundSource(report.windows[key].lo_by)}</p><p>Upper bound: {boundSource(report.windows[key].hi_by)}</p>{report.windows[key].note && <p>{report.windows[key].note}</p>}</div>)}<details><summary>Full evaluated whole-hole constraints and refinement evidence</summary><pre>{JSON.stringify(report.windows,null,2)}</pre></details></section>}
      {view === 'cases' && <section className="assessment-details"><h2>Stress and strength cases at the target</h2><p>Deciding case: {report.cycle.deciding_case}. The vertical-hole formulation is not a general directional-well stability prediction.</p><table><thead><tr><th>Case</th><th>Overall</th><th>Drilling / connection / trip evidence</th></tr></thead><tbody>{report.cycle.cases.map((item,index) => <tr key={index}><td>{String(item.label)}</td><td>{String(item.verdict)}</td><td><pre>{JSON.stringify(item.states,null,2)}</pre></td></tr>)}</tbody></table><details><summary>Target case values, pressures and SG-equivalents</summary><pre>{JSON.stringify(report.cycle,null,2)}</pre></details></section>}
      {view === 'profile' && <section className="assessment-details"><h2>Circulating temperatures of the chosen programme</h2><p>These temperatures belong to the programme the model chose for this assessment. They do not use the inputs on the circulation page.</p><SelectedProfile manifest={manifest} /><details><summary>Values as a table</summary><table><thead><tr><th>MD ({symbol('length')})</th><th>Bore ({symbol('temperature')})</th><th>Annulus ({symbol('temperature')})</th><th>Formation ({symbol('temperature')})</th></tr></thead><tbody>{manifest.profile.md_m.map((md,index) => <tr key={index}><td>{numberUnit(md,'length',system,1)}</td><td>{numberUnit(manifest.profile.bore_temperature_C[index],'temperature',system,3)}</td><td>{numberUnit(manifest.profile.annulus_temperature_C[index],'temperature',system,3)}</td><td>{numberUnit(manifest.profile.formation_temperature_C[index],'temperature',system,3)}</td></tr>)}</tbody></table></details></section>}
      {view === 'equipment' && <section className="assessment-details"><h2>Equipment checks</h2><p>{report.reading.equipment}</p>{Object.entries(report.components).map(([key, value]) => <details key={key}><summary>{key.replaceAll('_',' ')}</summary><pre>{JSON.stringify(value,null,2)}</pre></details>)}<details><summary>Full tool and coating checks</summary><pre>{JSON.stringify(report.tools,null,2)}</pre></details><details><summary>Site file used for this run</summary><pre>{manifest.resolved_scenario.definition.site.content}</pre></details></section>}
      <section className="assessment-source"><div className="section-heading"><h2>Site file as text</h2><span>TOML; the model rejects unknown keys</span></div><textarea aria-label="Assessment site TOML" spellCheck={false} value={scenario.definition.site.content} onChange={e => editSource(e.target.value)} /><p>Changes here replace the site form. Run the assessment again to update the result.</p></section>
      </> : null)}
      {!manifest && (page === 'results' || page === 'details') && <section className="assessment-empty"><ShieldCheck size={34} /><h2>No assessment yet</h2><p>Run the assessment to see the verdict, the mud-weight window, tool temperatures against their ratings and stability at the target. An assessment takes from tens of seconds to several minutes.</p></section>}
      <section className="assessment-history history-panel"><div className="section-heading"><h2>Assessment history</h2><span>{history.length} runs</span></div>{history.length ? history.slice(0,6).map(record => <div className="run-row" key={record.id}><button className="run-select" onClick={async () => { const selected = await api<Job>('/runs/' + record.id); active.current = selected.id; setJob(selected); if (selected.state === 'completed' && selected.manifest) { setManifest(selected.manifest); draft(selected.manifest.resolved_scenario); void loadSite(selected.manifest.resolved_scenario.definition.site.content); setPage('results'); setError(''); } else setError(selected.manifest?.failure?.message || `Assessment is ${selected.state}`); }}><span className="run-symbol">{record.state === 'completed' ? <Check size={12} /> : <TriangleAlert size={12} />}</span><span><strong>{record.manifest?.title || 'Site assessment'}</strong><small>{record.id.slice(-8)} · {new Date(record.created_at).toLocaleTimeString('en-GB')}</small></span></button><span className="run-result">{record.manifest?.validity?.assessment_status || record.state}</span></div>) : <p className="empty-history">Each assessment keeps its site inputs, the programme chosen and the model version.</p>}</section><footer className="page-footer"><span>Model results with their qualifications</span><span>Field comparisons show where the model has been tested</span></footer>
    </main>
  </div>;
}
