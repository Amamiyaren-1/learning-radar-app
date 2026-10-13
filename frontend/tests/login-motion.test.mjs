import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loginContourState,loginRibPose,loginAnnotationState,LOGIN_LOOP_SECONDS} from '../src/login-loop-motion.ts';
import {bootLogoTrack} from '../src/reference/boot-logo-tracks.ts';
test('login reuses the actual intro draw and moving-cut endpoint states',()=>{
  for(const [t,f] of [[0,229],[.9,246.5],[1.8,420],[3.3,453],[4.8,486]]){
    const state=loginContourState(t),source=bootLogoTrack(f);
    for(const key of ['start','length','strokeWidth'])assert.ok(Math.abs(state[key]-source[key])<1e-9,key);
  }
});
test('all rib positions and orientations close smoothly at the loop seam',()=>{
  for(const t of [1.8,4.8,LOGIN_LOOP_SECONDS]){
    const h=.00001,a=loginContourState(t-h),b=loginContourState(t),c=loginContourState(t+h);
    for(let i=0;i<88;i++){
      const pose=s=>loginRibPose(s.start+s.length*i/88,s.strokeWidth).matrix;
      const pa=pose(a),pb=pose(b),pc=pose(c);
      for(let k=0;k<6;k++){
        assert.ok(Math.abs(pa[k]-pc[k])<.0001,'position/orientation');
        assert.ok(Math.abs((pb[k]-pa[k])/h-(pc[k]-pb[k])/h)<.01,'velocity');
      }
    }
  }
});
test('annotations reveal point-line-text and retract text-line-point on the contour clock',()=>{
  for(let i=0;i<3;i++){
    let prior={point:0,line:0,text:0};
    for(let t=0;t<.8;t+=.005){
      const s=loginAnnotationState(t,loginContourState(t),i);
      assert.ok(s.point>=s.line&&s.line>=s.text);
      for(const key of ['point','line','text'])assert.ok(s[key]>=prior[key]);
      prior=s;
    }
    prior={point:1,line:1,text:1};
    for(let t=5.8;t<6.8;t+=.005){
      const s=loginAnnotationState(t,loginContourState(t),i);
      assert.ok(s.point>=s.line&&s.line>=s.text);
      for(const key of ['point','line','text'])assert.ok(s[key]<=prior[key]);
      prior=s;
    }
  }
});
test('annotations are absent at the contour seam and complete in static fallback',()=>{
  for(let i=0;i<3;i++){
    for(const t of [0,.1,6.34,6.6,6.7999,6.8,13.6])assert.deepEqual(loginAnnotationState(t,loginContourState(t),i),{point:0,line:0,text:0});
    assert.deepEqual(loginAnnotationState(6.8,{phase:'static',length:1,strokeWidth:26},i),{point:1,line:1,text:1});
    assert.deepEqual(loginAnnotationState(2,loginContourState(2),i),{point:1,line:1,text:1});
  }
});
