import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {FlagsPanel, ResultsPage, type ResultsPayload} from '../src/ResultsPage.ts';

const fixture = (name: string) => JSON.parse(readFileSync(new URL(`../../tests/fixtures/results/${name}`, import.meta.url), 'utf8'));
const render = (name: string, system: 'SI' | 'oilfield' = 'SI') => {
  const payload = fixture(`assessment-${name}.results.json`) as ResultsPayload;
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
    for (const heading of ['Mud-weight window', 'Tools and equipment', 'Stability at the target', 'Pressure', 'Field-tested range and qualifications']) assert.ok(html.includes(`>${heading}</h2>`), heading);
    for (const reason of manifest.assessment.status.reasons) assert.ok(html.includes(escape(reason)), reason);
    for (const flag of payload.flags!.outside) assert.ok(html.includes(escape(flag.message)));
    assert.equal((html.match(/Show details/g) || []).length, 6);
    assert.ok(html.includes('<svg'));
    assert.ok(html.includes('Not returned by the model'));
  });
}

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
