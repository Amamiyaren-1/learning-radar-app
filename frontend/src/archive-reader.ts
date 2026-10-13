import {bindNativeForms,hasUnsavedFeedback} from './native-forms';
import {matchesNavigation} from './navigation-target';
import {sound} from './sound';
type Reading={node:HTMLElement;title:string;kind:string};
const ordinary=(e:MouseEvent,a:HTMLAnchorElement)=>e.button===0&&!e.ctrlKey&&!e.metaKey&&!e.shiftKey&&!e.altKey&&!a.download&&(!a.target||a.target==='_self');
export function archiveReader(source:HTMLElement,app:HTMLElement,body:HTMLElement,info:HTMLElement,mode:(detail:boolean)=>void,reduced:()=>boolean){
  const originalURL=location.href,originalTitle=document.title,originalPage=document.body.dataset.page;
  const session=crypto.randomUUID();let activeURL='',ticket=0,controller:AbortController|undefined,disposed=false,scrollY=0;
  let current:Reading|undefined;let currentURL='';let returning=false;
  const concealed=new Map<HTMLElement,boolean>();
  const pane=document.createElement('section');pane.className='lr-archive-reading';pane.hidden=true;pane.setAttribute('aria-label','档案阅读');body.append(pane);
  const bar=document.createElement('div');bar.className='lr-reader-bar';
  const back=document.createElement('button');back.type='button';back.textContent='← 返回原选择';
  back.dataset.lrSound='handled';
  const native=document.createElement('a');native.textContent='普通页面打开';bar.append(back,native);
  const status=document.createElement('p');status.className='lr-notice';status.setAttribute('role','status');
  const content=document.createElement('div');content.id='lr-reader-document';pane.append(bar,status,content);
  const marker=(url:string,depth=0)=>({lrArchive:{session,url,depth}});
  const cancel=()=>{ticket++;controller?.abort();controller=undefined;};
  function restore(){
    cancel();activeURL='';pane.hidden=true;info.hidden=false;body.classList.remove('lr-archive-reading-mode');
    for(const [node,hidden] of concealed)node.hidden=hidden;concealed.clear();
    document.title=originalTitle;document.body.dataset.page=originalPage;mode(false);returning=false;
    window.scrollTo(0,scrollY);
  }
  function show(reading:Reading,url:string){
    if(!activeURL){scrollY=window.scrollY;for(const child of source.children){
      if(child instanceof HTMLElement&&child!==app&&child.tagName!=='NAV'){concealed.set(child,child.hidden);child.hidden=true;}
    }}
    current=reading;currentURL=url;activeURL=url;returning=false;native.href=url;status.hidden=true;info.hidden=true;pane.hidden=false;
    body.classList.add('lr-archive-reading-mode');content.replaceChildren(reading.node);document.title=reading.title;document.body.dataset.page=reading.kind;
    app.scrollIntoView({block:'start',behavior:'instant'});pane.tabIndex=-1;pane.focus({preventScroll:true});
  }
  function isDocumentURL(url:URL){return url.origin===location.origin&&(url.pathname.startsWith('/report/')||(url.pathname==='/history'&&url.searchParams.has('project')));}
  async function open(urlString:string,push=true){
    const url=new URL(urlString,location.href);if(!isDocumentURL(url)||disposed)return false;
    // Native unload protection owns the decision when leaving unsaved feedback.
    if(hasUnsavedFeedback()&&current&&currentURL!==url.href){location.assign(url.href);return true;}
    cancel();const own=ticket;controller=new AbortController();const request=controller;
    const timeout=setTimeout(()=>request.abort(),10000);const started=performance.now();mode(true);status.textContent='正在读取已保存档案…';
    app.dataset.navigation='loading';
    try{
      const response=await fetch(url.href,{credentials:'same-origin',signal:request.signal,headers:{Accept:'text/html'}});
      const finalURL=new URL(response.url);if(finalURL.origin!==location.origin)throw new Error('Cross-origin navigation');
      if(!response.ok||!response.headers.get('content-type')?.includes('text/html'))throw new Error('Native error');
      const doc=new DOMParser().parseFromString(await response.text(),'text/html');
      const boot=JSON.parse(doc.querySelector('#lr-bootstrap')?.textContent||'null');
      const server=doc.querySelector<HTMLElement>('#lr-business');
      if(!matchesNavigation(boot,url,finalURL)||!server){
        if(own===ticket)location.assign(finalURL.href);return true;
      }
      if(own!==ticket||disposed)return true;
      // Scripts (including refresh/bootstrap), app mount and duplicate navigation
      // are never executed. All remaining form controls came from this GET.
      server.querySelectorAll('script,#lr-app,nav').forEach(n=>n.remove());server.removeAttribute('id');
      server.querySelectorAll('table').forEach(table=>{const wrap=document.createElement('div');wrap.className='lr-table-wrap';wrap.tabIndex=0;wrap.setAttribute('aria-label','报告表格');table.before(wrap);wrap.append(table);});
      bindNativeForms(server);
      const remaining=reduced()?0:Math.max(0,850-(performance.now()-started));
      if(remaining)await new Promise(resolve=>setTimeout(resolve,remaining));
      if(own!==ticket||disposed)return true;
      // Keep a dirty draft in its original DOM when returning to the same URL,
      // after validating the authenticated GET again. Never persist form data.
      const reading=current&&currentURL===url.href&&hasUnsavedFeedback()?current:{node:document.adoptNode(server),title:doc.title,kind:boot.page_kind};
      if(push){
        if(!history.state?.lrArchive||history.state.lrArchive.session!==session)history.replaceState({...history.state,...marker(originalURL)},'',originalURL);
        if(location.href!==url.href)history.pushState(marker(url.href,(history.state?.lrArchive?.depth||0)+1),'',url.href);
      }
      show(reading,url.href);app.dataset.navigation='ready';
    }catch{
      if(own===ticket&&!disposed){app.dataset.navigation='fallback';location.assign(url.href);}
    }finally{clearTimeout(timeout);if(controller===request)controller=undefined;}
    return true;
  }
  back.onclick=()=>{if(returning)return;returning=true;sound.play('back');history.go(-Math.max(1,history.state?.lrArchive?.depth||1));};
  const pop=()=>{
    cancel();const state=history.state?.lrArchive;
    if(state?.session!==session){location.reload();return;}
    if(location.href===originalURL)restore();else void open(location.href,false);
  };
  const click=(e:MouseEvent)=>{
    const a=(e.target as Element).closest<HTMLAnchorElement>('a');if(!a||!ordinary(e,a)||!isDocumentURL(new URL(a.href)))return;
    if(a===native)return;e.preventDefault();sound.play('open');void open(a.href);
  };
  pane.addEventListener('click',click);window.addEventListener('popstate',pop);
  return {open,cancel:()=>{const pending=!!controller;cancel();if(pending&&!activeURL)mode(false);},reveal(value:number){if(!activeURL)return;const gain=reduced()?1:value;content.style.opacity=String(gain);content.style.transform=`translateY(${(1-gain)*18}px)`;content.inert=gain<.1;},get active(){return !!activeURL;},get url(){return activeURL;},dispose(){disposed=true;cancel();window.removeEventListener('popstate',pop);pane.remove();}};
}
