import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {FlagsPanel, ResultsPage, type ResultsPayload} from '../src/ResultsPage.ts';

const fixture = (name: string) => JSON.parse(readFileSync(new URL(`../../tests/fixtures/results/${name}`, import.meta.url), 'utf8'));
const render = (name: string, system: 'SI' | 'oilfield' = 'SI', prefix = 'assessment') => {
  const payload = fixture(`${prefix}-${name}.results.json`) as ResultsPayload;
  return {payload, html: renderToStaticMarkup(createElement(ResultsPage, {payload, system, onDetails: () => undefined, reportHref: '/report'}))};
};

for (const [name, status] of [['go', 'GO'], ['conditional', 'CONDITIONAL'], ['no-go', 'NO-GO']] as const) {
  test(`results page renders a saved ${status} assessment`, () => {
    const {payload, html} = render(name);
    const manifest = fixture(`assessment-${name}.json`).manifest;
    assert.equal(manifest.assessment.status.label, status);
    assert.match(html, new RegExp(`data-status="${status}"`));
    assert.match(html, new RegExp(`<div class="verdict-badge">${status}</div>`));
    assert.ok(html.includes(escape(manifest.assessment.reading.controlling_constraint)));
    for (const heading of ['Mud-weight window', 'Tools and equipment', 'Stability at the target', 'Pumps', 'Stated values', 'Field-tested range and qualifications']) assert.ok(html.includes(`>${heading}</h2>`), heading);
    for (const reason of manifest.assessment.status.reasons) assert.ok(html.includes(escape(reason)), reason);
    for (const flag of payload.flags!.outside) assert.ok(html.includes(escape(flag.message)));
    assert.equal((html.match(/Show details/g) || []).length, 6);
    assert.ok(html.includes('<svg'));
    // A result from BoreTHM 1.8.0 has no widths, margins or pump limit; nothing is computed in their place.
    assert.ok(html.includes('Not returned by the installed BoreTHM'));
    assert.ok(html.includes('it does not accept stated ones'));
  });
}

const escapeText = (text: string) => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#x27;');

for (const [name, status] of [['go', 'GO'], ['conditional', 'CONDITIONAL'], ['stated', 'NO-GO']] as const) {
  test(`results page shows the model's widths, margins and pumps for a ${name} assessment`, () => {
    const {html} = render(name, 'SI', 'interface');
    const report = fixture(`interface-${name}.json`).manifest.assessment;
    assert.equal(report.status.label, status);
    assert.ok(!html.includes('Not returned by the installed BoreTHM'));
    for (const key of ['drilling_fluid', 'trip_fluid']) {
      const width = report.window_width[key].width_kg_m3;
      if (typeof width === 'number') assert.ok(html.includes(escapeText(`${Math.round(width).toLocaleString('en-GB')} kg/m³`)), key);
    }
    const limit = report.pump_check.limit_Pa / 1e6;
    assert.ok(html.includes(`Pump pressure limit ${limit.toLocaleString('en-GB', {minimumFractionDigits: 2, maximumFractionDigits: 2})} MPa; ${report.pump_check.pumps} rig pumps.`));
    const margin = report.tool_check.run_in_unstaged.margin_C;
    assert.ok(html.includes(`${margin.toLocaleString('en-GB', {minimumFractionDigits: 1, maximumFractionDigits: 1})} °C`), 'unstaged margin');
    assert.ok(html.includes('Tool on run-in without staging<small> (for comparison; does not set the status)</small>'));
    assert.equal((html.match(/for comparison; does not set the status/g) || []).length, report.tool_check.run_in_unstaged.sets_status === false ? 1 : 0);
    const litres = report.pump_check.drilling.flow_L_s;
    assert.ok(html.includes(`${litres.toLocaleString('en-GB', {minimumFractionDigits: 1, maximumFractionDigits: 1})} L/s`), 'pump flow from the model in L/s');
    assert.ok(html.includes('Mud-weight window against depth'));
    assert.ok(html.includes('<title>'), 'hover text for each bound');
    assert.ok(html.includes('Common windows at each evaluated depth'));
    assert.ok(html.includes('breakout width') || html.includes('pore pressure'));
  });
}

test('stated values and the constraint they set are shown', () => {
  const {html} = render('stated', 'oilfield', 'interface');
  assert.ok(html.includes('Hydraulic programme: a stated custom string.'));
  assert.ok(html.includes('<td>Tool temperature limit</td><td>194.0 °F</td><td>assumed</td>'));
  assert.ok(html.includes('<td>Flow, as a mass flow</td><td>44.1 lb/s</td>'));
  assert.ok(html.includes('A stated value is used in the check behind the controlling constraint. In the model&#x27;s wording: Stated operations used in this check: tool limit 90 C; mass flow 20 kg/s.'));
  const go = render('go', 'SI', 'interface').html;
  assert.ok(go.includes('No operating value is stated; the model chose each one.'));
  assert.ok(go.includes('No stated value is used in the check behind the controlling constraint.'));
});

test('qualifications show plain sentences and the verdict text is labelled as the model\'s', () => {
  const {payload, html} = render('go');
  const list = html.slice(html.indexOf('qualification-list'), html.indexOf('</ul>', html.indexOf('qualification-list')));
  for (const q of payload.qualifications) {
    assert.ok(q.plain && list.includes(escape(q.plain)), q.code);
    assert.ok(!list.includes(`>${q.code}<`), q.code);
  }
  assert.ok(html.includes('Controlling constraint, in the model&#x27;s wording: '));
  assert.ok(html.includes('The model&#x27;s summary, in its own wording and units: '));
});

test('closed window and failing case are marked in the NO-GO result', () => {
  const {html} = render('no-go');
  assert.ok(html.includes('class="fail">Closed</td>'));
  assert.ok(html.includes('no fluid suits the scenario'));
});

test('oilfield units change the tables and keep the verdict', () => {
  const si = render('conditional', 'SI').html, oilfield = render('conditional', 'oilfield').html;
  assert.ok(si.includes(' kg/m³') && !si.includes(' ppg'));
  assert.ok(oilfield.includes(' ppg') && oilfield.includes(' °F') && oilfield.includes(' psi'));
  assert.ok(oilfield.includes('<div class="verdict-badge">CONDITIONAL</div>'));
});

test('tested-range panel for a circulation run', () => {
  const payload = fixture('circulation.results.json') as ResultsPayload;
  const html = renderToStaticMarkup(createElement(FlagsPanel, {flags: payload.flags, qualifications: payload.qualifications}));
  assert.ok(html.includes('Inputs outside the field-tested range'));
  assert.ok(html.includes('Formation temperature 120.0 °C is below the lowest field-tested formation temperature'));
});

function escape(text: string) {
  return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#x27;');
}
