import {RunBrowser} from './RunBrowser';
import {api} from './apiClient';
import {formatApiError} from './apiErrors';
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState, lazy, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import Plotly from 'plotly.js-dist-min';
import type { Data, Layout, PlotlyHTMLElement } from 'plotly.js';
import { ArrowDown, ArrowDownToLine, ArrowUp, ArrowUpRight, Check, ChevronDown, CircleHelp, Clock3, Code2, Columns3, Database, FileJson, FlaskConical, Layers3, LoaderCircle, Play, Plus, RotateCcw, Settings2, ShieldCheck, Square, Thermometer, TriangleAlert, Upload, Waves, X, ZoomIn } from 'lucide-react';
import type { components } from './api.generated';
import './style.css';
import { Assessment } from './Assessment';
import {History, type HistoryImport} from './History';
import {Sweeps} from './Sweeps';
import {WellView as Well} from './WellView';
const ScenarioScene=lazy(()=>import('./Scene').then(m=>({default:m.ScenarioScene})));
import type {Trajectory} from './sceneState';
import {SurveyEditor} from './SurveyEditor';
import {adjacentComponents, completionMatches, fingerprint, inputChanges, sample} from './state';

type Scenario = components['schemas']['Scenario'];
type JobEvent = components['schemas']['JobEvent'];
type Warning = { code: string; message: string; path: string };
type Scalar = { value: number; unit: string; basis: string };
type Profile = { md_m: number[]; tvd_m: number[]; bore_temperature_C: number[]; annulus_temperature_C: number[]; formation_temperature_C: number[]; annulus_wall_pressure_gauge_Pa: number[]; annulus_friction_Pa: number[]; annulus_hydrostatic_pressure_difference_Pa: number[]; property_pressure_absolute_Pa: number[]; component_id: string[]; hole_interval_id: string[] };
type Manifest = { trajectory?:Trajectory; run_id: string; title: string; acceptance: string; physical_request_hash: string; resolved_scenario_hash: string; resolved_scenario: Scenario; profile: Profile; summary: Record<string, Scalar>; warnings: Warning[]; build: { core_version: string; core_commit: string; python: string }; validity: { solver_converged: boolean; energy_status: string; domain_status: string; assessment_status: string }; wall_clock_duration_s: number; failure?: { code: string; message: string }; diagnostics?: unknown; limitations: string[]; numerical_profile: unknown; pressure_reference: unknown; source_ledger: unknown; imported?: boolean };
type Run = { id: string; state: string; scenario_id: string; scenario_revision: number; created_at: string; manifest: Manifest | null };
type Preset = { id: string; title: string; mode: string };
type Capability = { property_cache: { ready: boolean; reason?: string }; engine: { core_version: string; history_capabilities: {wall_history:boolean;tool_history:boolean}; history_message:string; adapter_compatible: boolean }; release_status: string };
type Difference = { md_m: number[]; fields: Record<string, number[]>; input_diff: { path: string; baseline: unknown; variant: unknown }[]; scalar_differences: Record<string, number> };
type Tab = 'temperature' | 'pressure' | 'budget' | 'compare' | 'data' | 'provenance';
const terminal = new Set(['completed', 'failed', 'cancelled', 'timed_out']);
const DRAFT = 'borethm-workbench-draft-v1';
const ACTIVE = 'borethm-workbench-active-v1';
const fmt = (value: number | undefined, decimals = 2) => value == null || !Number.isFinite(value) ? 'Unavailable' : value.toLocaleString('en-GB', { maximumFractionDigits: decimals, minimumFractionDigits: decimals });


function NumericInput({ label, value, min, max, step, invalid, change, onInvalid }: { label: string; value: number; min?: number; max?: number; step?: number; invalid?: boolean; change: (next: number) => void; onInvalid: (invalid: boolean) => void }) {
  const [text, setText] = useState(String(value));
  const inputRef=useRef<HTMLInputElement>(null), feedbackStarted=useRef(0);
  useLayoutEffect(()=>{if(feedbackStarted.current&&inputRef.current)inputRef.current.dataset.feedbackMs=(performance.now()-feedbackStarted.current).toFixed(3);},[text]);
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setText(String(value)); }, [value, invalid]);
  return <input ref={inputRef} type="number" aria-label={label} value={text} min={min} max={max} step={step || 'any'} aria-invalid={invalid || undefined} onFocus={() => { focused.current = true; }} onBlur={() => { focused.current = false; if (text !== '' && Number.isFinite(+text)) setText(String(value)); }} onChange={e => {
    feedbackStarted.current=performance.now();
    const nextText = e.currentTarget.value;
    setText(nextText);
    const next = e.currentTarget.valueAsNumber;
    onInvalid(!Number.isFinite(next));
    if (Number.isFinite(next)) change(next);
  }} />;
}


