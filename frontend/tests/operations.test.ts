import {test} from 'node:test';
import assert from 'node:assert/strict';
import {initial, programmeHeading, statedCount, type FieldRow} from '../src/operations.ts';

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

test('the programme heading names the model only when nothing is stated', () => {
  assert.equal(programmeHeading(false, 0), 'Programme chosen by the model');
  assert.equal(programmeHeading(false, statedCount({tool_limit_C: 175, sources: {}})), 'Programme: stated values and model choices');
  assert.equal(programmeHeading(true, 0), 'Programme: stated values and model choices');
  assert.equal(statedCount({sources: {}}), 0);
});
