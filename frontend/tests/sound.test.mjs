import assert from 'node:assert/strict';
import test from 'node:test';
import {MusicPlayback} from '../src/music-playback.ts';
import {soundPreferences} from '../src/sound-preferences.ts';
const media=()=>({paused:true,volume:0,currentTime:23,playCalls:0,pause(){this.paused=true;},async play(){this.playCalls++;this.paused=false;}});
test('new visitor defaults on; explicit mute survives and legacy choices are respected',()=>{
  assert.equal(soundPreferences(null).enabled,true);
  assert.equal(soundPreferences({enabled:false}).enabled,false);
  assert.equal(soundPreferences(null,{music:false}).enabled,false);
  assert.equal(soundPreferences({enabled:true},{music:false}).enabled,true);
  assert.deepEqual(soundPreferences({musicVolume:Infinity,effectsVolume:130}),{enabled:true,musicVolume:18,effectsVolume:100});
});
test('autoplay rejection keeps the on choice and a later gesture retries',async()=>{
  const m=media();m.play=async function(){this.playCalls++;if(this.playCalls===1)throw Object.assign(new Error(),{name:'NotAllowedError'});this.paused=false;};
  const p=new MusicPlayback(m,true,18);await p.start();assert.equal(p.enabled,true);assert.equal(p.state,'waiting');
  await p.start();assert.equal(p.state,'playing');assert.equal(m.playCalls,2);assert.equal(m.currentTime,23);
});
test('rapid configure does not add players/restart; a mute wins over a late play promise',async()=>{
  const m=media();let resolve;m.play=function(){this.playCalls++;return new Promise(r=>{resolve=()=>{this.paused=false;r();};});};
  const p=new MusicPlayback(m,true,18);const pending=p.start();p.configure(true,22);p.configure(false,22);resolve();await pending;
  assert.equal(m.playCalls,1);assert.equal(m.paused,true);assert.equal(p.state,'muted');assert.equal(m.currentTime,23);
  m.play=async function(){this.playCalls++;this.paused=false;};p.configure(true,18);await p.start();p.configure(true,20);await p.start();
  assert.equal(m.playCalls,2);assert.equal(p.state,'playing');assert.equal(m.currentTime,23);
});