function download(name: string, content: string) {
  const href = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = href; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

function at(profile: Profile, key: keyof Profile, depth: number): number | undefined {
  return sample(profile.md_m, profile[key] as number[], depth);
}

function Chart({ manifest, baseline, variants, tab, depth, onDepth, onHover, difference }: { manifest: Manifest; baseline: Manifest | null; variants:Manifest[]; tab: Tab; depth: number; onDepth: (depth: number) => void; onHover:(depth:number|null)=>void; difference: Difference | null }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = ref.current!;
    const p = manifest.profile;
    const traces: Data[] = [];
    let xlabel = 'Temperature (°C)';
    let depthAxis = true;
    const curve = (name: string, x: number[], y: number[], color: string, dash?: 'dash' | 'dot', width = 2.5) => traces.push({ name, x, y, type: 'scatter', mode: 'lines', line: { color, width, dash }, hovertemplate: `${tab==='pressure'?'%{x:.5f} MPa':'%{x:.3f} °C'}<br>MD %{y:.2f} m<extra>%{fullData.name}</extra>` });
    if (tab === 'pressure') {
      xlabel = 'Pressure (MPa; gauge / difference as labelled)';
      curve('Wall · gauge', p.annulus_wall_pressure_gauge_Pa.map(x => x/1e6), p.md_m, '#168c83');
      curve('Hydrostatic · difference', p.annulus_hydrostatic_pressure_difference_Pa.map(x => x/1e6), p.md_m, '#8995a1', 'dash');
      curve('Annulus friction · difference', p.annulus_friction_Pa.map(x => x/1e6), p.md_m, '#e78d57');
    } else if (tab === 'budget') {
      depthAxis = false; xlabel = 'Pressure difference (MPa)';
      const keys = ['dp_surface', 'dp_string', 'dp_annulus', 'dp_bit', 'dp_buoyancy'];
      traces.push({ type: 'waterfall', orientation: 'v', x: ['Surface', 'Bore', 'Annulus', 'Nozzles', 'Buoyancy', 'SPP'], y: [...keys.map(k => manifest.summary[k].value / 1e6), 0], measure: ['relative', 'relative', 'relative', 'relative', 'relative', 'total'], connector: { line: { color: '#c6d2d4' } }, increasing: { marker: { color: '#249c91' } }, decreasing: { marker: { color: '#d68b55' } }, totals: { marker: { color: '#344c63' } }, textposition: 'outside', text: [...keys.map(k => fmt(manifest.summary[k].value/1e6, 3)), fmt(manifest.summary.spp.value/1e6, 3)], hovertemplate: '%{x}: %{y:.4f} MPa difference<extra></extra>' } as Data);
    } else if (tab === 'compare' && difference) {
      xlabel = 'Temperature difference (°C; variant − baseline)';
      curve('Bore ΔT', difference.fields.bore_temperature_C, difference.md_m, '#188e87');
      curve('Annulus ΔT', difference.fields.annulus_temperature_C, difference.md_m, '#e78d57');
    } else {
      curve('Formation', p.formation_temperature_C, p.md_m, '#a1a9ae', 'dash', 1.5);
      curve('Bore · downflow', p.bore_temperature_C, p.md_m, '#188e87');
      curve('Annulus · return', p.annulus_temperature_C, p.md_m, '#e78d57');
      if (baseline && baseline.run_id !== manifest.run_id) {
        curve('Baseline bore', baseline.profile.bore_temperature_C, baseline.profile.md_m, '#188e87', 'dot', 1.4);
        curve('Baseline annulus', baseline.profile.annulus_temperature_C, baseline.profile.md_m, '#e78d57', 'dot', 1.4);
      }
      variants.forEach((variant,index)=>{
        if(variant.run_id===manifest.run_id||variant.run_id===baseline?.run_id)return;
        curve(`Variant ${index+1} bore · ${variant.run_id.slice(-6)}`,variant.profile.bore_temperature_C,variant.profile.md_m,['#5554a4','#a35873'][index]||'#596a78','dash',1.8);
        curve(`Variant ${index+1} annulus · ${variant.run_id.slice(-6)}`,variant.profile.annulus_temperature_C,variant.profile.md_m,['#5554a4','#a35873'][index]||'#596a78','dot',1.8);
      });
    }
    const layout: Partial<Layout> = {
      font: { family: 'Inter, Segoe UI, sans-serif', color: '#657585', size: 11 }, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
      margin: { l: 65, r: 30, t: 20, b: 52 }, autosize: true,
      showlegend: tab !== 'budget', legend: { orientation: 'h', y: 1.11, x: 0, font: { size: 10 }, traceorder: 'normal' },
      xaxis: { title: { text: depthAxis ? xlabel : '' }, gridcolor: '#edf0f0', zerolinecolor: '#c7d2d4', ticks: 'outside', tickcolor: '#d9dfdf', fixedrange: false },
      yaxis: { title: { text: depthAxis ? 'Measured depth (m)' : xlabel }, autorange: depthAxis ? 'reversed' : true, gridcolor: '#edf0f0', zerolinecolor: '#c7d2d4', ticks: 'outside', tickformat: depthAxis ? ',.0f' : undefined },
      hovermode: 'closest', uirevision: tab,
      shapes: depthAxis ? [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: depth, y1: depth, line: { color: '#89b9b3', width: 1, dash: 'dot' } }] : [],
    };
    let mounted = true;
    Plotly.react(node, traces, layout, { responsive: true, displaylogo: false, displayModeBar: false }).then(() => {
      if (!mounted) return;
      const received=(manifest as Manifest & {display_received_at_ms?:number}).display_received_at_ms;
      if(received&&node.dataset.displayRun!==manifest.run_id){node.dataset.displayRun=manifest.run_id;node.dataset.profileDisplayMs=(performance.now()-received).toFixed(3);}
      const chart = node as unknown as PlotlyHTMLElement;
      chart.removeAllListeners?.('plotly_click');
      chart.removeAllListeners?.('plotly_hover');
      chart.removeAllListeners?.('plotly_unhover');
      chart.on('plotly_hover', event => {const value=event.points[0]?.y; if(depthAxis && typeof value==='number') onHover(value);});
      chart.on('plotly_unhover', () => onHover(null));
      chart.on('plotly_click', event => {
        const selected = event.points[0]?.y;
        if (depthAxis && typeof selected === 'number') onDepth(selected);
      });
    });
    const observer = new ResizeObserver(() => Plotly.Plots.resize(node));
    observer.observe(node);
    return () => { mounted = false; observer.disconnect(); };
  }, [manifest, baseline, variants, tab, depth, difference]);
  useEffect(() => () => { if (ref.current) Plotly.purge(ref.current); }, []);
  return <div className="plot" ref={ref} aria-label={`${tab} chart. Click a curve to pin depth; use the data tab for numeric values.`} />;
}

