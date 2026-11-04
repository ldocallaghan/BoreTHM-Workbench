import test from 'node:test';
import assert from 'node:assert/strict';
import {savedIndex,eventIndex,stateSegments} from '../src/historyState.ts';

test('seek uses saved time, explicit right boundary and bounded interval',()=>{
  const times=[0,1,3,3,7,12];
  assert.equal(savedIndex(times,-1),0);
  assert.equal(savedIndex(times,2),1);
  assert.equal(savedIndex(times,3),3);
  assert.equal(savedIndex(times,999),5);
});
test('event stepping exposes both boundary sides without skipping them',()=>{
  const events=[{left_index:null,right_index:0},{left_index:2,right_index:3}];
  assert.equal(eventIndex(events,1,1),2);
  assert.equal(eventIndex(events,2,1),3);
  assert.equal(eventIndex(events,3,-1),2);
  assert.equal(eventIndex(events,0,-1),0);
});
test('time traces break across changes of state even when times touch',()=>{
  assert.deepEqual(stateSegments([0,3,3,7],[50,55,80,90],['a','a','b','b']),[
    {x:[0,3],y:[50,55]},{x:[3,7],y:[80,90]}
  ]);
});

test('continuation seeks preserve absolute physical time and clamp to the retained interval',()=>{
  const times=[2400,2401,2700,2700,2710,3600];
  assert.equal(savedIndex(times,0),0);
  assert.equal(savedIndex(times,2400),0);
  assert.equal(savedIndex(times,2700),3);
  assert.equal(savedIndex(times,9999),5);
  assert.deepEqual(stateSegments(times,[1,2,3,4,5,6],['hold','hold','hold','restart','restart','restart'])[0].x,[2400,2401,2700]);
});
