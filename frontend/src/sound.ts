import {MusicPlayback} from './music-playback';
import {BufferedMusic} from './buffered-music';
import {soundDefaults as defaults,clampVolume as clamp,soundPreferences} from './sound-preferences';
export type Cue='click'|'switch'|'open'|'back';
const storageKey='lr-sound-v1',positionKey='lr-music-position-v1';
const read=(key:string)=>{try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}};

class SoundManager{
  private prefs=soundPreferences(read(storageKey),read('lr-display-v1'));
  private music:BufferedMusic|undefined;
  private playback:MusicPlayback;
  private context:AudioContext|undefined;private buffers=new Map<Cue,AudioBuffer>();private loading:Promise<void>|undefined;
  private voice:AudioBufferSourceNode|undefined;private gain:GainNode|undefined;private voiceTimer=0;private lastCue=-Infinity;
  private started=false;private blockedProbe=false;private gesture=false;private cues=0;private suppressed=0;private resumed=false;
  constructor(){
    try{this.context=new AudioContext();this.music=new BufferedMusic(this.context,'/static/ui/audio/atmosphere.ogg');}catch{}
    this.playback=new MusicPlayback(this.music||{loop:true,volume:0,paused:true,play:async()=>{throw new Error('Audio unavailable');},pause:()=>{}},this.prefs.enabled,this.prefs.musicVolume,()=>this.changed());
    try{const saved=JSON.parse(sessionStorage.getItem(positionKey)||'null');if(this.music&&saved&&Number.isFinite(saved.time)&&Date.now()-saved.at<30*60*1000){this.music.currentTime=saved.time;this.resumed=true;}}catch{}
  }
  start(fixture=false){
    if(this.started)return;this.started=true;
    this.blockedProbe=fixture&&new URLSearchParams(location.search).has('ui_audio_block');
    void this.playback.start(this.blockedProbe);void this.loadEffects();
    const unlock=(event:Event)=>{
      if(!event.isTrusted||document.hidden)return;
      if(event instanceof KeyboardEvent&&(event.repeat||event.ctrlKey||event.metaKey||event.altKey||['Shift','Control','Alt','Meta'].includes(event.key)))return;
      if((event.target as Element)?.closest?.('[data-lr-sound-toggle]'))return;
      this.gesture=true;void this.unlock();
    };
    document.addEventListener('pointerdown',unlock,{capture:true});document.addEventListener('keydown',unlock,{capture:true});
    document.addEventListener('click',event=>{
      if(!event.isTrusted||event.defaultPrevented)return;
      const target=(event.target as Element).closest<HTMLElement>('button,a[href],summary,input[type=submit]');
      if(!target||target.closest('[data-lr-sound=handled],[disabled],[aria-disabled=true],[aria-busy=true],[inert]'))return;
      if(target instanceof HTMLButtonElement&&target.type==='submit'&&target.form&&!target.form.checkValidity())return;
      this.play('click');
    });
    window.addEventListener('pagehide',()=>{this.savePosition();this.music?.pause();this.stopVoice();});
    window.addEventListener('pageshow',event=>{if(event.persisted)void this.playback.start();});
    window.addEventListener('storage',event=>{if(event.key===storageKey){this.prefs=soundPreferences(read(storageKey));this.configure(false);}});
    document.addEventListener('visibilitychange',()=>{if(document.hidden){this.savePosition();this.music?.pause();this.stopVoice();}else void this.playback.start();});
    if(fixture&&new URLSearchParams(location.search).has('ui_audio_probe')){
      const panel=document.createElement('details');panel.className='lr-probe lr-sound-probe';
      const title=document.createElement('summary');title.textContent='fixture · 声音状态';const result=document.createElement('pre');result.id='lr-sound-result';panel.append(title,result);document.querySelector('.lr-footer')?.before(panel);
      const update=()=>{result.textContent=JSON.stringify(this.stats());};update();window.addEventListener('lr-sound-change',update);setInterval(update,1000);
    }
  }
  private savePosition(){try{sessionStorage.setItem(positionKey,JSON.stringify({time:this.music?.currentTime||0,at:Date.now()}));}catch{}}
  private changed(){if(!this.playback)return;window.dispatchEvent(new Event('lr-sound-change'));}
  private configure(persist=true){
    if(persist){try{localStorage.setItem(storageKey,JSON.stringify(this.prefs));}catch{}}
    this.playback.configure(this.prefs.enabled,this.prefs.musicVolume,!this.blockedProbe||this.gesture);
    if(!this.prefs.enabled){this.stopVoice();void this.context?.suspend().catch(()=>{});}else if(this.gesture)void this.unlock();
    if(this.gain&&this.context)this.gain.gain.setTargetAtTime(this.prefs.effectsVolume/100,this.context.currentTime,.02);
    this.changed();
  }
  setEnabled(enabled:boolean){this.gesture=true;this.prefs.enabled=enabled;this.configure();}
  setVolume(kind:'musicVolume'|'effectsVolume',value:number){this.prefs[kind]=clamp(value,defaults[kind]);this.configure();}
  get preferences(){return {...this.prefs};}
  private async loadEffects(){
    if(this.loading)return this.loading;
    this.loading=(async()=>{
      try{
        if(!this.context)return;this.gain=this.context.createGain();this.gain.gain.value=this.prefs.effectsVolume/100;this.gain.connect(this.context.destination);
        await Promise.all((['click','switch','open','back'] as Cue[]).map(async cue=>{
          const response=await fetch('/static/ui/audio/'+cue+'.wav');if(!response.ok)throw new Error('Audio unavailable');
          const buffer=await this.context!.decodeAudioData(await response.arrayBuffer());this.buffers.set(cue,buffer);
        }));
      }catch{}this.changed();
    })();return this.loading;
  }
  private async unlock(){
    if(!this.prefs.enabled)return;
    void this.playback.start();
    try{await this.loadEffects();if(this.prefs.enabled)await this.context?.resume();}catch{}
    this.changed();
  }
  private stopVoice(){clearTimeout(this.voiceTimer);if(this.voice){try{this.voice.stop();}catch{}this.voice.disconnect();this.voice=undefined;}}
  play(cue:Cue){
    if(!this.prefs.enabled||!this.context||this.context.state!=='running'||!this.gain||!this.buffers.has(cue))return;
    const now=performance.now();if(now-this.lastCue<80){this.suppressed++;this.changed();return;}this.lastCue=now;
    this.stopVoice();const voice=this.context.createBufferSource();voice.buffer=this.buffers.get(cue)!;voice.connect(this.gain);this.voice=voice;
    voice.onended=()=>{voice.disconnect();if(this.voice===voice)this.voice=undefined;};voice.start();this.cues++;
    this.voiceTimer=window.setTimeout(()=>{if(this.voice===voice)this.stopVoice();},Math.ceil(voice.buffer.duration*1000)+40);this.changed();
  }
  stats(){return {enabled:this.prefs.enabled,musicVolume:this.prefs.musicVolume,effectsVolume:this.prefs.effectsVolume,state:this.playback.state,musicPlayers:this.music?1:0,musicVoices:this.music?.voices||0,currentTime:this.music?.currentTime||0,duration:this.music?.duration||0,paused:this.music?.paused??true,readyState:this.music?.readyState||0,musicError:this.music?.error||0,context:this.context?.state||'none',decoded:[...this.buffers].map(([cue,b])=>({cue,duration:b.duration,channels:b.numberOfChannels,sampleRate:b.sampleRate,peak:Math.max(...b.getChannelData(0).map(Math.abs))})),activeVoices:this.voice?1:0,cues:this.cues,suppressed:this.suppressed,resumed:this.resumed,autoplayBlockFixture:this.blockedProbe,gesture:this.gesture};}
}
export const sound=new SoundManager();
function renderToggle(button:HTMLButtonElement){const {enabled}=sound.preferences;button.textContent=enabled?'声音开启':'声音关闭';button.setAttribute('aria-label',enabled?'关闭声音':'开启声音');button.setAttribute('aria-pressed',String(enabled));button.title=enabled?'配乐将在浏览器允许后播放；点击可静音':'点击开启配乐与音效';}
window.addEventListener('lr-sound-change',()=>document.querySelectorAll<HTMLButtonElement>('[data-lr-sound-toggle]').forEach(renderToggle));
export function soundToggle(className='lr-sound-toggle'){
  const button=document.createElement('button');button.type='button';button.className=className;button.dataset.lrSoundToggle='';button.dataset.lrSound='handled';
  renderToggle(button);button.onclick=()=>sound.setEnabled(!sound.preferences.enabled);return button;
}
