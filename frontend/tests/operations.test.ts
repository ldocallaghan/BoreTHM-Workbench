import {test} from 'node:test';
import assert from 'node:assert/strict';
import {initial, type FieldRow} from '../src/operations.ts';

const row = (name: string, value: unknown, restates: boolean): FieldRow => ({
  name, label: name, quantity: 'hours', unit: 'h', rule: 'the rule', source: 'the source', default: value, restates_default: restates,
});

test('a default that restates the default result is the starting value', () => {
  assert.equal(initial(row('trip_hours', 17.4586, true)), 17.4586);
});

test('a default that does not restate the result leaves the value empty', () => {
  assert.equal(initial(row('stage_spacing_m', 144.88, false)), null);
  assert.equal(initial(row('mass_flows_kg_s', null, false)), null);
});
