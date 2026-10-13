import loop from '../public/artwork/login-loop.svg?raw';
import {loginContourState,loginRibPose,loginAnnotationState} from './login-loop-motion';
/** Trusted, build-owned SVG. Native SSR form is never replaced. */
export function mountLoginVisual(form:HTMLFormElement,prefs:{reduced:boolean}){
  const visual=document.querySelector<HTMLElement>('.lr-login-visual');if(!visual)return;
  const svg=new DOMParser().parseFromString(loop,'image/svg+xml').documentElement;
  if(svg.localName!=='svg'||svg.querySelector('parsererror')){visual.dataset.enhanced='false';return;}
  visual.querySelector('img')?.replaceWith(document.importNode(svg,true));visual.dataset.enhanced='true';
  const ribs=[...visual.querySelectorAll<SVGUseElement>('[data-rib]')],group=visual.querySelector('.lr-loop-ribs')!;
  const annotations=[0,1,2].map(i=>({
    point:visual.querySelector<SVGCircleElement>(`[data-annotation-point="${i}"]`)!,
    line:visual.querySelector<SVGPathElement>(`[data-annotation-line="${i}"]`)!,
    text:visual.querySelector<SVGTextElement>(`[data-annotation-text="${i}"]`)!,
    clip:visual.querySelector<SVGRectElement>(`[data-annotation-clip="${i}"]`)!,
  }));
  const media=matchMedia('(prefers-reduced-motion:reduce)');
  let frame=0,last=0,elapsed=0,speed=1,order='';
  const render=(state:{start:number;length:number;strokeWidth:number;phase:string})=>{
    const poses=ribs.map((rib,i)=>{const pose=loginRibPose(state.start+state.length*Number(rib.dataset.rib)/88,state.strokeWidth);rib.setAttribute('transform',`matrix(${pose.matrix.map(x=>x.toFixed(3)).join(' ')})`);return {rib,depth:pose.depth,index:i};}).sort((a,b)=>a.depth-b.depth);
    const next=poses.map(p=>p.index).join(',');if(next!==order){group.append(...poses.map(p=>p.rib));order=next;}
    annotations.forEach((nodes,i)=>{
      const scan=loginAnnotationState(elapsed,state,i);
      nodes.point.style.opacity=String(scan.point);
      nodes.line.style.opacity=String(scan.line);
      nodes.line.setAttribute('stroke-dasharray',`${scan.line.toFixed(4)} 1`);
      nodes.text.style.opacity=String(scan.text);
      nodes.clip.setAttribute('width',(145*scan.text).toFixed(3));
    });
    visual.dataset.motion=state.phase;visual.dataset.cycle=elapsed.toFixed(4);
    visual.dataset.motionFrame=String(Number(visual.dataset.motionFrame||0)+1);
  };
  const draw=(now:number)=>{
    const dt=last?Math.min((now-last)/1000,.05):0;last=now;
    const target=visual.classList.contains('lr-login-input')?.65:1;
    speed+=(target-speed)*(1-Math.exp(-dt*5));elapsed+=dt*speed;
    render(loginContourState(elapsed));frame=requestAnimationFrame(draw);
  };
  const pause=()=>{
    cancelAnimationFrame(frame);last=0;const reduced=prefs.reduced||media.matches;
    visual.dataset.paused=String(document.hidden||reduced);
    if(reduced)render({start:0,length:1,strokeWidth:26,phase:'static'});
    else if(!document.hidden)frame=requestAnimationFrame(draw);
  };
  const focus=()=>{visual.classList.toggle('lr-login-input',form.contains(document.activeElement));};
  document.addEventListener('visibilitychange',pause);window.addEventListener('lr-display-change',pause);media.addEventListener('change',pause);
  window.addEventListener('pagehide',()=>cancelAnimationFrame(frame));window.addEventListener('pageshow',pause);
  form.addEventListener('focusin',focus);form.addEventListener('focusout',()=>queueMicrotask(focus));pause();focus();
}
