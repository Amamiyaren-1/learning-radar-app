import '@kitlangton/rolling-number/styles.css';
import './reference/document-decryption.css';
import {createRollingNumber,createRollingText} from '@kitlangton/rolling-number';
import {ArchiveScene} from './reference/scene';
import {DocumentDecryption} from './reference/document-decryption';
import {qualityPresets} from './reference/render-quality';
import {configureRecords,records,archiveColumns,columnFiles,fileLocation,type ArchiveRecord} from './reference/data';
import {wrap,type ArchiveNavigation} from './reference/archive-loop';
import {archiveReader} from './archive-reader';
import {FullScreenIntro} from './full-screen-intro';
import {sceneProbe} from './diagnostics';
import {sound} from './sound';

const el=<K extends keyof HTMLElementTagNameMap>(tag:K,text='',className='')=>{const n=document.createElement(tag);n.textContent=text;n.className=className;return n;};
const safeLink=(value:unknown)=>{if(typeof value!=='string')return '/history';const u=new URL(value,location.origin);return u.origin===location.origin&&u.pathname.startsWith('/')?u.pathname+u.search:'/history';};
function projectRecords(boot:any):ArchiveRecord[]{
  const data=boot.data;
  if(boot.page_kind==='projects')return (data.projects||[]).map((r:any)=>({id:'repo:'+r.repo,title:r.display_title||r.repo||'未分类',identity:r.repo_label||r.repo,url:safeLink(r.url),description:r.display_summary||r.description,source:r.display_note,humanTitle:['published','draft'].includes(r.display_sources?.title?.kind),tags:[...new Set((r.tags||[]).map((t:any)=>t.label))] }));
  const reports=boot.page_kind==='report'?[data.report]:boot.page_kind==='project'?(data.projects||[]).flatMap((r:any)=>r.reports||[]):(data.reports||[]);
  return reports.filter(Boolean).map((r:any)=>({id:'run:'+r.run_id,title:(boot.page_kind==='home'?r.display_title:'')||r.title||'保存的运行记录',identity:boot.page_kind==='home'?(r.repo_label||r.repo):undefined,url:safeLink(r.url),description:([r.display_summary,r.date,r.status].filter(Boolean).join(' / ')),source:r.display_note,humanTitle:boot.page_kind==='home'&&['published','draft'].includes(r.display_sources?.title?.kind),tags:[r.status||'保存记录']}));
}

