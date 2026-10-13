import assert from 'node:assert/strict';
import test from 'node:test';
import {qualityPresets,normalizeQuality} from '../src/reference/render-quality.ts';
test('selecting a named quality applies that preset without silently restoring expensive defaults',()=>{
  for(const quality of Object.values(qualityPresets))assert.deepEqual(normalizeQuality(quality),quality);
});
