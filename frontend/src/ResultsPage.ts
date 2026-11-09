// Results page for a completed assessment, and the tested-range panel shared
// with circulation runs. Written with createElement so the Node test runner
// can render it without a build step. Every number comes from the results
// payload, which holds model values, and is shown through a unit conversion.
import {createElement as h, Fragment, type ReactNode} from 'react';
import {format, type UnitSystem} from './units.ts';

type Bound = {state: string; case: string; md_m: number | null} | null;
type Fluid = {label: string; open: boolean | null; lower_kg_m3: number | null; upper_kg_m3: number | null; upper_unbounded?: boolean; width_kg_m3: number | null; lower_by: Bound; upper_by: Bound; chosen_kg_m3: number | null; note: string | null};
type ToolRow = {item: string; state?: string; peak_C: number | null; rating_C: number | null; margin_C: number | null; ok?: boolean | null; sets_status?: boolean | null; minutes_above_rating?: number | null};
type By = {state: string; case: string; mechanism: string} | null;
type Common = {label: string; lower_kg_m3: (number | null)[]; upper_kg_m3: (number | null)[]; width_kg_m3: (number | null)[]; lower_by: By[]; upper_by: By[]};
type Profile = {depths_m: number[]; common: Record<string, Common>; states: {state: string; case: string; lower_kg_m3: (number | null)[]; lower_mechanism: (string | null)[]; upper_kg_m3: (number | null)[]; upper_mechanism: (string | null)[]}[]};
type Pumping = {standpipe_Pa: number | null; margin_Pa: number | null; ok: boolean | null; flow_m3_s: number | null; rig_capacity_m3_s: number | null; within_rig: boolean | null; separate_circulation?: boolean};
type Stated = {returned: boolean; items: {name: string; label: string; quantity: string | null; value: unknown; unit: string | null; basis: string | null; source: string | null}[]; programme: string | null; sets_controlling_constraint: boolean; controlling_sentence: string | null};
export type AssessmentSummary = {
  verdict: {status: string | null; reasons: string[]; controlling_constraint: string | null; reading: Record<string, string>};
  site: {name?: string; target_depth_m?: number; target_T_C?: number; tier?: string};
  programme: {pipe: string | null; mass_flow_kg_s: number | null; flow?: number | null; flow_unit?: string | null; fluid_kg_m3: number | null; water: boolean; backpressure_Pa: number | null; bit_C: number | null; return_C: number | null; rop_m_h?: number | null};
  windows: {fluids: Record<string, Fluid>; by_case: {state: string; case: string; lower_kg_m3: number | null; lower_md_m: number | null; upper_kg_m3: number | null; upper_md_m: number | null}[]; evaluated_depths_m: number[]; profiles_returned: boolean; widths_returned?: boolean; profile?: Profile | null};
  tools: {rating_C: number | null; status: string | null; rows: ToolRow[]; margins_returned?: boolean; coating: {pipe: string | null; status: string | null; rows: ToolRow[]; margins_returned?: boolean}};
  stability: {verdicts: Record<string, string>; deciding_case: string | null; cases: {case: string; verdict: string; states: Record<string, string>}[]; site_verdict: string | null};
  pressure: {standpipe_Pa: number | null; trip_standpipe_Pa: number | null; within_rig_pumps: boolean | null; pumps_ok: boolean | null; pump_limit_Pa: number | null; pumps?: number | null; drilling?: Pumping | null; trip?: Pumping | null; returned?: boolean};
  stated?: Stated;
  input_warnings: string[];
  not_returned?: string;
};
export type Flags = {outside: {input: string; message: string}[]; notes: {input: string; message: string}[]} | null;
export type Qualification = {code: string; message: string; plain?: string; path?: string};
export type ResultsPayload = {mode: string; units: UnitSystem; run_id: string; title: string; flags: Flags; qualifications: Qualification[]; summary: AssessmentSummary | Record<string, unknown>; figures: Record<string, string>};
export type DetailSection = 'windows' | 'cases' | 'equipment' | 'profile' | 'overview' | 'qualifications';
const NOT_RETURNED = 'Not returned by the installed BoreTHM';

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