export async function mountArchive(app:HTMLElement,business:HTMLElement,boot:any,prefs:any,settings:HTMLDialogElement){
  configureRecords(projectRecords(boot));
  const root=el('section','','lr-archive');root.setAttribute('aria-label','三维档案展示');
  const head=el('header','','lr-archive-header');head.append(el('span',boot.page_kind==='report'?'报告档案':'档案窗口'));
  const fold=el('button','收起三维');fold.type='button';fold.setAttribute('aria-expanded','true');head.append(fold);root.append(head);
  const body=el('div','','lr-archive-body');root.append(body);app.append(root);
  const host=el('div','','lr-scene');body.append(host);
  const info=el('div','','lr-archive-info');body.append(info);
  const group=el('select');group.setAttribute('aria-label','档案展示分组');
  archiveColumns.forEach((tag,lane)=>{const option=el('option',`${tag} / ${columnFiles(lane).length}`);option.value=String(lane);group.append(option);});
  info.append(group);
  const number=el('div','','lr-file-number');number.setAttribute('aria-hidden','true');info.append(number);
  const title=el('h2','','lr-file-title');info.append(title);
  const identity=el('p','','lr-file-identity');info.append(identity);
  const description=el('p','','lr-file-description');info.append(description);
  const source=el('p','','lr-file-source lr-meta');source.hidden=true;info.append(source);
  const controls=el('div','','lr-archive-controls');const previous=el('button','上一份'),next=el('button','下一份');controls.append(previous,next);info.append(controls);
  const ticks=el('div','','lr-ticks');ticks.setAttribute('aria-label','当前分组档案刻度');info.append(ticks);
  const access=el('a','打开当前档案','lr-access');info.append(access);
  [previous,next,group,ticks,access].forEach(n=>n.dataset.lrSound='handled');
  const native=business.querySelector<HTMLElement>('[data-lr-role=project],[data-lr-role=report],[data-lr-role=report-body]')||business.querySelector<HTMLElement>('h1');
  if(native)native.id='lr-native-records';
  const all=el('a','查看下方完整列表与原生操作','lr-all-records');all.href=native?'#lr-native-records':'#lr-business';info.append(all);
  let scene:ArchiveScene|undefined;
  let intro:FullScreenIntro|undefined;
  const reader=archiveReader(business,app,body,info,detail=>{mode=detail?'detail':'archive';scene?.setMode(mode);},()=>!!prefs.reduced);
  const windowInfo=el('p',boot.data.truncated?'此处展示前 40 条。完整记录保留在下方列表。':'模型是有限展示窗口；档案身份与真实链接以文字为准。','lr-notice');root.append(windowInfo);
  fold.onclick=()=>{body.hidden=!body.hidden;fold.textContent=body.hidden?'展开三维':'收起三维';fold.setAttribute('aria-expanded',String(!body.hidden));if(!body.hidden)scene?.resize();};
  if(!records.length){
    title.textContent='尚无可展示档案';description.textContent='可以从首页发现项目，或调整项目中心的搜索与标签。';
    access.href='/';access.textContent='前往首页';[previous,next,group].forEach(n=>n.disabled=true);ticks.replaceChildren();host.append(el('div','空档案窗口','lr-empty-scene'));root.dataset.scene='empty';return;
  }
  let selected=0,lane=0;const memory=new Map<string,string>();
  try{const saved=JSON.parse(sessionStorage.getItem('lr-selection')||'{}');for(const [k,v] of Object.entries(saved))if(typeof v==='string')memory.set(k,v);selected=Math.max(0,records.findIndex(r=>r.id===memory.get('全部档案')));}catch{}
  let frame=0;let latest=performance.now()/1000;let destroyed=false;
  const titleRoll=createRollingText(title,{text:'',animated:!prefs.reduced,duration:380});
  let titleWrapped=false;
  function fitTitle(animate=false){
    const value=records[selected]?.title;if(!value)return;
    const probe=el('span',value);probe.setAttribute('aria-hidden','true');
    probe.style.cssText=`position:absolute;visibility:hidden;white-space:pre;font:${getComputedStyle(title).font}`;
    const style=getComputedStyle(info),available=info.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight);
    info.append(probe);const wrapped=available>0&&probe.getBoundingClientRect().width>available;probe.remove();
    title.dataset.titleWrap=String(wrapped);
    if(animate||wrapped!==titleWrapped||wrapped){
      titleRoll.update({text:value,animated:animate&&!prefs.reduced&&!wrapped});
      if(!animate||wrapped||prefs.reduced)titleRoll.finish();
    }
    titleWrapped=wrapped;
  }
  const fontDone=()=>fitTitle();document.fonts?.addEventListener('loadingdone',fontDone);
  const counter=createRollingNumber(number,{value:selected+1,animated:!prefs.reduced,duration:420});
  const documentEffect=new DocumentDecryption();let mode:'archive'|'detail'=boot.page_kind==='report'?'detail':'archive';
  let observer:ResizeObserver|undefined;let changeHandler:(()=>void)|undefined;
  let openAction=0;
  const dispose=()=>{if(destroyed)return;destroyed=true;intro?.finish();reader.dispose();cancelAnimationFrame(frame);observer?.disconnect();document.fonts?.removeEventListener('loadingdone',fontDone);if(changeHandler)window.removeEventListener('lr-display-change',changeHandler);documentEffect.clear();scene?.dispose();titleRoll.destroy();counter.destroy();};
  window.addEventListener('pagehide',event=>{documentEffect.clear();if(!(event as PageTransitionEvent).persisted)dispose();});
  function save(){try{sessionStorage.setItem('lr-selection',JSON.stringify(Object.fromEntries(memory)));}catch{}}
  function update(index:number,navigation?:ArchiveNavigation,audible=false){
    if(!records[index])return;
    const changed=index!==selected;if(changed&&audible)sound.play('switch');
    openAction++;reader.cancel();access.removeAttribute('aria-busy');access.textContent='打开当前档案';
    selected=index;const r=records[index];memory.set(archiveColumns[lane],r.id);save();
    scene?.select(selected,navigation);title.classList.toggle('lr-human-title',!!r.humanTitle);title.setAttribute('aria-label',r.title);fitTitle(true);counter.update({value:columnFiles(lane).indexOf(selected)+1,animated:!prefs.reduced});
    identity.textContent=r.identity||r.id.replace(/^(repo|run):/,'');description.textContent=r.description;source.textContent=r.source||'';source.hidden=!r.source;access.href=boot.page_kind==='report'?'#lr-native-records':r.url;access.textContent=boot.page_kind==='report'?'阅读下方报告':'打开当前档案';root.dataset.selectedIdentity=r.id;
    // Rebuild a bounded tick window when the current group or selection changes.
    ticks.replaceChildren();const files=columnFiles(lane);const at=files.indexOf(selected);const start=Math.max(0,Math.min(files.length-9,at-4));
    files.slice(start,start+9).forEach((value,j)=>{const tick=el('button',String(start+j+1));tick.type='button';tick.dataset.lrSound='handled';tick.setAttribute('aria-label',`选择 ${records[value].title}`);tick.setAttribute('aria-pressed',String(value===selected));tick.onclick=()=>update(value,undefined,true);ticks.append(tick);});
    identity.setAttribute('aria-live','polite');previous.disabled=next.disabled=files.length<2;
  }
  function shift(direction:number){const files=columnFiles(lane);if(files.length<2)return;update(files[wrap(files.indexOf(selected)+direction,files.length)],{axis:'row',direction},true);}
  previous.onclick=()=>shift(-1);next.onclick=()=>shift(1);
  group.onchange=()=>{
    reader.cancel();access.removeAttribute('aria-busy');mode='archive';scene?.setMode(mode);
    lane=Number(group.value);const files=columnFiles(lane);const remembered=files.find(i=>records[i].id===memory.get(archiveColumns[lane]));
    if(!files.length){scene?.renderer.domElement.setAttribute('hidden','');title.setAttribute('aria-label','此分组没有档案');titleRoll.update({text:'此分组没有档案'});identity.textContent='';description.textContent='请选择其他分组。';source.hidden=true;ticks.replaceChildren();previous.disabled=next.disabled=true;access.hidden=true;return;}
    const index=remembered??files[0];scene?.renderer.domElement.removeAttribute('hidden');access.hidden=false;update(index,{cell:fileLocation(index,lane)},true);
  };
  update(selected);
  try{
    const baseline=boot.fixture&&new URLSearchParams(location.search).has('ui_model_baseline');
    scene=new ArchiveScene(host,undefined,false,baseline?'baseline':'refined');scene.setReduced(!!prefs.reduced);scene.setQuality(qualityPresets[prefs.quality as 'original'|'balanced'|'performance']||qualityPresets.original);
    await Promise.race([scene.load(),new Promise<never>((_,reject)=>setTimeout(()=>reject(new Error('Asset timeout')),18000))]);
    if(destroyed){scene.dispose();return;}
    scene.setMode(mode);update(selected);root.dataset.scene='ready';
    root.dataset.modelPool=String(scene.getStats().archiveCount);
    scene.onSelect=(index,cell)=>{if(!reader.active&&!intro?.active&&index!==undefined&&records[index]){lane=wrap(cell?.lane??fileLocation(index).lane,archiveColumns.length);group.value=String(lane);update(index,cell?{cell}:undefined,true);}};
    host.addEventListener('webglcontextlost',event=>{event.preventDefault();intro?.finish();documentEffect.clear();root.dataset.scene='failed';cancelAnimationFrame(frame);if(reader.active)location.assign(reader.url);else host.replaceChildren(el('p','三维上下文不可用；下方列表与操作仍可使用。','lr-notice'));});
    access.addEventListener('click',event=>{
      if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||event.button!==0||boot.page_kind==='report')return;
      event.preventDefault();if(access.getAttribute('aria-busy')==='true')return;
      sound.play('open');const action=++openAction;access.setAttribute('aria-busy','true');access.textContent='档案抽取与读取中…';
      void reader.open(access.href).finally(()=>{if(action===openAction){access.removeAttribute('aria-busy');access.textContent='打开当前档案';}});
    });
    if(boot.page_kind==='report'&&!prefs.reduced){const article=business.querySelector<HTMLElement>('[data-lr-role=report-body]');if(article){documentEffect.reset(article,false);setTimeout(()=>documentEffect.clear(),6500);}}
    const probe=sceneProbe(host,scene,!!boot.fixture,prefs.quality);
    const draw=(now:number)=>{
      if(destroyed)return;latest=now/1000;
      const cpuStart=performance.now();
      try{if(!root.hidden&&!body.hidden&&!document.hidden)scene?.update(latest,intro?.sample(now));intro?.renderInspection();if(scene){reader.reveal(scene.detailVisibility);documentEffect.update(latest,scene.decryptionFrame,!!prefs.reduced);}probe(now,performance.now()-cpuStart);}
      catch{intro?.finish();documentEffect.clear();root.dataset.scene='failed';if(reader.active)location.assign(reader.url);else{dispose();root.remove();app.append(el('p','三维展示中断；原生内容和操作仍可使用。','lr-notice'));}return;}
      frame=requestAnimationFrame(draw);
    };frame=requestAnimationFrame(draw);
    observer=new ResizeObserver(()=>{if(!body.hidden)scene?.resize();documentEffect.refresh();fitTitle();});observer.observe(host);
    const displayChange=()=>{
      if(prefs.flat&&reader.active){location.assign(reader.url);return;}
      document.documentElement.classList.toggle('lr-reduced',!!prefs.reduced);scene?.setReduced(!!prefs.reduced);scene?.setQuality(qualityPresets[prefs.quality as 'original'|'balanced'|'performance']||qualityPresets.original);
      if(prefs.reduced){documentEffect.clear();titleRoll.finish();counter.finish();}root.hidden=!!prefs.flat;
      if(prefs.reduced||prefs.flat)intro?.finish();
    };changeHandler=displayChange;window.addEventListener('lr-display-change',displayChange);displayChange();
    root.addEventListener('keydown',event=>{
      if(reader.active||intro?.active||(event.target as HTMLElement).closest('input,textarea,select,button,a,[contenteditable=true]')||settings.open)return;
      if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();shift(event.key==='ArrowDown'?1:-1);}
      if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();group.value=String(wrap(lane+(event.key==='ArrowRight'?1:-1),archiveColumns.length));group.dispatchEvent(new Event('change'));}
    });host.tabIndex=0;
    const replay=el('button','重播入场');replay.type='button';head.insertBefore(replay,fold);
    const play=(force=false)=>{
      if(reader.active||intro?.active||prefs.reduced||prefs.flat)return;
      if(body.hidden){body.hidden=false;fold.textContent='收起三维';fold.setAttribute('aria-expanded','true');}
      if(!force){try{if(sessionStorage.getItem('lr-boot-seen'))return;sessionStorage.setItem('lr-boot-seen','1');}catch{return;}}
      intro=new FullScreenIntro(host,scene!,()=>{scene?.endIntro(mode);});
    };replay.onclick=()=>play(true);
    if(boot.page_kind==='home'){try{if(prefs.reduced||prefs.flat)sessionStorage.setItem('lr-boot-seen','1');}catch{}play();}
  }catch(error){dispose();throw error;}
}
