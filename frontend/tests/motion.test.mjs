import {test} from 'node:test';
import assert from 'node:assert/strict';
import {introFraming,introReturn,INTRO_RETURN_START,INTRO_RETURN_END,extraction,cinematicSelectedField} from '../src/reference/motion.ts';
test('27.3 camera anchor and framing preserve position and velocity',()=>{
  const h=.0001,a=introFraming(27.3-h),b=introFraming(27.3),c=introFraming(27.3+h);
  for(const key of ['x','y','anchor']){
    assert.ok(Math.abs(a[key]-c[key])<.00001,key);
    assert.ok(Math.abs((b[key]-a[key])/h-(c[key]-b[key])/h)<.0001,key+' velocity');
  }
  assert.equal(b.anchor,1);
});
test('cinematic world altitude rises without the shoulder-ring rebound',()=>{
  let prior=cinematicSelectedField(25.58)+extraction(25.58);
  for(let t=25.58;t<=28.85;t+=.001){const next=cinematicSelectedField(t)+extraction(t);assert.ok(next>=prior-1e-9);prior=next;}
});
test('intro return reaches ordinary lift with zero endpoint velocity',()=>{
  const lift=t=>extraction(Math.min(34.8,t))*(1-introReturn(t))+.4*introReturn(t);
  assert.equal(lift(INTRO_RETURN_START),3.35);assert.equal(lift(INTRO_RETURN_END),.4);
  let prior=3.35;for(let t=INTRO_RETURN_START;t<INTRO_RETURN_END;t+=.01){assert.ok(lift(t)<=prior+1e-10);prior=lift(t);}
  for(const t of [INTRO_RETURN_START,INTRO_RETURN_END])assert.ok(Math.abs((lift(t+.0001)-lift(t-.0001))/.0002)<.00001);
});
