/** One decoded loop with exact offsets; independent of HTTP Range support. */
export class BufferedMusic{
  private buffer:AudioBuffer|undefined;private source:AudioBufferSourceNode|undefined;
  private offset=0;private startedAt=0;private wantsPlayback=false;private gain:GainNode;
  private ready:Promise<void>;private level=.18;
  loop=true;error=0;
  constructor(private context:AudioContext,url:string){
    this.gain=context.createGain();this.gain.gain.value=this.level;this.gain.connect(context.destination);
    this.ready=(async()=>{try{const response=await fetch(url);if(!response.ok)throw new Error('Music unavailable');this.buffer=await context.decodeAudioData(await response.arrayBuffer());}catch(error){this.error=1;throw error;}})();
    void this.ready.catch(()=>{});
  }
  get paused(){return !this.source;}
  get duration(){return this.buffer?.duration||0;}
  get readyState(){return this.buffer?4:0;}
  get currentTime(){return this.duration?(this.offset+(this.source?this.context.currentTime-this.startedAt:0))%this.duration:this.offset;}
  set currentTime(value:number){if(Number.isFinite(value)&&value>=0)this.offset=this.duration?value%this.duration:value;}
  get volume(){return this.level;}
  set volume(value:number){this.level=value;this.gain.gain.setTargetAtTime(value,this.context.currentTime,.03);}
  async play(){
    this.wantsPlayback=true;await this.ready;await this.context.resume();
    if(!this.wantsPlayback||this.source)return;
    const source=this.context.createBufferSource();source.buffer=this.buffer!;source.loop=this.loop;source.connect(this.gain);
    this.offset%=this.duration;this.startedAt=this.context.currentTime;this.source=source;source.start(0,this.offset);
  }
  pause(){this.wantsPlayback=false;if(!this.source)return;this.offset=this.currentTime;const source=this.source;this.source=undefined;try{source.stop();}catch{}source.disconnect();}
  get voices(){return this.source?1:0;}
}