function by(b: By) {
  return b ? `${b.state}, ${b.case}: ${b.mechanism}` : '-';
}

function width(f: Fluid, system: UnitSystem) {
  if (f.width_kg_m3 != null) return format(f.width_kg_m3, 'density', system);
  return f.upper_unbounded ? 'No upper bound' : NOT_RETURNED;
}

function stated(item: Stated['items'][number], system: UnitSystem) {
  const v = item.value;
  const q = item.quantity as Parameters<typeof format>[1] | null;
  if (q && typeof v === 'number') return format(v, q, system);
  if (q && Array.isArray(v)) return v.map(x => format(x as number, q, system)).join(', ');
  if (q && v && typeof v === 'object' && !Array.isArray(v)) return Object.entries(v as Record<string, number>).map(([k, x]) => `${k} ${format(x, q, system)}`).join('; ') || 'No pipe rated';
  if (Array.isArray(v)) return `${v.join(', ')}${item.unit ? ' ' + item.unit : ''}`;
  return `${String(v)}${item.unit ? ' ' + item.unit : ''}`;
}

function WindowTable({profile, system}: {profile: Profile; system: UnitSystem}) {
  return h('details', {className: 'window-depth-table'}, h('summary', null, 'Common windows at each evaluated depth, with the state, case and mechanism that set each bound'),
    h('div', {className: 'table-scroll'}, h('table', {className: 'result-table'},
      h('thead', null, h('tr', null, ['Measured depth', 'Drilling fluid lower', 'Set by', 'Drilling fluid upper', 'Set by', 'Trip fluid lower', 'Set by', 'Trip fluid upper', 'Set by'].map((t, i) => h('th', {key: i}, t)))),
      h('tbody', null, profile.depths_m.map((d, i) => h('tr', {key: i},
        h('td', {className: 'n'}, format(d, 'length', system)),
        ...(['drilling_fluid', 'trip_fluid'] as const).flatMap(k => {
          const c = profile.common[k];
          return [h('td', {key: k + 'l', className: 'n'}, format(c.lower_kg_m3[i], 'density', system)), h('td', {key: k + 'lb'}, by(c.lower_by[i])),
            h('td', {key: k + 'u', className: 'n'}, format(c.upper_kg_m3[i], 'density', system)), h('td', {key: k + 'ub'}, by(c.upper_by[i]))];
        })))))),
    h('p', {className: 'small muted'}, "A lower bound below water's density means that water keeps the breakout within its limit at that depth. Hover over the chart for each state and case."));
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
      qualifications.length ? h('ul', {className: 'qualification-list'}, qualifications.map((q, i) => h('li', {key: i, title: q.code}, q.plain || q.message))) : h('p', null, 'None.'),
      qualifications.length ? h('p', {className: 'small muted'}, 'The qualification codes are listed in the run details.') : null));
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
        h('p', {className: 'verdict-constraint'}, h('strong', null, "Controlling constraint, in the model's wording: "), v.controlling_constraint || 'Not returned'),
        v.reasons.length ? h('ul', {className: 'verdict-reasons'}, v.reasons.map((r, i) => h('li', {key: i}, r))) : null,
        h('p', {className: 'verdict-reading'}, h('span', {className: 'verdict-label'}, "The model's summary, in its own wording and units: "), [v.reading.calculation, v.reading.whole_programme].filter(Boolean).join(' ')),
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
          h('td', {className: 'n'}, width(f, system)),
          h('td', null, bound(f.lower_by, system)),
          h('td', null, bound(f.upper_by, system)),
          h('td', {className: 'n'}, f.chosen_kg_m3 == null ? (f.note || 'None') : format(f.chosen_kg_m3, 'density', system))))))),
      h('p', {className: 'small muted'}, s.windows.profiles_returned ? 'Surface densities at 20 °C. Widths and the bounds against depth are the model\'s results.' : 'Surface densities at 20 °C. The installed BoreTHM returns each bound with its binding depth; it does not return the widths or the bounds against depth, so the chart shows each state and case.'),
      payload.figures.window_depth ? h(Figure, {svg: payload.figures.window_depth, caption: 'Thin lines: each state and case. Bold: the common windows. Dashed vertical lines: the fluids chosen. Hover over a point for its state, case and mechanism.'}) : payload.figures.window ? h(Figure, {svg: payload.figures.window}) : null,
      s.windows.profile ? h(WindowTable, {profile: s.windows.profile, system}) : null),
    h(Section, {id: 'results-tools', title: 'Tools and equipment', detail: 'equipment', onDetails},
      h('p', null, `Tool check: ${s.tools.status ?? 'not returned'}. Coating (${s.tools.coating.pipe ?? 'none'}): ${s.tools.coating.status ?? 'not returned'}.`),
      h('div', {className: 'table-scroll'}, h('table', {className: 'result-table'},
        h('thead', null, h('tr', null, ['Item', 'Peak', 'Rating', 'Margin', 'Check'].map(t => h('th', {key: t}, t)))),
        h('tbody', null, toolRows.map(r => h('tr', {key: r.item, className: r.sets_status === false ? 'comparison' : ''},
          h('td', null, r.item, r.sets_status === false ? h('small', null, ' (for comparison; does not set the status)') : null),
          h('td', {className: 'n'}, format(r.peak_C, 'temperature', system)),
          h('td', {className: 'n'}, format(r.rating_C, 'temperature', system)),
          h('td', {className: `n ${r.ok === false ? 'fail' : ''}`}, r.margin_C == null ? (r.rating_C == null ? 'No rating' : NOT_RETURNED) : format(r.margin_C, 'temperature_difference', system)),
          h('td', {className: r.ok === false ? 'fail' : r.ok ? 'ok' : ''}, r.ok == null ? '' : r.ok ? 'Pass' : 'Fail')))))),
      h('p', {className: 'small muted'}, s.tools.margins_returned ? "Limits, margins and pass flags are the model's results. Rows the model marks for comparison do not set the tool or coating status." : 'The installed BoreTHM does not return margins or a pass flag for the unstaged run-in.'),
      payload.figures.tools ? h(Figure, {svg: payload.figures.tools}) : null),
    h(Section, {id: 'results-stability', title: 'Stability at the target', detail: 'cases', onDetails},
      h('div', {className: 'table-scroll'}, h('table', {className: 'result-table'},
        h('thead', null, h('tr', null, h('th', null, 'Stress and strength case'), STATES.map(([, label]) => h('th', {key: label}, label)), h('th', null, 'Overall'))),
        h('tbody', null, s.stability.cases.map((c, i) => h('tr', {key: i},
          h('td', null, c.case),
          STATES.map(([key]) => h('td', {key, className: verdictClass(c.states[key] ?? null)}, c.states[key] ?? '-')),
          h('td', {className: verdictClass(c.verdict)}, h('strong', null, c.verdict))))))),
      h('p', null, `Deciding case: ${s.stability.deciding_case ?? 'not returned'}.`)),
    h(Section, {id: 'results-pressure', title: 'Pumps', detail: 'overview', onDetails},
      s.pressure.returned && s.pressure.drilling && s.pressure.trip ? h(Fragment, null,
        h('p', null, `Pump pressure limit ${format(s.pressure.pump_limit_Pa, 'pressure', system)}; ${s.pressure.pumps} rig pumps.`),
        h('div', {className: 'table-scroll'}, h('table', {className: 'result-table'},
          h('thead', null, h('tr', null, ['State', 'Standpipe pressure', 'Limit', 'Margin', 'Check', 'Flow', "Rig's capacity at that pressure"].map(t => h('th', {key: t}, t)))),
          h('tbody', null, ([['Drilling', s.pressure.drilling], ['Trip', s.pressure.trip]] as [string, Pumping][]).map(([label, p]) => h('tr', {key: label},
            h('td', null, label + (label === 'Trip' && p.separate_circulation ? ' (its own circulation)' : '')),
            h('td', {className: 'n'}, format(p.standpipe_Pa, 'pressure', system)),
            h('td', {className: 'n'}, format(s.pressure.pump_limit_Pa, 'pressure', system)),
            h('td', {className: `n ${p.ok === false ? 'fail' : ''}`}, format(p.margin_Pa, 'pressure', system)),
            h('td', {className: p.ok ? 'ok' : 'fail'}, p.ok ? 'Pass' : 'Fail'),
            h('td', {className: 'n'}, format(p.flow_m3_s, 'volume_flow', system)),
            h('td', {className: `n ${p.within_rig === false ? 'fail' : ''}`}, format(p.rig_capacity_m3_s, 'volume_flow', system))))))))
      : h('dl', {className: 'result-list'},
        h('dt', null, 'Standpipe pressure while drilling'), h('dd', null, format(s.pressure.standpipe_Pa, 'pressure', system)),
        h('dt', null, 'Standpipe pressure on the trip'), h('dd', null, s.pressure.trip_standpipe_Pa == null ? 'Not returned' : format(s.pressure.trip_standpipe_Pa, 'pressure', system)),
        h('dt', null, "Within the rig's pumps"), h('dd', null, s.pressure.within_rig_pumps == null ? 'Not returned' : s.pressure.within_rig_pumps ? 'Yes' : 'No'),
        h('dt', null, 'Pump pressure limit'), h('dd', null, NOT_RETURNED))),
    s.stated ? h(Section, {id: 'results-stated', title: 'Stated values'},
      !s.stated.returned ? h('p', null, 'The installed BoreTHM chose every operating value; it does not accept stated ones.') : h(Fragment, null,
        h('p', null, `Hydraulic programme: ${s.stated.programme === 'custom' ? 'a stated custom string' : s.stated.programme === 'reference' ? "the model's reference programme with stated options" : "the site file's programme, or the model's reference programme"}.`),
        s.stated.items.length ? h('div', {className: 'table-scroll'}, h('table', {className: 'result-table'},
          h('thead', null, h('tr', null, ['Operating choice', 'Stated value', 'Basis', 'Source'].map(t => h('th', {key: t}, t)))),
          h('tbody', null, s.stated.items.map(i => h('tr', {key: i.name}, h('td', null, i.label), h('td', null, stated(i, system)), h('td', null, i.basis ?? ''), h('td', null, i.source ?? '')))))) : h('p', null, 'No operating value is stated; the model chose each one.'),
        h('p', {className: s.stated.sets_controlling_constraint ? 'stated-constraint' : ''}, s.stated.sets_controlling_constraint ? `A stated value is used in the check behind the controlling constraint. In the model's wording: ${s.stated.controlling_sentence}` : 'No stated value is used in the check behind the controlling constraint.'))) : null,
    h(Section, {id: 'results-temperature', title: 'Circulating temperatures of the selected programme', detail: 'profile', onDetails},
      h('p', null, `Pipe ${s.programme.pipe ?? 'not returned'}, flow ${format(s.programme.mass_flow_kg_s, 'mass_flow', system)}, ${s.programme.water ? 'water' : `mud at ${format(s.programme.fluid_kg_m3, 'density', system)}`}. Bit ${format(s.programme.bit_C, 'temperature', system)}, return ${format(s.programme.return_C, 'temperature', system)}.`),
      payload.figures.temperature ? h(Figure, {svg: payload.figures.temperature}) : null));
}
