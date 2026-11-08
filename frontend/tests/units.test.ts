import {test} from 'node:test';
import assert from 'node:assert/strict';
import {UNIT_TABLE, format, fromSI, inputValue, number, roundSignificant, symbol, toSI, type Quantity, type UnitSystem} from '../src/units.ts';

const quantities = Object.keys(UNIT_TABLE.quantities) as Quantity[];
const systems: UnitSystem[] = ['SI', 'oilfield'];

test('every exact oilfield factor', () => {
  const factor = (q: Quantity) => UNIT_TABLE.quantities[q].units.oilfield.factor;
  assert.equal(factor('length'), 0.3048);
  assert.equal(factor('diameter'), 0.0254);
  assert.equal(factor('mass_flow'), 0.45359237);
  assert.equal(factor('pressure'), 6894.757293168);
  assert.equal(factor('density'), 119.826427);
  assert.equal(factor('volume_flow'), 3.785411784 * 0.001 / 60);
  assert.equal(factor('pressure_gradient'), 6894.757293168 / 0.3048);
  assert.equal(factor('specific_gravity'), 1000);
  assert.equal(toSI(32, 'temperature', 'oilfield'), 0);
  assert.equal(toSI(212, 'temperature', 'oilfield'), 100);
  assert.equal(toSI(9, 'temperature_difference', 'oilfield'), 5);
  assert.equal(symbol('diameter', 'SI'), 'mm');
  assert.equal(symbol('temperature_gradient', 'oilfield'), '°F/100 ft');
});

test('round trips to 12 significant figures in both systems', () => {
  const values = [0, 1e-4, 0.0254, 1, 3.5, 999, 3048, 2.5e6, 4.5e7, -12.5];
  for (const q of quantities) for (const s of systems) for (const v of values) {
    const back = toSI(fromSI(v, q, s), q, s);
    assert.equal(back.toExponential(11), roundSignificant(v).toExponential(11), `${q} ${s} ${v}`);
  }
});

test('oilfield entry gives the same SI scenario values as SI entry', () => {
  const cases: [Quantity, number, number][] = [
    ['length', 10000, 3048], ['diameter', 8.5, 215.9], ['temperature', 212, 100], ['temperature', 104, 40],
    ['density', 10, 1198.26427], ['volume_flow', 100, 6.30901964], ['mass_flow', 2, 0.90718474], ['pressure', 1000, 6.894757293168],
  ];
  for (const [q, oilfield, si] of cases) assert.equal(toSI(oilfield, q, 'oilfield').toExponential(11), toSI(si, q, 'SI').toExponential(11), q);
});

test('an unedited value keeps its SI number, so a scenario hash cannot change', () => {
  // Display values are only for showing; nothing is written back unless edited.
  const stored = [3000, 0.11, 40, 1e6, 28000];
  const kinds: Quantity[] = ['length', 'diameter', 'temperature', 'pressure', 'power'];
  const scenario = JSON.stringify(stored);
  for (const s of systems) stored.forEach((v, i) => { assert.ok(Number.isFinite(inputValue(v, kinds[i], s))); });
  assert.equal(JSON.stringify(stored), scenario);
  // Re-entering the shown oilfield value reproduces the SI value to 12 figures.
  for (const [i, v] of stored.entries()) {
    const shown = fromSI(v, kinds[i], 'oilfield');
    assert.equal(toSI(shown, kinds[i], 'oilfield').toExponential(11), v.toExponential(11));
  }
});

test('formatting in each system', () => {
  assert.equal(format(3048, 'length', 'oilfield'), '10,000 ft');
  assert.equal(format(100, 'temperature', 'oilfield'), '212.0 °F');
  assert.equal(format(1e6, 'pressure', 'SI'), '1.00 MPa');
  assert.equal(number(null, 'pressure', 'SI'), 'Unavailable');
});
