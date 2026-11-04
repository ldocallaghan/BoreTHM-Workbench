import {strict as assert} from 'node:assert';
import {test} from 'node:test';
import {formatApiError} from '../src/apiErrors.ts';
test('ZIP validation errors show the actionable message and field',()=>{
  assert.equal(formatApiError({detail:[{path:'/file',code:'ARTIFACT_INVALID',message:'File is not a zip file'}]}),'/file: File is not a zip file');
});
test('JSON validation locations and string errors are readable',()=>{
  assert.equal(formatApiError({detail:[{loc:['body','definition','flow'],msg:'A positive flow is required'}]}),'/definition/flow: A positive flow is required');
  assert.equal(formatApiError({detail:'Select a valid file'}),'Select a valid file');
});
