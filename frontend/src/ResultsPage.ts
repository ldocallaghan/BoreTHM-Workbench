// Results page for a completed assessment, and the tested-range panel shared
// with circulation runs. Written with createElement so the Node test runner
// can render it without a build step. Every number comes from the results
// payload, which holds model values, and is shown through a unit conversion.
import {createElement as h, Fragment, type ReactNode} from 'react';
import {format, type UnitSystem} from './units.ts';

type Bound = {state: string; case: string; md_m: number | null} | null;
type Fluid = {label: string; open: boolean | null; lower_kg_m3: number | null; upper_kg_m3: number | null; width_kg_m3: number | null; lower_by: Bound; upper_by: Bound; chosen_kg_m3: number | null; note: string | null};
type ToolRow = {item: string; peak_C: number | null; rating_C: number | null; margin_C: number | null; ok?: boolean | null; minutes_above_rating?: number | null};
export type AssessmentSummary = {
  verdict: {status: string | null; reasons: string[]; controlling_constraint: string | null; reading: Record<string, string>};
  site: {name?: string; target_depth_m?: number; target_T_C?: number; tier?: string};
  programme: {pipe: string | null; mass_flow_kg_s: number | null; fluid_kg_m3: number | null; water: boolean; backpressure_Pa: number | null; bit_C: number | null; return_C: number | null};
  windows: {fluids: Record<string, Fluid>; by_case: {state: string; case: string; lower_kg_m3: number | null; lower_md_m: number | null; upper_kg_m3: number | null; upper_md_m: number | null}[]; evaluated_depths_m: number[]; profiles_returned: boolean};
  tools: {rating_C: number | null; status: string | null; rows: ToolRow[]; coating: {pipe: string | null; status: string | null; rows: ToolRow[]}};
  stability: {verdicts: Record<string, string>; deciding_case: string | null; cases: {case: string; verdict: string; states: Record<string, string>}[]; site_verdict: string | null};
  pressure: {standpipe_Pa: number | null; trip_standpipe_Pa: number | null; within_rig_pumps: boolean | null; pumps_ok: boolean | null; pump_limit_Pa: number | null};
  input_warnings: string[];
};
export type Flags = {outside: {input: string; message: string}[]; notes: {input: string; message: string}[]} | null;
export type Qualification = {code: string; message: string; path?: string};
export type ResultsPayload = {mode: string; units: UnitSystem; run_id: string; title: string; flags: Flags; qualifications: Qualification[]; summary: AssessmentSummary | Record<string, unknown>; figures: Record<string, string>};
export type DetailSection = 'windows' | 'cases' | 'equipment' | 'profile' | 'overview' | 'qualifications';

const STATES: [string, string][] = [['drilling', 'Drilling'], ['connection', 'Connection'], ['connection, fresh rock', 'Connection, fresh rock'], ['trip', 'Trip']];

function Figure({svg, caption}: {svg: string; caption?: string}) {
  return h('figure', {className: 'result-figure'}, h('div', {className: 'svg-figure', dangerouslySetInnerHTML: {__html: svg}}), caption ? h('figcaption', null, caption) : null);
}

function Section({id, title, detail, onDetails, children}: {id: string; title: string; detail?: DetailSection; onDetails?: (s: DetailSection) => void; children?: ReactNode}) {
  return h('section', {className: 'result-section', 'aria-labelledby': id},
    h('div', {className: 'section-heading'}, h('h2', {id}, title), detail && onDetails ? h('button', {className: 'text-button', onClick: () => onDetails(detail)}, 'Show details') : null),
    children);
}

function verdictClass(status: string | null) {
  return status === 'GO' ? 'go' : status === 'NO-GO' ? 'no-go' : status === 'CONDITIONAL' ? 'conditional' : 'neutral';
}

function bound(b: Bound, system: UnitSystem) {
  if (!b) return 'Not returned';
  const depth = b.md_m == null ? 'no binding depth returned' : format(b.md_m, 'length', system);
  return `${b.state}, ${b.case}, ${depth}`;
}

