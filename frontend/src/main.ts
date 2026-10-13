import './enhancement.css';
import {bindNativeForms} from './native-forms';
import {sound,soundToggle} from './sound';

type Envelope={contract_version:string;page_kind:string;fixture:boolean;http_status:number;data:Record<string,any>;ui_resources:{available:boolean}};
const business=document.querySelector<HTMLElement>('#lr-business')!;
const app=document.querySelector<HTMLElement>('#lr-app')!;
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,text='',className='')=>{const el=document.createElement(tag);el.textContent=text;el.className=className;return el;};
function getPrefs(){try{return JSON.parse(localStorage.getItem('lr-display-v1')||'{}');}catch{return {};}}
const prefs={reduced:false,flat:false,quality:'balanced',...getPrefs()};
const motionMedia=matchMedia('(prefers-reduced-motion: reduce)');
let applicationReduced=!!prefs.reduced;
function syncReduced(){
  prefs.reduced=applicationReduced||motionMedia.matches;
  document.documentElement.classList.toggle('lr-reduced',!!prefs.reduced);
}
syncReduced();
motionMedia.addEventListener('change',()=>{syncReduced();window.dispatchEvent(new Event('lr-display-change'));});
// Only display preferences persist. No auth, report text or form values in browser storage.
function savePrefs(){try{localStorage.setItem('lr-display-v1',JSON.stringify({...prefs,reduced:applicationReduced}));}catch{}}
function settings(){
  const dialog=document.createElement('dialog');dialog.className='lr-dialog';dialog.setAttribute('aria-labelledby','lr-settings-title');
  const title=node('h2','显示与声音');title.id='lr-settings-title';dialog.append(title,node('p','设置只影响当前浏览器的显示。'));
  for(const [key,label] of [['reduced','减少动画'],['flat','只使用二维页面']] as const){
    const row=node('label',label);const input=document.createElement('input');input.type='checkbox';input.checked=key==='reduced'?applicationReduced:!!prefs[key];
    input.addEventListener('change',()=>{if(key==='reduced')applicationReduced=input.checked;else prefs[key]=input.checked;syncReduced();savePrefs();window.dispatchEvent(new Event('lr-display-change'));});row.prepend(input);dialog.append(row);
  }
  const soundRow=node('label','播放配乐与交互音效');const soundInput=document.createElement('input');soundInput.type='checkbox';soundInput.checked=sound.preferences.enabled;
  soundInput.onchange=()=>sound.setEnabled(soundInput.checked);window.addEventListener('lr-sound-change',()=>{soundInput.checked=sound.preferences.enabled;});soundRow.prepend(soundInput);dialog.append(soundRow);
  const quality=node('label','三维画质');const select=document.createElement('select');
  for(const [value,label] of [['performance','性能'],['balanced','均衡'],['original','参考画质']]){const opt=node('option',label);opt.value=value;select.append(opt);}
  select.value=prefs.quality;select.onchange=()=>{prefs.quality=select.value;savePrefs();window.dispatchEvent(new Event('lr-display-change'));};quality.append(select);dialog.append(quality);
  for(const [key,label] of [['musicVolume','配乐音量'],['effectsVolume','音效音量']] as const){const row=node('label',label);const range=document.createElement('input');range.type='range';range.min='0';range.max='100';range.value=String(sound.preferences[key]);range.oninput=()=>sound.setVolume(key,Number(range.value));window.addEventListener('lr-sound-change',()=>{range.value=String(sound.preferences[key]);});row.append(range);dialog.append(row);}
  dialog.append(node('p','浏览器限制自动播放时，首次点击或键盘操作会启动声音。关闭选择会保留。','muted'));
  dialog.append(node('p','模型仅用于展示；任务、报告和通知以页面原状态为准。','muted'),node('p','MiSans © 小米 · 参考程序 © 2026 LBEILC (MIT) · Three.js / Rolling Number (MIT)','muted'));
  const close=node('button','返回页面');close.type='button';close.onclick=()=>dialog.close();dialog.append(close);document.body.append(dialog);
  const trigger=node('button','显示与声音','lr-settings-trigger');trigger.type='button';trigger.onclick=()=>dialog.showModal();
  business.querySelector('nav')?.append(trigger);
  return dialog;
}

