/** Playback policy stays separate from the user's persistent choice. */
type Track={loop:boolean;volume:number;paused:boolean;play:()=>Promise<void>;pause:()=>void};
export class MusicPlayback {
  state:'waiting'|'playing'|'muted'|'unavailable'='waiting';
  private pending=false;
  private retryAfterPending=false;
  readonly media:Track;enabled:boolean;private changed:()=>void;
  constructor(media:Track,enabled:boolean,volume:number,changed:()=>void=()=>{}){
    this.media=media;this.enabled=enabled;this.changed=changed;
    media.loop=true;this.configure(enabled,volume,false);
  }
  configure(enabled:boolean,volume:number,attempt=true){
    if(enabled&&!this.enabled&&this.pending)this.retryAfterPending=true;
    this.enabled=enabled;this.media.volume=Math.max(0,Math.min(1,volume/100));
    if(!enabled){this.media.pause();this.state='muted';}
    else if(!this.media.paused)this.state='playing';
    else if(this.state!=='unavailable')this.state='waiting';
    this.changed();if(enabled&&attempt)void this.start();
  }
  async start(blocked=false){
    if(!this.enabled||this.pending||this.state==='unavailable')return;
    if(!this.media.paused){this.state='playing';this.changed();return;}
    if(blocked){this.state='waiting';this.changed();return;}
    this.pending=true;
    try{await this.media.play();this.state=this.enabled&&!this.media.paused?'playing':this.enabled?'waiting':'muted';}
    catch(error){this.state=!this.enabled?'muted':(error as Error).name==='NotAllowedError'?'waiting':'unavailable';}
    finally{this.pending=false;if(!this.enabled)this.media.pause();this.changed();if(this.retryAfterPending){this.retryAfterPending=false;if(this.enabled)void this.start();}}
  }
  fail(){this.state='unavailable';this.changed();}
}