function App() {
  const [workspaceView, setWorkspaceView] = useState<'circulation' | 'assessment' | 'history' | 'sweep'>('circulation');
  const [capability, setCapability] = useState<Capability | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetId, setPresetId] = useState('synthetic-water');
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [baseline, setBaseline] = useState<Manifest | null>(null);
  const [variants, setVariants] = useState<Manifest[]>([]);
  const [range, setRange] = useState({minimum:20,maximum:140});
  const [hoverDepth,setHoverDepth] = useState<number|null>(null);
  const [reduceMotion,setReduceMotion]=useState(()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [redo,setRedo] = useState<Scenario[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [tab, setTab] = useState<Tab>('temperature');
  const [show3D,setShow3D]=useState(false),[draftGeometry,setDraftGeometry]=useState(false);
  const [depth, setDepth] = useState(1500);
  const [group, setGroup] = useState('operating');
  const [error, setError] = useState('');
  const [errorDetails,setErrorDetails]=useState('');
  const [showAllRuns,setShowAllRuns]=useState(false);
  const [fieldErrors, setFieldErrors] = useState('');
  const [inputInvalid, setInputInvalid] = useState<Record<string, boolean>>({});
  const [validating, setValidating] = useState(false);
  const [warnings, setWarnings] = useState<Warning[]>([]);
  const [showWarnings, setShowWarnings] = useState(false);
  const [notice, setNotice] = useState('');
  const [difference, setDifference] = useState<Difference | null>(null);
  const [jsonEditor, setJsonEditor] = useState(false);
  const [jsonText, setJsonText] = useState('');
  const [auto, setAuto] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [stage, setStage] = useState('');
  const [connected, setConnected] = useState(true);
  const [recovered, setRecovered] = useState(false);
  const [undo, setUndo] = useState<Scenario[]>([]);
  const scenarioRef = useRef<Scenario | null>(null);
  const selectedRun = useRef<string | null>(null);
  const submittedSnapshot = useRef('');
  const importRef = useRef<HTMLInputElement>(null);
  const busy = submitting || !!run && !terminal.has(run.state);
  const stale = !!manifest && (fingerprint(scenario) !== fingerprint(manifest.resolved_scenario) || Object.values(inputInvalid).some(Boolean));
  const valid = !validating && !fieldErrors && !Object.values(inputInvalid).some(Boolean);

  const setDraft = (next: Scenario) => { scenarioRef.current = next; setScenario(next); };
  const refreshHistory = () => api<{ items: Run[] }>('/runs?mode=circulation_state&limit=5').then(r => setHistory(r.items));

  useEffect(() => {
    Promise.all([api<Capability>('/capabilities'), api<Preset[]>('/presets')]).then(async ([c, p]) => {
      setCapability(c); setPresets(p.filter(item => item.mode === 'circulation_state'));
      const saved = localStorage.getItem(DRAFT);
      let recoveredDraft: Scenario | null = null;
      if (saved) {
        try { recoveredDraft = JSON.parse(saved); } catch { localStorage.removeItem(DRAFT); }
      }
      if (recoveredDraft) { setDraft(recoveredDraft); setPresetId('__draft__'); setRecovered(true); }
      else setDraft(await api<Scenario>('/presets/synthetic-water'));
      const stored = localStorage.getItem(ACTIVE);
      if (stored) {
        try {
          const identity = JSON.parse(stored) as { id: string; snapshot: string };
          submittedSnapshot.current = identity.snapshot; selectedRun.current = identity.id;
          const recoveredRun = await api<Run>('/runs/' + identity.id);
          setRun(recoveredRun);
          if (recoveredRun.state === 'completed' && recoveredRun.manifest && fingerprint(scenarioRef.current) === identity.snapshot) setManifest(recoveredRun.manifest);
        } catch { localStorage.removeItem(ACTIVE); }
      }
      await refreshHistory();
    }).catch(e => setError(String(e.message)));
  }, []);

  useEffect(() => {
    if (!scenario) return;
    localStorage.setItem(DRAFT, JSON.stringify(scenario));
    setValidating(true); setFieldErrors('');
    const controller = new AbortController();
    const timer = setTimeout(() => api<{ warnings: Warning[] }>('/scenarios/validate', { method: 'POST', body: JSON.stringify(scenario), signal: controller.signal }).then(r => {
      setWarnings(r.warnings); setValidating(false);
    }).catch(e => { if (e.name !== 'AbortError') { setFieldErrors(e.message); setValidating(false); } }), 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [scenario]);

  useEffect(() => {
    if (!run || terminal.has(run.state)) return;
    const id = run.id;
    const source = new EventSource(`/api/v1/runs/${id}/events`);
    let sequence = 0;
    setConnected(true);
    const update = async () => {
      try {
        const next = await api<Run>('/runs/' + id);
        setConnected(true);
        if (selectedRun.current !== id) return;
        setRun(next);
        if (terminal.has(next.state)) {
          source.close();
          if (next.state === 'completed' && next.manifest) {
            if (completionMatches(selectedRun.current, id, submittedSnapshot.current, scenarioRef.current)) {
              setManifest(previous=>previous?.run_id===next.manifest!.run_id?previous:{...next.manifest!,display_received_at_ms:performance.now()}); setNotice('');
            } else setNotice('Run finished for earlier inputs. Open it from run history.');
          } else if (next.manifest?.failure) setError(`${next.manifest.failure.code}: ${next.manifest.failure.message}`);
          await refreshHistory();
        }
      } catch { setConnected(false); }
    };
    const listener = (event: MessageEvent) => {
      const value = JSON.parse(event.data) as JobEvent;
      if (selectedRun.current !== id || value.run_id !== id || value.scenario_id !== run.scenario_id || value.scenario_revision !== run.scenario_revision) return;
      if (value.type !== 'reset' && value.sequence <= sequence) return;
      sequence = value.sequence;
      setStage(value.type === 'reset' ? 'Restoring job state' : value.name || value.stage || value.type);
      void update();
    };
    ['queued', 'stage_started', 'completed', 'failed', 'cancelled', 'timed_out', 'reset'].forEach(type => source.addEventListener(type, listener as EventListener));
    source.onopen = () => setConnected(true);
    source.onerror = () => { setConnected(false); void update(); };
    const timer = setInterval(update, 1500);
    void update();
    return () => { source.close(); clearInterval(timer); };
  }, [run?.id, run ? terminal.has(run.state) : true]);

  useEffect(() => {
    setDifference(null);
    if (!baseline || !manifest || baseline.run_id === manifest.run_id || baseline.imported || manifest.imported) return;
    const controller = new AbortController();
    api<Difference>('/comparisons', { method: 'POST', body: JSON.stringify({ baseline_run_id: baseline.run_id, variant_run_id: manifest.run_id, alignment: 'common_md' }), signal: controller.signal }).then(setDifference).catch(e => { if (e.name !== 'AbortError') setNotice(e.message); });
    return () => controller.abort();
  }, [baseline, manifest]);

  useEffect(() => {
    if (!auto || !valid || busy || !scenario || !stale) return;
    const timer = setTimeout(() => void submit(), 400);
    return () => clearTimeout(timer);
  }, [scenario, auto, valid, busy]);

  const edit = (change: (draft: Scenario) => void) => {
    if (!scenario) return;
    setUndo(stack => [...stack.slice(-29), scenario]); setRedo([]);
    const next = structuredClone(scenario); change(next); setDraft(next); setNotice('');
  };

  useEffect(()=>{
    if(!jsonEditor)return;
    const modal=document.querySelector<HTMLElement>('.json-modal');
    const previous=document.activeElement as HTMLElement|null;
    const elements=()=>Array.from(modal?.querySelectorAll<HTMLElement>('button:not(:disabled),textarea,input:not(:disabled)')||[]);
    modal?.querySelector<HTMLTextAreaElement>('textarea')?.focus();
    const key=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){event.preventDefault();setJsonEditor(false);}
      if(event.key==='Tab'){
        const controls=elements(),first=controls[0],last=controls.at(-1);
        if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
        else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
      }
    };
    document.addEventListener('keydown',key);
    return()=>{document.removeEventListener('keydown',key);previous?.focus();};
  },[jsonEditor]);

  async function submit() {
    if (!scenarioRef.current || busy || !valid) return;
    setError(''); setNotice(''); setSubmitting(true); setStage('Submitting frozen scenario');
    const snapshot = fingerprint(scenarioRef.current);
    try {
      const next = await api<Run>('/runs', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ scenario: scenarioRef.current }) });
      selectedRun.current = next.id; submittedSnapshot.current = snapshot; setRun(next);
      localStorage.setItem(ACTIVE, JSON.stringify({ id: next.id, snapshot }));
    } catch (e) { setError((e as Error).message); }
    finally { setSubmitting(false); }
  }

  async function loadPreset(id: string) {
    try {
      const next = await api<Scenario>('/presets/' + id);
      if (scenario) setUndo(stack => [...stack.slice(-29), scenario]);
      setRedo([]); setDraft(next); setPresetId(id); setInputInvalid({}); setRecovered(false); setError('');
    } catch (e) { setError((e as Error).message); }
  }

  async function selectRun(item: Pick<Run,'id'>) {
    try {
      const full = await api<Run>('/runs/' + item.id);
      if (full.state === 'completed' && full.manifest) {
        if (scenario) setUndo(stack => [...stack.slice(-29), scenario]);
        selectedRun.current = full.id; submittedSnapshot.current = fingerprint(full.manifest.resolved_scenario); setRun(full); setRedo([]);
        setPresetId('__draft__'); setRecovered(false); setDraft(full.manifest.resolved_scenario); setManifest(full.manifest); setInputInvalid({}); setError('');
        setDepth(Math.min(depth, full.manifest.profile.md_m.at(-1)!));
        await refreshHistory();
      } else { setError(full.manifest?.failure?.message || `Run is ${full.state}`); }
    } catch (e) { setError((e as Error).message); }
  }

  const [historyImport,setHistoryImport]=useState<HistoryImport|null>(null);
  async function importFile(file: File) {
    try {
      const isZip = file.name.toLowerCase().endsWith('.zip');
      const response = await fetch('/api/v1/imports', { method: 'POST', headers: { 'Content-Type': isZip ? 'application/zip' : 'application/json' }, body: file });
      const result = await response.json();
      if (!response.ok) { setErrorDetails(JSON.stringify(result,null,2)); throw new Error(formatApiError(result)); }
      if (result.scenario.mode === 'wall_history' || result.scenario.mode === 'tool_history') {
        setHistoryImport(result);setWorkspaceView('history');return;
      }
      if (result.scenario.mode === 'site_assessment') {
        localStorage.setItem('borethm-assessment-draft-v1', JSON.stringify(result.scenario));
        if (result.manifest) sessionStorage.setItem('borethm-assessment-import-v1', JSON.stringify(result.manifest));
        setWorkspaceView('assessment');
        return;
      }
      if (scenario) setUndo(stack => [...stack.slice(-29), scenario]);
      setRedo([]); setDraft(result.scenario); setInputInvalid({});
      if (result.manifest) setManifest({ ...result.manifest, imported: true });
      setNotice(isZip ? `Opened verified bundle (${result.verified_artifacts} artifacts). Original engine identity retained.` : 'Scenario imported into a new local draft.');
      setError('');
    } catch (e) { setError((e as Error).message); }
  }

  function number(label: string, value: number, unit: string, change: (value: number) => void, options?: { min?: number; max?: number; step?: number; hint?: string; slider?: boolean }) {
    return <label className="field" key={label}>
      <span className="field-label">{label}<span className="field-unit">{unit}</span></span>
      <div className="number-row"><NumericInput label={label} value={value} min={options?.min} max={options?.max} step={options?.step} invalid={inputInvalid[label]} change={change} onInvalid={invalid => setInputInvalid(old => ({ ...old, [label]: invalid }))} /><span>{unit}</span></div>
      {options?.slider && <input type="range" aria-label={`${label} convenience slider`} min={options.min} max={options.max} step={options.step || 1} value={Math.max(options.min || 0, Math.min(options.max || 100, value))} onChange={e => change(+e.target.value)} />}
      {options?.hint && <small>{options.hint}</small>}
    </label>;
  }

  const comparisonVariants = useMemo(()=>[manifest,...variants].filter((m):m is Manifest=>!!m && m.run_id!==baseline?.run_id).filter((m,i,a)=>a.findIndex(x=>x.run_id===m.run_id)===i).slice(0,3),[manifest,variants,baseline]);
  const undoDraft = () => {const previous=undo.at(-1);if(previous&&scenario){setRedo(stack=>[...stack,scenario]);setUndo(stack=>stack.slice(0,-1));setDraft(previous);setInputInvalid({});}};
  const redoDraft = () => {const next=redo.at(-1);if(next&&scenario){setUndo(stack=>[...stack,scenario]);setRedo(stack=>stack.slice(0,-1));setDraft(next);setInputInvalid({});}};
  if (!scenario) return <div className="loading"><div className="brand-mark"><Waves /></div><h1>BoreTHM Workbench</h1><p>{error || 'Connecting to the local calculation service…'}</p>{error && <button onClick={() => location.reload()}>Retry connection</button>}</div>;
  const d = scenario.definition;
  const p = manifest?.profile;
  const currentDepth = hoverDepth ?? Math.min(depth, Math.max(d.well.bit_md_m,p?.md_m.at(-1)||0));

  const comparisonSet = baseline ? [baseline,...comparisonVariants] : [];
  const selectedComponents = adjacentComponents((manifest?.resolved_scenario||scenario).definition.well.string,currentDepth);
  const tableDepths = p?.md_m || [];
  const tabs: [Tab, string][] = [['temperature', 'Temperature'], ['pressure', 'Pressure'], ['budget', 'Hydraulic budget'], ['compare', 'Compare'], ['data', 'Data'], ['provenance', 'Run details']];
  const metric = (label: string, key: string, unit: string, divisor = 1, icon = <Thermometer size={15} />) => {
    const value = manifest?.summary[key]?.value;
    const old = baseline?.summary[key]?.value;
    const delta = value != null && old != null && baseline?.run_id !== manifest?.run_id ? (value - old)/divisor : undefined;
    return <div className="metric"><div className="metric-label">{label}{icon}</div><div className="metric-value">{fmt(value == null ? undefined : value/divisor, key === 'spp' ? 3 : 2)} <span>{unit}</span></div><div className="metric-foot">{delta == null ? (value == null ? 'Awaiting calculation' : key === 'spp' ? 'Hydraulic budget · difference' : 'Bulk fluid temperature') : <><span className="delta">{delta >= 0 ? '+' : ''}{fmt(delta)} {unit}</span> <span>vs baseline</span></>}</div></div>;
  };

  return <div className={`app-shell ${reduceMotion ? 'reduce-motion' : ''}`}>
    <nav className="rail" aria-label="Workspace navigation"><a className="rail-logo" href="/" aria-label="BoreTHM home"><Waves size={25} /></a><button className={`rail-item ${workspaceView === 'circulation' ? 'active' : ''}`} aria-label="Scenario workbench" title="Workbench" onClick={() => setWorkspaceView('circulation')}><Layers3 size={21} /></button><button className={`rail-item ${workspaceView === 'assessment' ? 'active' : ''}`} aria-label="Full site assessment" title="Full site assessment" onClick={() => setWorkspaceView('assessment')}><ShieldCheck size={21} /></button><button className={`rail-item ${workspaceView === 'history' ? 'active' : ''}`} aria-label="Physical thermal histories" title="Thermal histories" onClick={()=>{if(capability?.engine.history_capabilities.wall_history||capability?.engine.history_capabilities.tool_history)setWorkspaceView('history');else setNotice(capability?.engine.history_message||'Checking installed history capabilities.');}}><Thermometer size={21}/></button><button className={`rail-item ${workspaceView==='sweep'?'active':''}`} aria-label="Parameter sweeps" title="Parameter sweeps" onClick={()=>setWorkspaceView('sweep')}><Columns3 size={21}/></button><button className="rail-item" aria-label="Show run history" title="Run history" onClick={() => document.getElementById('run-history')?.scrollIntoView({ behavior: 'auto' })}><Clock3 size={21} /></button><button className="rail-item" aria-label="Show run provenance" title="Provenance" onClick={() => setTab('provenance')}><Database size={20} /></button><div className="rail-spacer" /><a className="rail-item" href="/docs" target="_blank" title="API documentation" aria-label="API documentation"><Code2 size={20} /></a><button className="rail-item" aria-label="About this development version" title="About" onClick={() => setNotice('BoreTHM Workbench 0.3.0.dev0 . Local circulation and prescribed thermal histories. Results have not been compared with field measurements. See the Workbench guide for limits.')}><CircleHelp size={20} /></button><div className="avatar">LO</div></nav>
    <div className="workspace-shell">
      <header className="topbar"><div className="product-name">BoreTHM <span>Workbench</span><span className="version">0.3.0.dev0</span></div><div className="top-meta"><span className={`connection-dot ${capability?.property_cache.ready ? '' : 'offline'}`} />Local engine <button className="text-button" onClick={()=>void api<Capability>('/capabilities').then(setCapability).catch(e=>setError(e.message))}>Recheck engine</button><span className="separator">/</span> BoreTHM {capability?.engine.core_version || '1.6.0'}<span className="development-badge">DEVELOPMENT</span></div></header>
      <div className="workspace-body">{workspaceView === 'sweep' ? <Sweeps initialScenario={scenario} onOpenRun={id=>{void selectRun({id}).then(()=>setWorkspaceView('circulation'));}}/> : workspaceView === 'assessment' ? <Assessment /> : workspaceView === 'history' ? <History incoming={historyImport} consumed={()=>setHistoryImport(null)}/> : <>
        <aside className="controls">
          <div className="control-heading"><span><Settings2 size={16} /> Scenario inputs</span><button className="icon-button" title="Reset to selected preset (undo available)" aria-label="Reset to preset" disabled={presetId==='__draft__'} onClick={() => void loadPreset(presetId)}><RotateCcw size={15} /></button></div>
          <label className="preset-label">START FROM A PRESET<select value={presetId} onChange={e => void loadPreset(e.target.value)}>{presetId==='__draft__'&&<option value="__draft__" disabled>Recovered / custom draft</option>}{presets.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
          <div className="mode-box"><span className="eyebrow">CALCULATION MODE</span><div><FlaskConical size={16} /><strong>Operating-state exploration</strong></div><p>Run calculates another operating point. Playback inspects saved times. Wall branches change future prescribed boundaries. No moving cold front or pressure wave is simulated.</p></div>
          <div className="control-scroll">
            {(['operating', 'well', 'formation', 'fluid', 'heat'] as const).map((name, i) => <section className={`control-group ${group === name ? 'open' : ''}`} key={name}>
              <button className="group-toggle" aria-expanded={group === name} onClick={() => setGroup(group === name ? '' : name)}><span><span className="group-number">0{i+1}</span>{ { operating: 'Operating conditions', well: 'Well & pipe geometry', formation: 'Formation', fluid: 'Fluid properties', heat: 'Heat & hydraulics' }[name]}</span><ChevronDown size={14} /></button>
              {group === name && <div className="group-content">
                {name === 'operating' && <>
                  {d.operating.flow.kind === 'mass' ? number('Mass flow rate', d.operating.flow.mass_rate_kg_s, 'kg/s', value => edit(s => { s.definition.operating.flow = { kind: 'mass', mass_rate_kg_s: value }; }), { min: 5, max: 80, slider: true, hint: 'Slider: 5–80 kg/s convenience range. Type other positive values.' }) : number('Surface volume flow', d.operating.flow.volume_rate_m3_s * 1000, 'L/s', value => edit(s => { if (s.definition.operating.flow.kind === 'surface_volume') s.definition.operating.flow.volume_rate_m3_s = value/1000; }))}
                  {number('Inlet temperature', d.operating.inlet_temperature_C, '°C', value => edit(s => { s.definition.operating.inlet_temperature_C = value; }), { min: 1, max: 100, slider: true, hint: 'Declared at the string inlet.' })}
                  {number('Wellhead absolute pressure', d.operating.surface_pressure.pressure_absolute_Pa/1e6, 'MPa abs', value => edit(s => { s.definition.operating.surface_pressure.pressure_absolute_Pa = value*1e6; }), { hint: `${fmt(d.operating.surface_pressure.pressure_absolute_Pa/1e6 - .1, 3)} MPa gauge · atmosphere = 0.1 MPa abs` })}
                  {d.operating.exposure.kind === 'legacy_uniform_years' && number('Circulation exposure', d.operating.exposure.years, 'years', value => edit(s => { s.definition.operating.exposure = { kind: 'legacy_uniform_years', years: value }; }), { hint: 'Legacy pathway · minimum 0.001 years, recorded in output.' })}
                  {d.operating.exposure.kind === 'uniform_seconds' && number('Circulation exposure', d.operating.exposure.seconds, 's', value => edit(s => { s.definition.operating.exposure = { kind: 'uniform_seconds', seconds: value }; }), { hint: 'Seconds pathway · minimum 3,600 s, recorded in output.' })}
                  {d.operating.exposure.kind === 'depth_seconds' && <p className="small muted">Depth-dependent exposure is preserved. Edit its table through Scenario JSON.</p>}
                </>}
                {name === 'well' && <>
                  <div className="inline-note"><Layers3 size={15} /><span>{d.well.survey.kind==='vertical'?'Vertical survey':d.well.survey.kind==='stations'?'Full station survey':'MD/TVD only · 3D incomplete'} · {d.well.string.length} string component(s), {d.well.hole.length} hole interval(s).</span></div>
                  <SurveyEditor survey={d.well.survey} length={d.well.bit_md_m} change={survey=>edit(s=>{s.definition.well.survey=survey;})}/>
                  {d.well.string.length === 1 && d.well.hole.length === 1 ? <>
                    {number('Bit measured depth', d.well.bit_md_m, 'm', value => edit(s => { s.definition.well.bit_md_m = value; s.definition.well.string[0].length_m = value; s.definition.well.hole[0].md_bottom_m = value; }))}
                    {number('Hole diameter', d.well.hole[0].r_wall_m*2000, 'mm', value => edit(s => { s.definition.well.hole[0].r_wall_m = value/2000; }))}
                    {d.well.string[0].pipe.layers.length === 1 && <>
                      {number('Pipe bore diameter', d.well.string[0].pipe.layers[0].r_in_m*2000, 'mm', value => edit(s => { s.definition.well.string[0].pipe.layers[0].r_in_m = value/2000; }))}
                      {number('Pipe outer diameter', d.well.string[0].pipe.layers[0].r_out_m*2000, 'mm', value => edit(s => { s.definition.well.string[0].pipe.layers[0].r_out_m = value/2000; }))}
                      {number('Pipe thermal conductivity', d.well.string[0].pipe.layers[0].conductivity_W_m_K, 'W/m·K', value => edit(s => { s.definition.well.string[0].pipe.layers[0].conductivity_W_m_K = value; }))}
                    </>}
                  </> : <p className="small muted">Edit layered and serial assemblies through Scenario JSON. Boundaries and IDs are retained.</p>}
                </>}
                {name === 'formation' && <>
                  {number('Rock conductivity', d.formation.conductivity_W_m_K, 'W/m·K', value => edit(s => { s.definition.formation.conductivity_W_m_K = value; }))}
                  {number('Volumetric heat capacity', d.formation.volumetric_heat_capacity_J_m3_K/1e6, 'MJ/m³·K', value => edit(s => { s.definition.formation.volumetric_heat_capacity_J_m3_K = value*1e6; }))}
                  <div className="geotherm-preview"><span className="eyebrow">GEOTHERM · TVD</span>{d.formation.geotherm.points_tvd_m_temperature_C.map(([z, t], index) => <div key={index}><span>{fmt(z, 0)} m</span><strong>{fmt(t, 1)} °C</strong></div>)}<small>Below last point: {fmt(d.formation.geotherm.gradient_below_K_m*1000, 1)} K/km</small></div>
                  <p className="small muted">Use Scenario JSON to change geotherm points and their source.</p>
                </>}
                {name === 'fluid' && <>
                  <label className="field"><span className="field-label">Fluid model</span><select value={d.fluid.kind} onChange={e => edit(s => {
                    if (e.target.value === 'iapws_water') s.definition.fluid = { kind: 'iapws_water' };
                    else if (e.target.value === 'barite_mud') s.definition.fluid = { kind: 'barite_mud', surface_density_kg_m3: 1200, plastic_viscosity_Pa_s: .02, viscosity_reference_temperature_C: 20, yield_point_Pa: 5, conserve_mass: true };
                    else s.definition.fluid = { kind: 'constant_properties', density_kg_m3: 1000, heat_capacity_J_kg_K: 4200, viscosity_Pa_s: .001, conductivity_W_m_K: .6, approximation_label: 'Constant-property approximation; synthetic case' };
                  })}><option value="iapws_water">Water · IAPWS-95</option><option value="barite_mud">Barite-weighted mud</option><option value="constant_properties">Constant-property approximation</option></select></label>
                  {d.fluid.kind === 'iapws_water' && <div className="inline-note"><ShieldCheck size={17} /><span>Temperature and pressure dependent. Table: 1–479 °C, 0.1–218.1 MPa absolute.</span></div>}
                  {d.fluid.kind === 'barite_mud' && <>
                    {number('Surface density', d.fluid.surface_density_kg_m3, 'kg/m³', value => edit(s => { if (s.definition.fluid.kind === 'barite_mud') s.definition.fluid.surface_density_kg_m3 = value; }), { hint: 'At 20 °C and 0.1 MPa absolute.' })}
                    {number('Plastic viscosity', d.fluid.plastic_viscosity_Pa_s*1000, 'mPa·s', value => edit(s => { if (s.definition.fluid.kind === 'barite_mud') s.definition.fluid.plastic_viscosity_Pa_s = value/1000; }))}
                    {number('Yield point', d.fluid.yield_point_Pa, 'Pa', value => edit(s => { if (s.definition.fluid.kind === 'barite_mud') s.definition.fluid.yield_point_Pa = value; }))}
                    <p className="small muted">Mass-conserving mixture with the core Bingham formulation; validity qualifications remain visible.</p>
                  </>}
                  {d.fluid.kind === 'constant_properties' && <p className="small muted">Constant properties are a synthetic approximation. Edit individual properties through Scenario JSON.</p>}
                </>}
                {name === 'heat' && <>
                  {number('Base face heat', d.heat.face_heat_W/1000, 'kW', value => edit(s => { s.definition.heat.face_heat_W = value*1000; }))}
                  {number('Nozzle total flow area', d.hydraulics.nozzle_area_m2*1e6, 'mm²', value => edit(s => { s.definition.hydraulics.nozzle_area_m2 = value/1e6; }))}
                  <label className="check-row"><input type="checkbox" checked={d.heat.friction_heating} onChange={e => edit(s => { s.definition.heat.friction_heating = e.target.checked; })} /><span>Include friction heating</span></label>
                  <div className="inline-note"><span>Surface losses affect pump pressure; they do not become downhole heat. Rotary partition is unavailable in this build.</span></div>
                </>}
              </div>}
            </section>)}
            <button className="json-button" onClick={() => { setJsonText(JSON.stringify(scenario, null, 2)); setJsonEditor(true); }}><FileJson size={15} />Scenario JSON<ArrowUpRight size={13} /></button>
          </div>
          <div className="control-footer">
            <label className="check-row auto"><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /><span>Auto-run after edits</span><span className="muted">400 ms</span></label>
            <button className="run-button" disabled={!busy && (!valid || !capability?.property_cache.ready || !capability?.engine.adapter_compatible)} onClick={() => busy && run ? void api(`/runs/${run.id}/cancel`, { method: 'POST' }).then(() => setStage('Cancellation requested')).catch(e => setError(e.message)) : void submit()}>{busy ? <Square size={14} /> : <Play size={14} fill="currentColor" />}{busy ? 'Cancel calculation' : 'Run calculation'}{busy ? <LoaderCircle className="spin" size={16} /> : <span>↵</span>}</button>
            <div className="control-footer-meta"><span>{validating ? 'Validating…' : valid ? <><Check size={11} /> Inputs validated</> : <><TriangleAlert size={11} /> Check inputs</>}</span><button disabled={!undo.length} onClick={undoDraft}>Undo</button><button disabled={!redo.length} onClick={redoDraft}>Redo</button></div>
          </div>
        </aside>

        <main className="main">
          <div className="breadcrumb">Workspace <span>/</span> Circulation workbench</div>
          <div className="page-heading"><div><div className="eyebrow"><span className="tiny-line" />OPERATING-STATE EXPLORATION</div><h1>{scenario.title}</h1><div className="page-subtitle"><span className="tag">{(Array.isArray(scenario.metadata?.tags) && scenario.metadata.tags.includes('synthetic')) ? 'SYNTHETIC CASE' : 'SCENARIO'}</span><span>{fmt(d.well.bit_md_m, 0)} m MD</span><span>·</span><span>{d.well.survey.kind==='vertical'?'Vertical well':'Surveyed well'}</span><span>·</span><span>{d.fluid.kind === 'iapws_water' ? 'IAPWS-95 water' : d.fluid.kind === 'barite_mud' ? 'Barite mud' : 'Constant properties'}</span></div></div><div className="heading-actions"><input ref={importRef} type="file" accept=".json,.zip" hidden onChange={e => { if (e.target.files?.[0]) void importFile(e.target.files[0]); e.target.value = ''; }} /><button className="secondary-button" onClick={() => importRef.current?.click()}><Upload size={14} />Import</button><button className="secondary-button" onClick={() => download('scenario.json', JSON.stringify(scenario, null, 2))}><FileJson size={14} />Save draft</button></div></div>
          {(recovered || notice) && <div className="notice"><CircleHelp size={15} /><span>{notice || 'Recovered your local draft. Calculations retain their saved input revision.'}</span><button aria-label="Dismiss notice" onClick={() => { setNotice(''); setRecovered(false); }}><X size={14} /></button></div>}
          {(!capability?.property_cache.ready || !capability?.engine.adapter_compatible) && <div className="error"><TriangleAlert size={16} />{capability?.property_cache.reason || 'Installed model is unavailable.'}</div>}
          {(error || fieldErrors || Object.values(inputInvalid).some(Boolean)) && <div className="error" role="alert"><TriangleAlert size={17} /><div><strong>{fieldErrors ? 'Check the scenario inputs' : 'Calculation needs attention'}</strong><pre>{fieldErrors || error || 'Enter a finite number in each marked field.'}</pre>{errorDetails&&<details><summary>Technical details</summary><pre>{errorDetails}</pre></details>}</div><button aria-label="Dismiss calculation error" onClick={() => setError('')}><X size={14} /></button></div>}
          <div className="result-status"><div><span className={`status-light ${busy ? 'working' : manifest ? 'solved' : ''}`} />{busy ? <><strong>{stage || run?.state}</strong><span>Previous result remains visible</span></> : manifest ? <><strong>{stale ? 'Previous result; inputs changed' : 'Calculated operating state'}</strong><span>{manifest.imported ? 'Imported immutable bundle' : `${fmt(manifest.wall_clock_duration_s, 2)} s · revision ${run?.scenario_revision || 'saved'}`}</span></> : <><strong>Ready to explore</strong><span>Run the scenario to calculate temperatures and pressure</span></>}</div>{manifest && <button className="qualification-badge" onClick={() => setShowWarnings(!showWarnings)}><TriangleAlert size={12} />{manifest.acceptance} · {manifest.warnings.length} qualifications<ChevronDown size={12} /></button>}</div>
          {!connected && busy && <div className="notice"><TriangleAlert size={15} />Connection interrupted. Showing last known job state; reconnecting without resubmitting.</div>}
          {showWarnings && manifest && <div className="qualification-panel"><h3>Calculation qualifications</h3>{manifest.warnings.map((w, i) => <div key={i}><span>{w.code}</span><p>{w.message}</p></div>)}<p className="small muted">Programme status: not evaluated. This component run does not establish a whole-programme GO status.</p></div>}
          <div className={`metrics ${stale ? 'stale' : ''}`}>{metric('Bit bore delivery', 'bit_bore_delivery_temperature', '°C')}{metric('Annulus above bit', 'bit_annulus_temperature', '°C')}{metric('Surface return', 'surface_return_temperature', '°C', 1, <ArrowUpRight size={16} />)}{metric('Standpipe pressure', 'spp', 'MPa', 1e6, <Waves size={16} />)}</div>

          <section className="visual-panel" aria-label="Linked scientific views">
            <div className="panel-toolbar"><div className="tabs" role="tablist">{tabs.map(([id, title]) => <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{title}{id === 'compare' && baseline && <span className="tab-dot" />}</button>)}</div><button className="icon-button" title="Data table" aria-label="Show numeric data table" onClick={() => setTab('data')}><Columns3 size={16} /></button></div>
            <div className="view-controls" aria-label="Display controls; no calculation is submitted">
              <span>Bulk temperature colours</span>
              <label>Minimum °C <input aria-label="Colour minimum temperature" type="number" value={range.minimum} onChange={e=>{const v=e.currentTarget.valueAsNumber;if(Number.isFinite(v)&&v<range.maximum)setRange({...range,minimum:v});}}/></label>
              <label>Maximum °C <input aria-label="Colour maximum temperature" type="number" value={range.maximum} onChange={e=>{const v=e.currentTarget.valueAsNumber;if(Number.isFinite(v)&&v>range.minimum)setRange({...range,maximum:v});}}/></label>
              <button onClick={()=>{const values=[manifest,...comparisonSet].flatMap(m=>m?[...m.profile.bore_temperature_C,...m.profile.annulus_temperature_C]:[]);if(values.length)setRange({minimum:Math.floor(Math.min(...values)),maximum:Math.ceil(Math.max(...values))+1});}}>Autoscale displayed runs</button>
              <span>Fixed across compared runs · view only</span><label><input aria-label="Reduce motion" type="checkbox" checked={reduceMotion} onChange={e=>setReduceMotion(e.target.checked)}/>Reduce motion</label>
            </div>
            <div className="scene-mode-controls"><button aria-pressed={!show3D} onClick={()=>setShow3D(false)}>2D views</button><button aria-pressed={show3D} onClick={()=>setShow3D(true)}>3D view</button>{show3D&&<label><input type="checkbox" checked={draftGeometry} onChange={e=>setDraftGeometry(e.target.checked)}/>Preview draft geometry without results</label>}</div>
            {show3D&&<Suspense fallback={<p role="status">Loading 3D viewer…</p>}><ScenarioScene scenario={draftGeometry?scenario:manifest?.resolved_scenario||scenario} manifest={draftGeometry?null:manifest} depth={currentDepth} onDepth={setDepth} range={range} alternatives={[baseline,...variants].filter((m):m is Manifest=>!!m&&!!m.trajectory).map((m,i)=>({label:i===0?'Baseline':`Variant ${i}`,well:m.resolved_scenario.definition.well,trajectory:m.trajectory!,profile:m.profile,identity:m.run_id,pressureBasis:(m.pressure_reference as {wall_basis?:string})?.wall_basis}))}/></Suspense>}
            <div className="view-subtitle"><span>{tab === 'temperature' ? 'Linked temperature profiles' : tab === 'pressure' ? 'Annulus wall-pressure reconstruction' : tab === 'budget' ? 'Standpipe pressure decomposition' : tab === 'compare' ? 'Baseline & variant' : tab === 'data' ? 'Authoritative numerical samples' : 'Scientific identity & provenance'}</span><span>{tab === 'pressure' ? `${(manifest?.pressure_reference as {wall_basis?:string})?.wall_basis||'wall_budget_v1'} · gauge / difference` : tab === 'budget' ? 'hydraulic_budget_v160 · signed terms' : 'MD ↓ · SI units'}{stale && <em>Previous inputs</em>}</span></div>
            {tab === 'data' ? <div className="data-scroll">{p ? <table><caption>Run {manifest?.run_id.slice(-8)} · {manifest?.acceptance} · original float64 samples; display values rounded</caption><thead><tr><th>MD (m)</th><th>Bore (°C)</th><th>Annulus (°C)</th><th>Formation (°C)</th><th>Wall (MPa gauge)</th><th>Annulus friction (MPa Δ)</th></tr></thead><tbody>{tableDepths.map((z, i) => <tr key={i} onClick={() => setDepth(z)}><td>{fmt(z, 1)}</td><td>{fmt(p.bore_temperature_C[i], 3)}</td><td>{fmt(p.annulus_temperature_C[i], 3)}</td><td>{fmt(p.formation_temperature_C[i], 3)}</td><td>{fmt(p.annulus_wall_pressure_gauge_Pa[i]/1e6, 5)}</td><td>{fmt(p.annulus_friction_Pa[i]/1e6, 5)}</td></tr>)}</tbody></table> : <Empty />}</div> : tab === 'provenance' ? <div className="provenance">{manifest ? <><details><summary>Recorded initialisation and solver attempts</summary><pre>{JSON.stringify((manifest as Manifest & {attempts?:unknown}).attempts,null,2)}</pre></details><div className="provenance-grid"><div><span>RUN ID</span><code>{manifest.run_id}</code></div><div><span>PHYSICAL REQUEST SHA-256</span><code>{manifest.physical_request_hash}</code></div><div><span>ENGINE</span><p>BoreTHM {manifest.build.core_version} · Python {manifest.build.python}</p><code>{manifest.build.core_commit}</code></div><div><span>INDEPENDENT VALIDITY CHECKS</span><p>Solver: {manifest.validity.solver_converged ? 'converged' : 'failed'} · Energy: {manifest.validity.energy_status}<br />Domain: {manifest.validity.domain_status} · Assessment: {manifest.validity.assessment_status}</p></div></div><details><summary>Pressure conventions</summary><pre>{JSON.stringify(manifest.pressure_reference, null, 2)}</pre></details><details><summary>Numerical profile</summary><pre>{JSON.stringify(manifest.numerical_profile, null, 2)}</pre></details><details><summary>Heat source ledger</summary><pre>{JSON.stringify(manifest.source_ledger, null, 2)}</pre></details><details><summary>Input provenance & assumptions</summary><pre>{JSON.stringify(manifest.resolved_scenario.provenance, null, 2)}</pre></details><div className="limitations">{manifest.limitations.map(text => <p key={text}>· {text}</p>)}</div></> : <Empty />}</div> : <>
              {tab === 'compare' && !baseline ? <div className="compare-empty"><Columns3 size={28} /><h3>Give your experiment a baseline</h3><p>Calculate a scenario, pin it as baseline, change an input and run again.<br />Differences use common MD and compatible pressure bases.</p><button className="secondary-button" disabled={!manifest} onClick={() => manifest && setBaseline(manifest)}><Plus size={14} />Pin current result as baseline</button>{notice && <p>{notice}</p>}</div> : <div className="linked-view"><Well scenario={manifest?.resolved_scenario || scenario} manifest={manifest} depth={currentDepth} onDepth={setDepth} range={range} /><div className="chart-region">{manifest ? <Chart manifest={manifest} baseline={baseline} variants={comparisonVariants} tab={tab} depth={currentDepth} onDepth={setDepth} onHover={setHoverDepth} difference={difference} /> : <Empty />}</div></div>}
              {tab === 'compare' && baseline && !difference && <p className="small muted">Absolute profiles shown. A common-MD difference requires a distinct current run with compatible geometry and pressure bases.</p>}
              {tab === 'compare' && difference && <details className="input-diff"><summary>{difference.input_diff.length} input differences · variant minus baseline</summary>{difference.input_diff.map(item => <div key={item.path}><code>{item.path}</code><span>{JSON.stringify(item.baseline)} → {JSON.stringify(item.variant)}</span></div>)}</details>}
            </>}
            <div className="depth-inspector"><div className="depth-control"><ZoomIn size={16} /><label htmlFor="selected-depth">PINNED DEPTH</label><input id="selected-depth" aria-label="Pinned measured depth" type="number" min="0" max={Math.max(d.well.bit_md_m,...comparisonSet.map(m=>m.profile.md_m.at(-1)||0))} value={Math.round(currentDepth*100)/100} onChange={e => { if (e.currentTarget.value !== '') setDepth(Math.max(0,+e.target.value)); }} /><span>m MD</span></div><div className="depth-values"><span>Bore <strong>{fmt(p ? at(p, 'bore_temperature_C', currentDepth) : undefined,3)} °C</strong></span><span>Annulus <strong>{fmt(p ? at(p, 'annulus_temperature_C', currentDepth) : undefined,3)} °C</strong></span><span>Wall pressure <strong>{fmt(p ? (at(p, 'annulus_wall_pressure_gauge_Pa', currentDepth) ?? NaN)/1e6 : undefined, 5)} MPa g</strong></span></div><span className="sample-note">{p ? p.md_m.includes(currentDepth) ? 'Numerical sample' : 'Display interpolation' : 'Awaiting result'}</span></div>
          </section>

          <div className="geometry-inspector"><strong>{hoverDepth == null?'Pinned':'Hover'} depth · {fmt(currentDepth,1)} m MD</strong><span>{selectedComponents.length?selectedComponents.map(id=>{const component=(manifest?.resolved_scenario||scenario).definition.well.string.find(s=>s.id===id);return `${id}: ${component?.pipe.name}`;}).join(' | boundary sides: '):'Current run unavailable at this depth'}</span><span>{(manifest?.resolved_scenario||scenario).definition.well.hole.filter(h=>currentDepth>=h.md_top_m&&currentDepth<=h.md_bottom_m).map(h=>`${h.id}: wall radius ${h.r_wall_m} m, ${h.layers.length} material layers`).join(' | ')}</span></div>
          {baseline&&<section className="comparison-panel" aria-label="Baseline and up to three variants"><div className="section-heading"><h2>Baseline and variants</h2><span>Current variant plus pinned runs · maximum three variants</span></div><div className="comparison-scroll"><table><caption>Absolute accepted results; unavailable depths are never snapped to a shorter bit. Each result retains its qualifications.</caption><thead><tr><th>Run / role</th><th>Fluid / geometry</th><th>Flow kg/s</th><th>Bore at cursor °C</th><th>Annulus at cursor °C</th><th>SPP MPa Δ</th></tr></thead><tbody>{comparisonSet.map((m,i)=><tr key={m.run_id}><td>{i===0?'Baseline':`Variant ${i}`} · {m.run_id.slice(-8)} · {m.acceptance}</td><td>{m.resolved_scenario.definition.fluid.kind} · {m.resolved_scenario.definition.well.bit_md_m} m MD</td><td>{fmt(m.summary.mass_flow.value)}</td><td>{fmt(at(m.profile,'bore_temperature_C',currentDepth))}</td><td>{fmt(at(m.profile,'annulus_temperature_C',currentDepth))}</td><td>{fmt(m.summary.spp.value/1e6,3)}</td></tr>)}</tbody></table></div><details><summary>Input differences: current run versus baseline</summary>{manifest&&inputChanges({scenario:baseline.resolved_scenario,build:baseline.build,pressure_reference:baseline.pressure_reference},{scenario:manifest.resolved_scenario,build:manifest.build,pressure_reference:manifest.pressure_reference}).map((change,i)=><div key={i}><code>{change.path}</code><pre>{JSON.stringify(change.baseline)} → {JSON.stringify(change.variant)}</pre></div>)}</details><details><summary>Exact input, numerical/model versions and pressure bases for every compared run</summary>{comparisonSet.map((m,i)=><div key={m.run_id}><h3>{i===0?'Baseline':`Variant ${i}`} · {m.run_id}</h3><pre>{JSON.stringify({scenario:m.resolved_scenario,build:m.build,pressure_reference:m.pressure_reference},null,2)}</pre></div>)}</details></section>}
          <div className="lower-grid"><section className="history-panel" id="run-history"><div className="section-heading"><h2><Clock3 size={16} />Run history</h2><button className="text-button" onClick={()=>setShowAllRuns(true)}>All runs</button></div>{history.length ? <div className="run-list">{history.slice(0, 5).map(item => <div className={`run-row ${item.id === manifest?.run_id ? 'selected' : ''}`} key={item.id}><button className="run-select" onClick={() => void selectRun(item)}><span className={`run-symbol ${item.state === 'completed' ? 'complete' : 'other'}`}>{item.state === 'completed' ? <Check size={12} /> : <Clock3 size={12} />}</span><span><strong>{item.manifest?.title || 'Circulation run'}</strong><small>{item.id.slice(-8)} · {new Date(item.created_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}</small></span></button><span className="run-result">{item.manifest?.summary?.spp ? `${fmt(item.manifest.summary.spp.value/1e6, 3)} MPa` : item.state}</span><button className={`baseline-button ${baseline?.run_id === item.id ? 'pinned' : ''}`} disabled={item.state !== 'completed'} onClick={async () => { try { const full = await api<Run>('/runs/' + item.id); if (full.manifest) {setBaseline(full.manifest);setVariants(old=>old.filter(m=>m.run_id!==full.id));} } catch (e) { setError((e as Error).message); } }}>{baseline?.run_id === item.id ? 'Baseline' : 'Set baseline'}</button><button className="baseline-button" disabled={item.state!=='completed'||baseline?.run_id===item.id} onClick={async()=>{if(variants.some(m=>m.run_id===item.id)){setVariants(old=>old.filter(m=>m.run_id!==item.id));return;}if(variants.length>=3){setNotice('Remove a saved variant before adding another. The comparison includes at most three variants.');return;}try{const full=await api<Run>('/runs/'+item.id);if(full.manifest)setVariants(old=>[...old,full.manifest!]);}catch(e){setError((e as Error).message);}}}>{variants.some(m=>m.run_id===item.id)?'Remove variant':'Add variant'}</button></div>)}</div> : <div className="empty-history">Your completed runs will appear here. Each run preserves its exact inputs.</div>}</section>
            <section className="context-panel"><div className="section-heading"><h2><ShieldCheck size={16} />Calculation context</h2></div><div className="context-row"><span>Model</span><strong>1D circulation · radial rock response</strong></div><div className="context-row"><span>Numerics</span><strong>{manifest ? manifest.resolved_scenario.numerics.profile + ' · ' + (manifest.resolved_scenario.numerics.initialisation === 'interactive-bounded-v1' ? 'bounded initialisation' : 'deterministic start') : 'No accepted result'}</strong></div><div className="context-row"><span>Run ID</span><strong data-testid="result-run-id">{manifest?.run_id || 'No accepted result'}</strong></div><div className="context-row"><span>Programme assessment</span><strong className="neutral">Not evaluated</strong></div><p>Profiles describe an operating state. Solver iterations are not physical time. All views share the same accepted numerical result.</p><div className="context-actions"><button disabled={!manifest} onClick={() => setShowWarnings(!showWarnings)}><TriangleAlert size={13} />Qualifications</button>{manifest && !manifest.imported ? <a href={`/api/v1/runs/${manifest.run_id}/export`}><ArrowDownToLine size={13} />Export run</a> : <span className="muted small">{manifest?.imported ? 'Imported bundle' : 'No result to export'}</span>}</div></section></div>
          <footer className="page-footer"><span><span className="connection-dot" />Local calculation · immutable run artifacts</span><span>Development demonstration · synthetic presets are not drilling programmes</span></footer>
        </main></>}
      </div>
    </div>
    {showAllRuns && <RunBrowser onClose={()=>setShowAllRuns(false)} onOpen={async id=>{await selectRun({id});setShowAllRuns(false);}}/>}
    {jsonEditor && <div className="modal-backdrop"><section className="json-modal" role="dialog" aria-modal="true" aria-labelledby="json-title"><div className="section-heading"><h2 id="json-title">Scenario JSON</h2><button className="icon-button" aria-label="Close JSON editor" onClick={() => setJsonEditor(false)}><X size={18} /></button></div><p>Full schema 1.0 inputs. Unknown keys and unsupported capabilities are rejected. Physical units are SI as named.</p><textarea aria-label="Scenario JSON contents" value={jsonText} onChange={e => setJsonText(e.target.value)} spellCheck={false} /><div className="modal-actions"><button className="secondary-button" onClick={() => setJsonEditor(false)}>Cancel</button><button className="run-button" onClick={async () => { try { const parsed = JSON.parse(jsonText); const checked = await api<{resolved_scenario:Scenario}>('/scenarios/validate', { method: 'POST', body: JSON.stringify(parsed) }); setUndo(stack => [...stack, scenario]); setRedo([]); setDraft(checked.resolved_scenario); setInputInvalid({}); setJsonEditor(false); setError(''); } catch (e) { setError((e as Error).message); setNotice('JSON validation failed; close the editor to inspect the field error.'); } }}>Validate & apply</button></div></section></div>}
  </div>;
}

function Empty() { return <div className="empty-chart"><div className="empty-icon"><Waves size={30} strokeWidth={1.4} /></div><h3>Your well, calculated.</h3><p>Adjust the inputs and run the circulation model.<br />Accepted temperatures and pressure will appear here.</p><div><span /><span /><span /></div></div>; }

createRoot(document.getElementById('root')!).render(<App />);
