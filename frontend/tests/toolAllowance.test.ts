import test from 'node:test';
import assert from 'node:assert/strict';
import {allowanceFor} from '../src/toolAllowance.ts';

test('nearest collar band uses the smaller band at equal distances',()=>{
  const rows=[4.75,6.75,8].map(band_in=>({tool_class:'mwd_pulser',band_in,dp_ref_psi:10,flow_ref_gpm:250,exponent:2,sources:[]}));
  assert.equal(allowanceFor(rows,'mwd_pulser',5.75)?.band_in,4.75);
  assert.equal(allowanceFor(rows,'mwd_pulser',7.8)?.band_in,8);
  assert.equal(allowanceFor(rows,'mud_motor',6.75),undefined);
});
