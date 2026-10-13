import {bootMarkContour} from './reference/brand.ts';
import {bootLogoTrack} from './reference/boot-logo-tracks.ts';

const smooth=(x:number)=>{x=Math.max(0,Math.min(1,x));return x*x*x*(10+x*(-15+6*x));};
export const LOGIN_LOOP_SECONDS=6.8;
/** Intro draw (229–264) and travelling cut (420–486), then a reverse draw.
 * Its unwrapped offset closes at one revolution. No independent rib wave. */
export function loginContourState(seconds:number){
  const t=((seconds%LOGIN_LOOP_SECONDS)+LOGIN_LOOP_SECONDS)%LOGIN_LOOP_SECONDS;
  if(t<1.8)return {...bootLogoTrack(229+35*smooth(t/1.8)),phase:'draw'};
  if(t<4.8)return {...bootLogoTrack(420+66*smooth((t-1.8)/3)),phase:'cut'};
  const p=smooth((t-4.8)/2),state=bootLogoTrack(264-35*p);
  const offset=(bootLogoTrack(486).start-bootLogoTrack(264).start)*(1-p)+p;
  return {...state,start:state.start+offset,phase:'return'};
}

/** Annotation scans share the contour clock, including focus slowdown and pause.
 * Each part takes 200–220 contour ms (308–338ms at 65% input speed),
 * with 35ms group stagger. */
export function loginAnnotationState(seconds:number,state:{phase:string;length:number;strokeWidth:number},index:number){
  if(state.phase==='static')return {point:1,line:1,text:1};
  const t=((seconds%LOGIN_LOOP_SECONDS)+LOGIN_LOOP_SECONDS)%LOGIN_LOOP_SECONDS,stagger=index*.035;
  const scan=(p:number)=>Math.max(0,Math.min(1,smooth(p)));
  if(state.length<=.0003||state.strokeWidth<=.3)return {point:0,line:0,text:0};
  if(state.phase==='draw')return {
    point:scan((t-.18-stagger)/.20),
    line:scan((t-.27-stagger)/.21),
    text:scan((t-.37-stagger)/.22),
  };
  if(state.phase==='return')return {
    text:1-scan((t-5.84-stagger)/.21),
    line:1-scan((t-5.93-stagger)/.22),
    point:1-scan((t-6.02-stagger)/.20),
  };
  return {point:1,line:1,text:1};
}

// Flatten the trusted M/C/Z path once; no per-frame DOM length queries.
const values=bootMarkContour.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
const points=[{x:values[0],y:values[1],distance:0}];
for(let i=2;i<values.length;i+=6){
  const from=points[points.length-1];
  for(let j=1;j<=48;j++){
    const t=j/48,u=1-t,x=u**3*from.x+3*u*u*t*values[i]+3*u*t*t*values[i+2]+t**3*values[i+4];
    const y=u**3*from.y+3*u*u*t*values[i+1]+3*u*t*t*values[i+3]+t**3*values[i+5],prior=points[points.length-1];
    points.push({x,y,distance:prior.distance+Math.hypot(x-prior.x,y-prior.y)});
  }
}
const total=points[points.length-1].distance;
function point(fraction:number){
  const distance=(((fraction%1)+1)%1)*total;let lo=0,hi=points.length-1;
  while(hi-lo>1){const m=(lo+hi)>>1;if(points[m].distance<distance)lo=m;else hi=m;}
  const a=points[lo],b=points[hi],p=(distance-a.distance)/(b.distance-a.distance);
  return {x:a.x+(b.x-a.x)*p,y:a.y+(b.y-a.y)*p};
}
export function loginRibPose(fraction:number,width=26){
  const p=point(fraction),a=point(fraction-.001),b=point(fraction+.001);
  const dx=b.x-a.x,dy=b.y-a.y,len=Math.hypot(dx,dy),nx=-dy/len,ny=dx/len;
  const z=48*Math.sin(fraction*Math.PI*2),scale=width/26;
  return {depth:z-(p.y-71.5)*.45,matrix:[nx*42*scale,ny*42*.78*scale,34*.26*scale,-34*.55*scale,300+(p.x-155)*1.3+z*.26,193+(p.y-71.5)*1.65*.78-z*.55]};
}
