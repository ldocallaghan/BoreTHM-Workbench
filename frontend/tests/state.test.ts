import {test} from 'node:test';
import assert from 'node:assert/strict';
import {adjacentComponents,completionMatches,fingerprint,sample} from '../src/state.ts';

test('reversed completion cannot replace the newer selected run or edited draft',()=>{
  const original={flow:30,numerics:{profile:'reference-v1'}},next={flow:35,numerics:{profile:'reference-v1'}};
  assert.equal(completionMatches('run-new','run-old',fingerprint(original),next),false);
  assert.equal(completionMatches('run-new','run-old',fingerprint(original),original),false);
  assert.equal(completionMatches('run-new','run-new',fingerprint(next),original),false);
  assert.equal(completionMatches('run-new','run-new',fingerprint(next),{numerics:{profile:'reference-v1'},flow:35}),true);
});

test('out-of-domain comparison picking is unavailable, never snapped to the bit',()=>{
  assert.equal(sample([0,100],[20,80],150),undefined);
  assert.equal(sample([0,200],[20,100],150),80);
  assert.equal(sample([0,100],[20,80],-1),undefined);
  assert.equal(sample([0,100],[20,80],50),50);
});

test('component boundaries identify both sides without inventing a geometry ramp',()=>{
  const parts=[{id:'upper',length_m:100},{id:'lower',length_m:20}];
  assert.deepEqual(adjacentComponents(parts,100),['upper','lower']);
  assert.deepEqual(adjacentComponents(parts,100.001),['lower']);
  assert.deepEqual(adjacentComponents(parts,121),[]);
});