async function enhance(){
  const raw=document.querySelector('#lr-bootstrap')?.textContent;
  if(!raw)return;
  const boot:Envelope=JSON.parse(raw);
  if(boot.contract_version!=='1')throw new Error('Unsupported UI contract');
  document.body.dataset.page=boot.page_kind;
  document.body.dataset.httpStatus=String(boot.http_status);
  document.documentElement.classList.toggle('lr-reduced',!!prefs.reduced);
  document.querySelector('.lr-topline')?.append(soundToggle());sound.start(!!boot.fixture);
  const isLogin=!!business.querySelector('form[data-lr-action=login]');
  if(boot.page_kind==='login'||isLogin){
    try{sessionStorage.removeItem('lr-selection');sessionStorage.removeItem('lr-boot-seen');}catch{}
    const form=business.querySelector<HTMLFormElement>('form[data-lr-action=login]');
    if(form){try{const {mountLoginVisual}=await import('./login-visual');mountLoginVisual(form,prefs);}catch{/* Static SVG and native form remain usable. */}}
    const error=form?.previousElementSibling;
    if(boot.http_status>=400&&error?.tagName==='P'&&!error.querySelector('strong')){error.id='lr-login-error';error.setAttribute('role','alert');form?.setAttribute('aria-describedby','lr-login-error');}
    if(boot.fixture&&new URLSearchParams(location.search).has('ui_probe')){
      const result=node('pre','','lr-probe');result.id='lr-login-probe';result.hidden=true;document.querySelector('.lr-footer')?.before(result);
      const report=()=>{const visual=document.querySelector<HTMLElement>('.lr-login-visual');result.textContent=JSON.stringify({fixture:true,date:new Date().toISOString(),viewport:[innerWidth,innerHeight],dpr:devicePixelRatio,userAgent:navigator.userAgent,reduced:!!prefs.reduced,systemReduced:matchMedia('(prefers-reduced-motion:reduce)').matches,visibility:document.visibilityState,motion:visual?.dataset.motion,motionFrame:visual?.dataset.motionFrame,paused:visual?.dataset.paused,ribs:visual?.querySelectorAll('[data-rib]').length,svgNodes:document.querySelectorAll('.lr-login-diagram svg *').length,overflow:document.documentElement.scrollWidth>innerWidth,canvas:document.querySelectorAll('canvas').length,form:{method:form?.method,action:form?.getAttribute('action'),description:form?.getAttribute('aria-describedby'),inputs:[...(form?.querySelectorAll('input')||[])].map(i=>({name:i.name,type:i.type,required:i.required,autocomplete:i.autocomplete,maxLength:i.maxLength}))},fonts:[...document.fonts].map(f=>({family:f.family,weight:f.weight,status:f.status})),resources:performance.getEntriesByType('resource').map(r=>r.name.split('/static/ui/')[1]).filter(Boolean)});};
      void document.fonts.ready.then(report);form?.addEventListener('focusin',()=>queueMicrotask(report));form?.addEventListener('focusout',()=>queueMicrotask(report));document.addEventListener('visibilitychange',report);window.addEventListener('resize',report);
    }
    return;
  }
  const dialog=settings();
  business.querySelectorAll<HTMLAnchorElement>('nav a').forEach(a=>{if(a.pathname===location.pathname&&((a.search===''&&!location.search)||a.search===location.search))a.setAttribute('aria-current','page');});
  bindNativeForms(business);
  business.querySelector('[data-lr-action=logout]')?.addEventListener('submit',()=>{try{sessionStorage.removeItem('lr-selection');sessionStorage.removeItem('lr-boot-seen');}catch{}});
  const entries=[...business.querySelectorAll<HTMLElement>('[data-lr-role=project],[data-lr-role=report]')];
  entries.forEach((el,i)=>{el.dataset.displayIndex=String(i+1);});
  business.querySelectorAll('article table').forEach(table=>{const wrap=node('div','','lr-table-wrap');wrap.tabIndex=0;wrap.setAttribute('aria-label','报告表格');table.before(wrap);wrap.append(table);});
  if(boot.http_status>=400){
    business.classList.add('lr-error-page');
    const help=node('p','重新载入本站页面取得当前表单；额度或限流请等待后再操作。','lr-notice');
    const home=node('a','返回首页');home.href='/';const login=node('a','重新登录');login.href='/login';help.append(document.createElement('br'),home,document.createTextNode('　'),login);business.append(help);
  }
  if(boot.page_kind==='task'){
    const task=boot.data.task||{};
    if(['queued','running','waiting_quota'].includes(task.status)){
      const status=node('p','此页每两秒更新。执行状态、报告结果与通知分别记录。','lr-task-phase');business.querySelector('h1')?.after(status);
      let scroll=0;try{scroll=Number(sessionStorage.getItem('lr-task-scroll:'+task.task_id)||0);}catch{}
      if(scroll)requestAnimationFrame(()=>window.scrollTo(0,scroll));
      window.addEventListener('pagehide',()=>{try{sessionStorage.setItem('lr-task-scroll:'+task.task_id,String(window.scrollY));}catch{}});
    }
    return; // Keep original reload script; never restart intro or create a write form.
  }
  if(boot.page_kind==='projects'&&entries.length===0){const link=node('a','帮我发现新项目');link.href='/';business.append(link);}
  if(!['home','projects','project','report','legacy_history'].includes(boot.page_kind))return;
  if(boot.page_kind==='home') business.querySelector('.card:has([data-lr-action=submit])')?.after(app);
  else if(boot.page_kind==='projects')business.querySelector('form[data-lr-action=search]')?.after(app);
  else business.querySelector('h1')?.after(app);
  if(!boot.ui_resources?.available)return;
  app.hidden=false;
  const notice=node('p','正在准备档案展示；下方原生内容可以直接使用。','lr-notice');app.append(notice);
  let mounting=false,mounted=false;
  async function mountWhenEnabled(){
    document.documentElement.classList.toggle('lr-reduced',!!prefs.reduced);
    if(prefs.flat){notice.textContent='二维模式 · 所有记录和操作仍在下方页面。';if(!notice.isConnected)app.prepend(notice);return;}
    if(mounted){notice.remove();return;}
    if(mounting)return;
    mounting=true;notice.textContent='正在准备档案展示；下方原生内容可以直接使用。';
    try{
      const {mountArchive}=await import('./archive');
      await mountArchive(app,business,boot,prefs,dialog);
      mounted=true;
      if(!prefs.flat)notice.remove();else notice.textContent='二维模式 · 所有记录和操作仍在下方页面。';
    }catch{
      notice.textContent='三维展示暂不可用。下方同主题列表、正文与原生操作仍可使用。';
      app.querySelector('.lr-archive')?.remove();
      business.querySelectorAll('.document-redaction-window').forEach(el=>el.remove());
      business.querySelectorAll('.document-redacted').forEach(el=>el.classList.remove('document-redacted'));
    }finally{mounting=false;}
  }
  window.addEventListener('lr-display-change',()=>{void mountWhenEnabled();});
  await mountWhenEnabled();
}
void enhance().catch(()=>{app.hidden=true;business.hidden=false;});
