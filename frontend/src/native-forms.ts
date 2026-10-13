const bound=new WeakSet<HTMLFormElement>();
const dirty=new Set<HTMLFormElement>();
window.addEventListener('beforeunload',event=>{
  if([...dirty].some(form=>form.isConnected)){event.preventDefault();event.returnValue='';}
});
window.addEventListener('pageshow',()=>document.querySelectorAll('form[aria-busy]').forEach(form=>{
  form.removeAttribute('aria-busy');form.querySelector('.lr-status')?.remove();
}));
export const hasUnsavedFeedback=()=>[...dirty].some(form=>form.isConnected);
/** Attach behavior to server-rendered nodes. Never reconstruct a write form. */
export function bindNativeForms(root:HTMLElement){
  root.querySelectorAll<HTMLFormElement>('form[method=post]').forEach(form=>{
    if(bound.has(form))return;bound.add(form);
    form.addEventListener('submit',()=>{
      dirty.clear();form.setAttribute('aria-busy','true');
      const status=document.createElement('span');status.className='lr-status';status.textContent='正在提交…';status.setAttribute('role','status');form.append(status);
    });
    if(form.dataset.lrAction!=='feedback')return;
    const values=()=>['understood_what','understood_highlight','want_more','note'].map(name=>
      [...form.querySelectorAll<HTMLInputElement|HTMLTextAreaElement>(`[name="${name}"]`)].filter(c=>c.type!=='radio'||(c as HTMLInputElement).checked).map(c=>c.value));
    const initial=JSON.stringify(values());
    const changed=()=>{if(JSON.stringify(values())===initial)dirty.delete(form);else dirty.add(form);};
    form.addEventListener('input',changed);form.addEventListener('change',changed);
  });
}