export function FlagsPanel({flags, qualifications, inputWarnings}: {flags: Flags; qualifications: Qualification[]; inputWarnings?: string[]}) {
  return h(Fragment, null,
    h('div', {className: 'flags-panel'},
      h('h3', null, 'Inputs outside the field-tested range'),
      flags == null ? h('p', null, 'Not applicable to this run.') : flags.outside.length ? h('ul', {className: 'flag-list'}, flags.outside.map((f, i) => h('li', {key: i}, f.message))) : h('p', null, 'All compared inputs are inside the field-tested range.'),
      flags && flags.notes.length ? h('details', null, h('summary', null, 'Notes on the field comparisons'), h('ul', null, flags.notes.map((n, i) => h('li', {key: i}, n.message)))) : null),
    inputWarnings && inputWarnings.length ? h('div', {className: 'flags-panel'}, h('h3', null, 'Site input warnings'), h('ul', null, inputWarnings.map((w, i) => h('li', {key: i}, w)))) : null,
    h('div', {className: 'flags-panel'}, h('h3', null, `Qualifications (${qualifications.length})`),
      qualifications.length ? h('ul', {className: 'qualification-list'}, qualifications.map((q, i) => h('li', {key: i}, h('code', null, q.code), ' ', q.message))) : h('p', null, 'None.')));
}

