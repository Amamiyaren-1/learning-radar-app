/** Explicit fixture-only measurement UI. No credentials, forms or business data. */
import type {ArchiveScene} from './reference/scene';
import {qualityPresets,type RenderQuality} from './reference/render-quality';
export function sceneProbe(host:HTMLElement,scene:ArchiveScene,fixture:boolean,preset='original'){
  if(!fixture||!new URLSearchParams(location.search).has('ui_probe'))return ()=>{};
  const panel=document.createElement('details');panel.className='lr-probe';
  panel.innerHTML='<summary>fixture · 本机测量与录屏</summary><label>单因素测量 <select aria-label="测量条件"></select></label><button type="button">开始采样</button><button type="button">录制舞台</button><a hidden>下载连续录屏</a><output></output><pre id="lr-probe-result"></pre>';
  host.parentElement!.after(panel);
  const choices:Record<string,Partial<RenderQuality>>={original:{},performance:qualityPresets.performance,balanced:qualityPresets.balanced,'balanced AO off':{...qualityPresets.balanced,aoSamples:0},'AO off':{aoSamples:0},'DOF off':{depthOfField:0},'shadows off':{shadows:0},'transmission half':{transmission:.5},'resolution 80%':{scale:80},'matrix frozen':{}};
  const select=panel.querySelector('select')!;for(const key of Object.keys(choices)){const o=document.createElement('option');o.textContent=key;o.value=key;select.append(o);}
  select.value=Object.hasOwn(choices,preset)?preset:'original';
  select.onchange=()=>{scene.setMatrixProbe(select.value==='matrix frozen');scene.setQuality({...qualityPresets.original,...choices[select.value]});};
  let previous=0,start=0,active=false;let samples:number[]=[],cpu:number[]=[];
  const output=panel.querySelector('output')!,result=panel.querySelector('pre')!;
  panel.querySelectorAll('button')[0].onclick=()=>{samples=[];cpu=[];previous=0;start=performance.now();active=true;output.textContent='采样中（12秒）';};
  let recorder:MediaRecorder|undefined;
  // Opt-in, bounded fixture trace across the cinematic → interactive handoff.
  const traceEnabled=new URLSearchParams(location.search).has('ui_intro_trace');
  const trace=document.createElement('pre');trace.id='lr-intro-trace';trace.hidden=true;if(traceEnabled)panel.append(trace);
  const traceRows:unknown[]=[];
  panel.querySelectorAll('button')[1].onclick=()=>{
    if(recorder?.state==='recording'){recorder.stop();return;}
    const stream=scene.renderer.domElement.captureStream(60);const chunks:BlobPart[]=[];
    recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp9',videoBitsPerSecond:12000000});
    recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
    recorder.onstop=()=>{stream.getTracks().forEach(t=>t.stop());const a=panel.querySelector('a')!;if(a.href.startsWith('blob:'))URL.revokeObjectURL(a.href);a.href=URL.createObjectURL(new Blob(chunks,{type:'video/webm'}));a.download='fixture-stage-'+Date.now()+'.webm';a.hidden=false;panel.querySelectorAll('button')[1].textContent='录制舞台';};
    recorder.start();panel.querySelectorAll('button')[1].textContent='结束录屏';
  };
  return (now?:number,renderCPU=0)=>{
    panel.dataset.renderFrame=String(scene.renderer.info.render.frame);
    if(traceEnabled&&now){
      const s=scene.getStats(),intro=document.querySelector<HTMLElement>('.lr-full-intro');
      const rect=scene.renderer.domElement.getBoundingClientRect(),model=scene.projectCard(0,1.85);
      const corners=[scene.projectCard(-2.5,3.7),scene.projectCard(2.5,.05)];
      const screen=(p:number[])=>[rect.left+p[0]/1920*rect.width,rect.top+p[1]/1080*rect.height];
      const row={
        now,time:intro?Number(intro.dataset.time):null,phase:intro?.dataset.phase||'interactive',
        frame:scene.renderer.info.render.frame,canvas:[scene.renderer.domElement.width,scene.renderer.domElement.height],
        canvasRect:[rect.left,rect.top,rect.width,rect.height],screenModel:screen(model),screenCorners:corners.map(screen),
        extraction:s.extraction,detail:s.cameraDetail,rail:s.rail,track:s.columnCamera,cell:s.selectedCell,
        camera:s.cameraPosition,aim:s.cameraAim,fov:s.fieldOfView,world:s.modelPosition,worldVelocity:s.worldVelocity,
        rotation:s.modelRotation,clarity:s.decryption.clarity,appearance:s.appearance,targetDetail:s.targetDetail,
        model,corners,pulses:s.pulses,resources:performance.getEntriesByType('resource').filter(e=>e.name.includes('archive-cassette')).length,
      };
      traceRows.push(row);if(traceRows.length>3000)traceRows.shift();
      if(now-Number(trace.dataset.updated||0)>200){trace.textContent=JSON.stringify(traceRows);trace.dataset.updated=String(now);}
      panel.dataset.pose=JSON.stringify(row);
    }
    if(!now||!active)return;
    if(previous){samples.push(now-previous);cpu.push(renderCPU);}previous=now;
    if(now-start<12000)return;active=false;
    const sorted=[...samples].sort((a,b)=>a-b),mean=samples.reduce((a,b)=>a+b,0)/samples.length;
    const gl=scene.renderer.getContext(),ext=gl.getExtension('WEBGL_debug_renderer_info');
    const stats=scene.getStats();
    const data={fixture:true,date:new Date().toISOString(),condition:select.value,visibility:document.visibilityState,userAgent:navigator.userAgent,viewport:[innerWidth,innerHeight],dpr:devicePixelRatio,gpu:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),fonts:[...document.fonts].map(f=>({family:f.family,weight:f.weight,status:f.status})),quality:JSON.parse(host.dataset.renderQuality||'{}'),frames:samples.length,meanMS:mean,p50MS:sorted[Math.floor(sorted.length*.5)],p95MS:sorted[Math.floor(sorted.length*.95)],fps:1000/mean,cpuMeanMS:cpu.reduce((a,b)=>a+b,0)/cpu.length,stats,resources:performance.getEntriesByType('resource').map(e=>({name:e.name.split('/static/ui/')[1],duration:e.duration})).filter(e=>e.name),samples};
    result.textContent=JSON.stringify(data);output.textContent=`${data.fps.toFixed(1)} fps · P95 ${data.p95MS.toFixed(1)}ms`;
  };
}
