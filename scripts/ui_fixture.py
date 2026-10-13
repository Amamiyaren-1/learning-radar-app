"""F4 local UI acceptance. Synthetic fixtures only; existing Python routes, no worker.

Run from this isolated worktree. Never point it at a production data directory.
Control flags below inject failures only into this fixture process, not application code.
"""
from pathlib import Path
import argparse,copy,json,sys,threading,time,ipaddress
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from learnradar.config import load_settings
from learnradar.tasks import TaskQueue
from learnradar.web import serve
from learnradar.ui_assets import AssetRegistry

ROOT=Path(__file__).resolve().parents[1]

def audit(event,args):
    if event=='socket.connect':
        address=args[1]
        if not isinstance(address,tuple) or not ipaddress.ip_address(address[0]).is_loopback:
            raise RuntimeError('fixture acceptance forbids external connections')
    if event.startswith('smtplib.'):
        raise RuntimeError('fixture acceptance forbids SMTP')

def seed(directory,count,*,state_samples=True):
    queue=TaskQueue(directory,demo=True)
    analysis=json.loads((ROOT/'tests/fixtures/offline_analysis.json').read_text(encoding='utf-8'))
    with queue.store() as store:
        if store.conn.execute('SELECT COUNT(*) FROM reports').fetchone()[0]:return queue
        for index in range(1,count+1):
            run=f'fixture-f4-{index:03}';repo=f'fixture-ui/project-{index:03}'
            payload=copy.deepcopy(analysis);payload['title']=f'fixture 档案 {index:03} / {repo} · 未实测'
            payload['target']['repo']=repo
            cases=' '.join(f'case-{n:03}' for n in (1,7,8,9,40,100) if index<=n)
            payload['target']['summary_one_line']='fixture 离线展示；未实测。 '+cases
            date=f'2026-10-09T{23-(index//60):02}:{59-index%60:02}:00+08:00'
            store.start_run(run,kind='fixture',started_at=date,snapshot={'topic':'agent-memory','fixture':True})
            store.finish_run(run,status='published',finished_at=date)
            report_path=directory/'reports'/f'{run}.md';report_path.parent.mkdir(parents=True,exist_ok=True)
            text=f'# {payload["title"]}\n\n全部为 fixture，不是真实搜索或模型输出，未实测。\n\n## 项目用途\n\n'+('\n\n'.join('fixture 学习档案用于检查长文阅读与换行。中文与 English 同时显示，所有状态取自隔离保存的示例。' for _ in range(12)))
            text+='\n\n## 表格与代码\n\n| 项目 | 内容 | 状态 |\n| --- | --- | --- |\n| fixture | 离线排版 | 未实测 |\n\n```python\nprint("fixture only")\n'+('x'*160)+'\n```\n\n- fixture 列表\n- 原生反馈仍绑定该运行\n'
            report_path.write_text(text,encoding='utf-8')
            store.save_report(run,report_id=run,created_at=date,status='published',title=payload['title'],markdown_path=str(report_path),html_path='',payload=payload,validation={'ok':True,'errors':[],'warnings':['fixture 内容未实测']})
            if index%3:
                store.save_candidates(run,[{'full_name':repo,'description':payload['target']['summary_one_line'],'topics':['fixture','group-a'] if index%3==1 else ['fixture','group-b']}],role='target')
                from learnradar.archive import archive_report
                archive_report(store.conn,store.conn.execute('SELECT * FROM reports WHERE run_id=?',(run,)).fetchone())
        if count:
            for kind in ('rejected','damaged'):
                run='fixture-f4-'+kind;payload=copy.deepcopy(analysis) if kind=='rejected' else {'target':{'repo':'fixture-ui/project-001'}}
                payload['target']['repo']='fixture-ui/project-001'
                store.start_run(run,kind='fixture',started_at='2026-10-08',snapshot={'topic':'agent-memory','fixture':True})
                store.finish_run(run,status='rejected',finished_at='2026-10-08')
                store.save_report(run,report_id=run,created_at='2026-10-08',status='rejected',title='fixture '+kind+' / 未实测',markdown_path='',html_path='',payload=payload,validation={'ok':False,'errors':['fixture 原拒绝原因 / 结构诊断'],'warnings':[]})
        for index,status in enumerate(('queued','running','waiting_quota','completed','failed','interrupted','cancelled') if state_samples else ()):
            identifier=queue._insert(store.conn,'agent-memory','fixture-state-'+status,options={'fixture':True},kind='manual_recommendation',run_id='fixture-state-'+status,budget_scope='manual',notify_mode='none',cycle_id='',ts='2026-10-08T12:00:00+08:00',day='2026-10-08')
            store.conn.execute('UPDATE tasks SET status=?,phase=?,detail=?,reserved_day=NULL WHERE task_id=?',(status,'analysis' if status=='running' else 'queued','fixture 状态样例；未实测，无执行进程',identifier))
        store.conn.commit()
    return queue

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--count',type=int,default=100);parser.add_argument('--port',type=int,default=0);parser.add_argument('--variant',choices=['normal','script-fail','model-fail','no-js','no-webgl'],default='normal')
    parser.add_argument('--session',default='ui-fixture-f4')
    parser.add_argument('--no-state-samples',action='store_true',help='B3: leave the queue empty for a real fixture worker')
    parser.add_argument('--assets',type=Path,help='isolated public build for same-machine before/after measurement')
    args=parser.parse_args();sys.addaudithook(audit)
    if not args.session.replace('-','').isalnum():parser.error('session must be a simple directory name')
    directory=ROOT/'data'/args.session/args.variant
    queue=seed(directory,args.count,state_samples=not args.no_state_samples)
    asset_root=(args.assets or ROOT/'frontend/dist').resolve()
    if not asset_root.is_relative_to(ROOT):parser.error('fixture assets must stay inside this worktree')
    assets=AssetRegistry(asset_root);original_read=assets.read
    if args.variant=='no-js':assets.scripts=[]
    def read(name):
        if args.variant=='script-fail' and name=='assets/app.js':return None
        if args.variant=='model-fail' and name.endswith('.glb'):return None
        if args.variant=='no-js' and name=='assets/app.js':return b'// fixture: script enhancement disabled', 'text/javascript'
        if args.variant=='no-webgl' and name=='assets/app.js':
            payload,mime=original_read(name)
            prefix=b'const originalGetContext=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return /webgl/i.test(type)?null:originalGetContext.call(this,type,...args)};\n'
            return prefix+payload,mime
        return AssetRegistry(asset_root).read(name)
    # serve closes over its registry; use its public fixture root loader during construction.
    from unittest.mock import patch
    assets.read=read
    with patch('learnradar.ui_assets.AssetRegistry',return_value=assets):
        server=serve(queue.path,queue=queue,settings=load_settings(local_overrides=False),port=args.port)
    native_create=server.auth.store.create_session
    control=directory/'control.json'
    fault={'fail':0,'delay':0};fault_lock=threading.Lock()
    original_get=server.RequestHandlerClass.do_GET
    def controlled_get(handler):
        if handler.path.startswith(('/history','/report/')):
            with fault_lock:
                fail=fault['fail']>0
                if fail:fault['fail']-=1
                delay=fault['delay']
            if delay:time.sleep(delay)
            if fail:
                (directory/'get-fault-observed.json').write_text(json.dumps({'fixture':True,'path':handler.path,'status':503,'time':time.time()}),encoding='utf-8')
                handler.send_error(503,'fixture GET failure');return
        original_get(handler)
    server.RequestHandlerClass.do_GET=controlled_get
    def controls():
        last=''
        while True:
            try:
                current=control.read_text(encoding='utf-8')
                if current!=last:
                    last=current;command=json.loads(current).get('action')
                    if command=='expire':
                        with queue.store() as store:store.conn.execute('UPDATE web_sessions SET expires=0');store.conn.commit()
                    elif command=='rotate-csrf':
                        with queue.store() as store:store.conn.execute("UPDATE web_sessions SET csrf='fixture-stale-csrf'");store.conn.commit()
                    elif command=='capacity':server.auth.store.create_session=lambda *a,**k:None
                    elif command=='get-fail':
                        with fault_lock:fault['fail']=1
                    elif command=='get-delay':
                        with fault_lock:fault['delay']=1.5
                    elif command=='reset':
                        with fault_lock:fault.update(fail=0,delay=0)
                        server.auth.store.create_session=native_create
                        server.auth.limiter.clock=lambda:time.time()+100000
            except (OSError,ValueError):pass
            time.sleep(.1)
    threading.Thread(target=controls,daemon=True).start()
    address=f'http://127.0.0.1:{server.server_port}'
    (directory/'public-state.json').write_text(json.dumps({'fixture':True,'url':address,'pid':__import__('os').getpid(),'count':args.count,'variant':args.variant}),encoding='utf-8')
    print(address,flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