export function ResultsPage({payload, system, onDetails, reportHref}: {payload: ResultsPayload; system: UnitSystem; onDetails?: (section: DetailSection) => void; reportHref?: string}) {
  const s = payload.summary as AssessmentSummary;
  const v = s.verdict;
  const fluids = Object.values(s.windows.fluids);
  const toolRows = [...s.tools.rows, ...s.tools.coating.rows];
  return h('div', {className: 'results-page', 'data-status': v.status || ''},
    h('section', {className: `verdict-panel ${verdictClass(v.status)}`, 'aria-label': 'Verdict'},
      h('div', {className: 'verdict-badge'}, v.status || 'Not evaluated'),
      h('div', null,
        h('p', {className: 'verdict-constraint'}, h('strong', null, 'Controlling constraint: '), v.controlling_constraint || 'Not returned'),
        v.reasons.length ? h('ul', {className: 'verdict-reasons'}, v.reasons.map((r, i) => h('li', {key: i}, r))) : null,
        h('p', {className: 'verdict-reading'}, [v.reading.calculation, v.reading.whole_programme].filter(Boolean).join(' ')),
        reportHref ? h('a', {className: 'secondary-button', href: reportHref, download: true}, 'Export report') : null)),
    h(Section, {id: 'results-range', title: 'Field-tested range and qualifications', detail: 'qualifications', onDetails},
      h(FlagsPanel, {flags: payload.flags, qualifications: payload.qualifications, inputWarnings: s.input_warnings})),
    h(Section, {id: 'results-window', title: 'Mud-weight window', detail: 'windows', onDetails},
      h('div', {className: 'table-scroll'}, h('table', {className: 'result-table'},
        h('thead', null, h('tr', null, ['Fluid', 'Window', 'Lower bound', 'Upper bound', 'Width', 'Lower bound set by', 'Upper bound set by', 'Fluid chosen'].map(t => h('th', {key: t}, t)))),
        h('tbody', null, fluids.map(f => h('tr', {key: f.label},
          h('td', null, f.label),
          h('td', {className: f.open ? 'ok' : 'fail'}, f.open ? 'Open' : 'Closed'),
          h('td', {className: 'n'}, format(f.lower_kg_m3, 'density', system), h('br'), format(f.lower_kg_m3, 'specific_gravity', system)),
          h('td', {className: 'n'}, format(f.upper_kg_m3, 'density', system), h('br'), format(f.upper_kg_m3, 'specific_gravity', system)),
          h('td', {className: 'n'}, format(f.width_kg_m3, 'density', system)),
          h('td', null, bound(f.lower_by, system)),
          h('td', null, bound(f.upper_by, system)),
          h('td', {className: 'n'}, f.chosen_kg_m3 == null ? (f.note || 'None') : format(f.chosen_kg_m3, 'density', system))))))),
      h('p', {className: 'small muted'}, 'Surface densities at 20 °C. Width is the upper bound minus the lower bound. The model returns each bound with its binding depth; it does not return the bounds against depth, so the chart shows each state and case.'),
      payload.figures.window ? h(Figure, {svg: payload.figures.window}) : null),
    h(Section, {id: 'results-tools', title: 'Tools and equipment', detail: 'equipment', onDetails},
      h('p', null, `Tool check: ${s.tools.status ?? 'not returned'}. Coating (${s.tools.coating.pipe ?? 'none'}): ${s.tools.coating.status ?? 'not returned'}.`),
      h('div', {className: 'table-scroll'}, h('table', {className: 'result-table'},
        h('thead', null, h('tr', null, ['Item', 'Peak', 'Rating', 'Margin', 'Check'].map(t => h('th', {key: t}, t)))),
        h('tbody', null, toolRows.map(r => h('tr', {key: r.item},
          h('td', null, r.item),
          h('td', {className: 'n'}, format(r.peak_C, 'temperature', system)),
          h('td', {className: 'n'}, format(r.rating_C, 'temperature', system)),
          h('td', {className: `n ${r.margin_C != null && r.margin_C < 0 ? 'fail' : ''}`}, format(r.margin_C, 'temperature_difference', system)),
          h('td', null, r.ok == null ? '' : r.ok ? 'Pass' : 'Fail')))))),
      h('p', {className: 'small muted'}, 'Margin is the rating minus the peak.'),
      payload.figures.tools ? h(Figure, {svg: payload.figures.tools}) : null),
    h(Section, {id: 'results-stability', title: 'Stability at the target', detail: 'cases', onDetails},
      h('div', {className: 'table-scroll'}, h('table', {className: 'result-table'},
        h('thead', null, h('tr', null, h('th', null, 'Stress and strength case'), STATES.map(([, label]) => h('th', {key: label}, label)), h('th', null, 'Overall'))),
        h('tbody', null, s.stability.cases.map((c, i) => h('tr', {key: i},
          h('td', null, c.case),
          STATES.map(([key]) => h('td', {key, className: verdictClass(c.states[key] ?? null)}, c.states[key] ?? '-')),
          h('td', {className: verdictClass(c.verdict)}, h('strong', null, c.verdict))))))),
      h('p', null, `Deciding case: ${s.stability.deciding_case ?? 'not returned'}.`)),
    h(Section, {id: 'results-pressure', title: 'Pressure', detail: 'overview', onDetails},
      h('dl', {className: 'result-list'},
        h('dt', null, 'Standpipe pressure while drilling'), h('dd', null, format(s.pressure.standpipe_Pa, 'pressure', system)),
        h('dt', null, 'Standpipe pressure on the trip'), h('dd', null, s.pressure.trip_standpipe_Pa == null ? 'Not returned' : format(s.pressure.trip_standpipe_Pa, 'pressure', system)),
        h('dt', null, "Within the rig's pumps"), h('dd', null, s.pressure.within_rig_pumps == null ? 'Not returned' : s.pressure.within_rig_pumps ? 'Yes' : 'No'),
        h('dt', null, 'Pump pressure limit'), h('dd', null, 'Not returned by the model'))),
    h(Section, {id: 'results-temperature', title: 'Circulating temperatures of the selected programme', detail: 'profile', onDetails},
      h('p', null, `Pipe ${s.programme.pipe ?? 'not returned'}, flow ${format(s.programme.mass_flow_kg_s, 'mass_flow', system)}, ${s.programme.water ? 'water' : `mud at ${format(s.programme.fluid_kg_m3, 'density', system)}`}. Bit ${format(s.programme.bit_C, 'temperature', system)}, return ${format(s.programme.return_C, 'temperature', system)}.`),
      payload.figures.temperature ? h(Figure, {svg: payload.figures.temperature}) : null));
}
