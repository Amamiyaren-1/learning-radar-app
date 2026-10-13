import {BootSequence} from './reference/boot';
import {logo} from './reference/brand';
import {InspectionOverlay} from './reference/inspection-overlay';
import type {ArchiveScene} from './reference/scene';
import './intro.css';
import {soundToggle} from './sound';
import {smooth as ease,introReturn,INTRO_RETURN_START,INTRO_RETURN_END,INTRO_END} from './reference/motion';
/** Established display followed by a return, sharing the archive's canvas. */
export class FullScreenIntro{
  private overlay=document.createElement('div');private stage=document.createElement('div');
  private placeholder=document.createComment('archive scene position');private sequence:BootSequence;
  private start=performance.now();private prior=document.activeElement as HTMLElement|null;
  private inspection:InspectionOverlay;
  private siblings=new Map<HTMLElement,boolean>();private scroll=[scrollX,scrollY];
  private slot=document.createElement('div');private dockRect:DOMRect|undefined;private dockStartRect:DOMRect|undefined;
  active=true;
  constructor(private host:HTMLElement,private scene:ArchiveScene,private done:()=>void){
    this.overlay.className='lr-full-intro';this.overlay.setAttribute('role','dialog');this.overlay.setAttribute('aria-modal','true');this.overlay.setAttribute('aria-label','全屏入场 · 显示演出');
    this.stage.className='lr-intro-stage';this.stage.dataset.mode='boot';
    this.stage.innerHTML=`<div id="boot-background" class="boot-background"><svg viewBox="0 0 1920 1080" preserveAspectRatio="none"><g fill="none" stroke="#fff" stroke-width="3"><path d="M-210 705C-45 705 182 704 247 567C337 377 99 306 4 435S27 680 169 631C309 584 227 314 279 111S568-113 568-113"/><path d="M1560-80C1374 114 1671 168 1601 323S1371 367 1431 480S1692 666 1559 787S1329 886 1498 1130"/><circle cx="1450" cy="648" r="346"/></g></svg><div class="boot-white"></div></div>
      <header class="brand"><h1>LEARNING RADAR</h1><div>PERSONAL ARCHIVE</div><p>个人助手</p></header>
      <section class="boot" aria-hidden="true"><div class="access-text">ACCESS</div><div class="boot-logo">${logo}</div><div class="auth-status"><span>▪</span><span id="auth-message"></span><i></i></div>
      <div class="scan"><svg viewBox="0 0 1920 1080"><g fill="none" stroke="#080a08" stroke-width="2" stroke-linecap="round"><path/><path stroke="#fff"/><path/><path/><path/><path/><circle class="orbit-dot" r="8" fill="#78552e" stroke="none"/><circle class="orbit-dot" r="8" fill="#78552e" stroke="none"/><circle class="scan-core" cx="960" cy="540" r="5" fill="#080a08" stroke="none"/></g></svg><span>DISPLAY SEQUENCE</span></div>
      <div class="welcome"><div class="welcome-panel"></div><div class="welcome-heading">WELCOME TO</div><div class="welcome-company"><strong>LEARNING RADAR</strong><strong class="welcome-highlight">LEARNING RADAR</strong></div><div class="welcome-database">PERSONAL ARCHIVE</div><div class="welcome-logo">${logo}</div></div></section>
      <div class="lr-intro-callout"><small>PERSONAL ARCHIVE</small><div>SELECTING FILES…</div><i></i><p></p></div>
      <div class="powered">POWERED BY <b>LEARNING RADAR</b><i></i></div><div class="lr-intro-caption"></div>`;
    const skip=document.createElement('button');skip.type='button';skip.className='lr-intro-skip';skip.textContent='跳过开场 ↗';skip.onclick=()=>this.skip();
    const label=document.createElement('span');label.className='lr-intro-label';label.textContent='个人助手 · 显示演出';
    const marks=document.createElement('div');marks.className='lr-intro-inspection';marks.innerHTML='<svg id="inspection-marks" viewBox="0 0 1920 1080" preserveAspectRatio="none" aria-hidden="true"><path id="inspection-lines"/><g id="inspection-corners"></g><circle id="inspection-point" r="1.8"/></svg><div id="inspection-text">DISPLAY:<strong>PERSONAL ARCHIVE</strong></div>';
    this.slot.className=host.className;this.slot.setAttribute('aria-hidden','true');this.slot.style.visibility='hidden';this.host.before(this.placeholder,this.slot);this.overlay.append(host,this.stage,marks,skip,label,soundToggle('lr-intro-sound'));document.body.append(this.overlay);
    for(const node of document.body.children)if(node instanceof HTMLElement&&node!==this.overlay){this.siblings.set(node,node.inert);node.inert=true;}
    document.documentElement.classList.add('lr-intro-active');host.classList.add('lr-intro-scene');scene.setMode('hidden');
    this.sequence=new BootSequence(this.stage);this.sequence.update(0);this.inspection=new InspectionOverlay(marks);this.resize();scene.resize();skip.focus();
    window.addEventListener('resize',this.resize);this.overlay.addEventListener('keydown',this.key);
  }
  private key=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();this.skip();}if(event.key==='Tab'){event.preventDefault();const buttons=[...this.overlay.querySelectorAll<HTMLButtonElement>('button')];const at=buttons.indexOf(document.activeElement as HTMLButtonElement);buttons[(at+(event.shiftKey?-1:1)+buttons.length)%buttons.length]?.focus();}};
  private resize=()=>{
    // Landscape retains the complete 1920×1080 composition. Portrait crops
    // outer ornament while retaining the central scan/logo at readable scale.
    const scale=Math.min(innerWidth/(innerWidth<760?1080:1920),innerHeight/1080);
    this.stage.style.transform=`translate(-50%,-50%) scale(${scale})`;
  };
  private prepareDock(){
    // The stable scrollbar gutter makes the rendered fullscreen canvas narrower
    // than innerWidth. Start from its actual rectangle to preserve projection.
    this.dockStartRect=this.host.getBoundingClientRect();
    const r=this.slot.getBoundingClientRect();
    if(r.width>0&&r.height>0){
      // Home's native task form can place the archive below the first fold.
      // Position the real page while the full canvas still occupies the view.
      const margin=Math.max(16,Math.min(96,(innerHeight-r.height)/2));
      this.scroll=[scrollX,Math.max(0,scrollY+r.top-margin)];window.scrollTo(...this.scroll as [number,number]);
    }
    this.dockRect=this.slot.getBoundingClientRect();
  }
  sample(now:number){
    if(!this.active)return undefined;const elapsed=(now-this.start)/1000,t=Math.min(INTRO_END,elapsed);
    this.overlay.dataset.time=t.toFixed(4);
    if(elapsed>=INTRO_END){this.finish();return undefined;}
    if(elapsed>=INTRO_RETURN_END){
      if(!this.dockRect)this.prepareDock();
      this.dockRect=this.slot.getBoundingClientRect();const r=this.dockRect,from=this.dockStartRect!,p=ease((elapsed-INTRO_RETURN_END)/.9);
      this.overlay.classList.add('lr-intro-docking');
      Object.assign(this.host.style,{left:from.left+(r.left-from.left)*p+'px',top:from.top+(r.top-from.top)*p+'px',width:from.width+(r.width-from.width)*p+'px',height:from.height+(r.height-from.height)*p+'px',right:'auto',bottom:'auto'});this.scene.resize();
    }
    const motion=this.sequence.update(Math.min(35,t));
    // The design stage fits inside wide viewports; its background cannot hide
    // the canvas in the side gutters. Restore the reference's scene reveal,
    // using the same cinematic progress instead of adding an edge cover.
    const reveal=ease((t-22)/.4);this.host.style.opacity=String(reveal);
    this.overlay.dataset.phase=t>=INTRO_RETURN_END?'dock':t>=INTRO_RETURN_START?'return':t>=34?'hold':t>=28.3?'inspect':t>=25.68?'select':t>=22?'array':motion.step;
    const caption=this.stage.querySelector<HTMLElement>('.lr-intro-caption')!;
    caption.textContent=t>=INTRO_RETURN_START?'收回档案 · 返回选择':t>=28.3?'抽取档案 · 仅显示演出':t>=25.68?'当前档案':t>=22?'档案阵列':motion.step==='scan'?'显示资源准备':motion.step==='welcome'?'欢迎使用个人助手':'';
    const callout=this.stage.querySelector<HTMLElement>('.lr-intro-callout')!;callout.style.opacity=t>=22&&t<28.3?'1':'0';
    callout.querySelector('div')!.textContent=t<25.68?'SELECTING FILES…':'CURRENT FILE';
    callout.querySelector('p')!.textContent=t>=25.68?this.placeholder.parentElement?.closest<HTMLElement>('.lr-archive')?.dataset.selectedIdentity?.replace(/^(repo|run):/,'')||'':'';
    this.stage.querySelector<HTMLElement>('#boot-background')!.style.visibility=t>=21.92?'hidden':'visible';
    return {reveal,lift:ease((t-26)/1.8),zoom:(.55*ease((t-27.3)/1.65)+.45*ease((t-29)/5))*(1-introReturn(t)),time:t};
  }
  renderInspection(){if(this.active)this.inspection.render(this.scene.decryptionFrame,(x,y)=>this.scene.projectCard(x,y),true);}
  private skip(){
    this.finish();
  }
  finish(){
    if(!this.active)return;this.active=false;window.removeEventListener('resize',this.resize);
    // Skip/reduced-motion cleanup resolves directly to ordinary selection.
    if(!this.dockRect)this.prepareDock();const r=this.slot.getBoundingClientRect();
    if(r.width>0&&r.height>0)Object.assign(this.host.style,{left:r.left+'px',top:r.top+'px',width:r.width+'px',height:r.height+'px',right:'auto',bottom:'auto'});
    try{if(r.width>0&&r.height>0)this.scene.resize();this.scene.update(performance.now()/1000,{reveal:1,lift:0,zoom:0,time:INTRO_END});}catch{}
    this.sequence.reset();this.host.classList.remove('lr-intro-scene');this.host.removeAttribute('style');this.placeholder.replaceWith(this.host);this.slot.remove();
    this.overlay.remove();document.documentElement.classList.remove('lr-intro-active');
    for(const [node,inert] of this.siblings)node.inert=inert;
    this.scene.resize();this.done();window.scrollTo(this.scroll[0],this.scroll[1]);this.prior?.focus({preventScroll:true});
  }
}
