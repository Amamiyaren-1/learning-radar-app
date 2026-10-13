"""Windows daily formal web/worker lifecycle; no task submission or mail retry."""
import argparse
import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
import webbrowser

from .auth_store import AuthStore
from .config import ConfigError, load_settings
from .demo_launcher import _handle, owned_alive, save
from .tasks import TaskError, TaskQueue, WorkerLock

ROOT = Path(__file__).resolve().parents[1]


def runtime_settings(config_root):
    original = load_settings(Path(config_root))
    from .notify import email_config_path
    os.environ['LEARNRADAR_EMAIL_CONFIG'] = str(email_config_path(original))
    original_web=original.root/'config/web.toml'
    if original_web.is_file() and not os.environ.get('LEARNRADAR_WEB_CONFIG'):
        os.environ['LEARNRADAR_WEB_CONFIG']=str(original_web)
    return dataclasses.replace(original, root=ROOT, prompts_dir=ROOT/'prompts',
        app={**original.app, 'data_dir':str(original.data_dir)})


def pending_work(queue):
    return [{'task_id':r['task_id'],'status':r['status'],'notify_pending':bool(r['notify_pending'])}
            for r in queue.list() if r['status'] in ('queued','running','waiting_quota') or r['notify_pending']]


def check_start(queue, allowed=()):
    pending = pending_work(queue)
    denied = [r for r in pending if r['notify_pending'] or r['status']=='running' or r['task_id'] not in allowed]
    if denied:
        raise TaskError('未领取旧任务或通知：'+json.dumps(denied,ensure_ascii=False)+
                        '；先评审。仅明确授权的排队任务可用--allow-task，通知重试不由启动器授权。')
    if (queue.directory/'locks/weekly.lock').exists():
        raise TaskError('旧weekly锁仍存在；未猜测执行者已退出，未启动worker。')


def account_required(settings):
    database=settings.data_dir/'learning-radar.sqlite'
    if not database.is_file():
        raise ConfigError('正式数据库不存在；本启动器不初始化数据或账号。')
    account=AuthStore(database).account()
    if not account or account['fixture']:
        raise ConfigError('缺少正式网页账号或误用fixture账号；本启动器不初始化密码。')


def child(args, settings, queue):
    stop=Path(args.stop_file);ready=Path(args.ready_file)
    if args.action=='worker':
        check_start(queue,args.allow_task)
        from .worker import run_worker
        def on_ready():
            check_start(queue,args.allow_task)
            save(ready,{'pid':os.getpid(),'role':'worker'})
        return run_worker(settings,queue,on_ready=on_ready,stop_requested=stop.exists)
    from .web import serve
    from .web_config import load_web_config
    server=serve(queue.path,settings=settings,queue=queue,port=args.port,
                 web_config=load_web_config(settings,mode='loopback'))
    def watch():
        while not stop.exists():time.sleep(.2)
        server.shutdown()
    threading.Thread(target=watch,daemon=True).start()
    save(ready,{'pid':os.getpid(),'role':'web','url':'http://127.0.0.1:'+str(server.server_port)})
    try:server.serve_forever()
    finally:server.server_close()
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('start','status','stop','web','worker'),nargs='?',default='start')
    parser.add_argument('--config-root',type=Path,default=ROOT)
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--web-only',action='store_true')
    parser.add_argument('--allow-task',action='append',default=[])
    parser.add_argument('--open-browser',action='store_true')
    parser.add_argument('--ready-file',help=argparse.SUPPRESS)
    parser.add_argument('--stop-file',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    created=[];state=None;path=None;lock=None
    try:
        if os.name!='nt':raise ConfigError('此日常启动器仅支持Windows；Linux使用统一task_cli及已评审service。')
        settings=runtime_settings(args.config_root)
        if args.action not in ('status','stop'):account_required(settings)
        queue=TaskQueue(settings.data_dir,demo=False,daily_limit=settings.limit('manual_tasks_per_day',3))
        if args.action in ('web','worker'):
            if not args.ready_file or not args.stop_file:raise TaskError('子进程必须由本启动器创建。')
            return child(args,settings,queue)
        folder=settings.data_dir/'runtime-launcher';folder.mkdir(parents=True,exist_ok=True)
        lock=WorkerLock(settings.data_dir);lock.path=settings.data_dir/'locks/runtime-launcher.lock'
        if not lock.acquire():raise TaskError('另一次启动/停止正在处理。')
        path=folder/'state.json'
        state=json.loads(path.read_text()) if path.exists() else {
            'mode':'formal','source_root':str(ROOT),'config_root':str(args.config_root.resolve()),
            'directory':str(settings.data_dir),'processes':[],'url':''}
        if (state.get('mode')!='formal' or state.get('source_root')!=str(ROOT)
                or state.get('config_root')!=str(args.config_root.resolve()) or state.get('directory')!=str(settings.data_dir)):
            raise TaskError('所有权记录与源码/配置/数据目录不一致；未操作已有进程。')
        records=state.get('processes')
        if not isinstance(records,list):raise TaskError('进程记录损坏。')
        for r in records:
            if (r.get('role') not in ('web','worker') or not isinstance(r.get('pid'),int)
                    or r['pid']<=0 or not str(r.get('birth','')).isdigit()
                    or not Path(r.get('stop_file','')).resolve().is_relative_to(folder.resolve())):
                raise TaskError('进程所有权记录无效；未操作进程。')
        alive={r['role']:r for r in records if owned_alive(r)}
        if args.action=='status':
            print(json.dumps({'formal':True,'url':state['url'] if 'web' in alive else '',
                'running':sorted(alive),'pending':pending_work(queue)},ensure_ascii=False));return 0
        if args.action=='stop':
            # Graceful requests are unique per launch. Finish current business
            # and notification once, then exit; never terminate an in-flight API.
            for r in alive.values():Path(r['stop_file']).write_text('stop',encoding='utf-8')
            deadline=time.monotonic()+10
            while any(owned_alive(r) for r in alive.values()) and time.monotonic()<deadline:time.sleep(.1)
            state['processes']=[r for r in alive.values() if owned_alive(r)];save(path,state)
            print(json.dumps({'stop_requested':True,'still_finishing':[r['role'] for r in state['processes']],
                              'data_and_budget_retained':True}));return 2 if state['processes'] else 0
        if not args.web_only and 'worker' not in alive:
            check_start(queue,args.allow_task)
            probe=WorkerLock(settings.data_dir)
            if not probe.acquire():raise TaskError('已有统一执行者持有内核锁；未启动重复worker，也不接管它。')
            probe.release()
        state['processes']=list(alive.values());save(path,state)
        for role in ('web',) if args.web_only else ('web','worker'):
            if role in alive:continue
            nonce=role+'-'+uuid.uuid4().hex
            ready=folder/(nonce+'.ready.json');stop=folder/(nonce+'.stop');log=folder/(nonce+'.log')
            command=[sys.executable,'-m','learnradar.runtime_launcher',role,'--config-root',str(args.config_root.resolve()),
                     '--port',str(args.port),'--ready-file',str(ready),'--stop-file',str(stop)]
            for identifier in args.allow_task:command+=['--allow-task',identifier]
            with log.open('w',encoding='utf-8') as stream:
                process=subprocess.Popen(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,
                    env={**os.environ,'PYTHONIOENCODING':'utf-8','NO_PROXY':'127.0.0.1,localhost'},
                    creationflags=subprocess.CREATE_NO_WINDOW)
            value=_handle(process.pid)
            if not value:raise TaskError('自管子进程退出；请查看仅本机日志。')
            kernel,handle,birth=value;kernel.CloseHandle(handle)
            record={'role':role,'pid':process.pid,'birth':birth,'stop_file':str(stop),'log':str(log)}
            created.append((process,record));state['processes'].append(record);save(path,state)
            deadline=time.monotonic()+20
            while not ready.is_file() and time.monotonic()<deadline:
                if process.poll() is not None:raise TaskError(role+'未就绪；请查看仅本机日志。')
                time.sleep(.1)
            if not ready.is_file():raise TaskError(role+'就绪超时。')
            info=json.loads(ready.read_text());
            if info.get('pid')!=process.pid:raise TaskError('就绪身份不匹配。')
            if role=='web':state['url']=info['url'];save(path,state)
        print(json.dumps({'formal':True,'url':state['url'],'running':[r['role'] for r in state['processes']],
                          'tasks_submitted':0,'notification_retries_requested':0},ensure_ascii=False))
        if args.open_browser:webbrowser.open(state['url'])
        return 0
    except (TaskError,ConfigError,OSError,ValueError,KeyError,TypeError) as exc:
        for process,r in created:
            Path(r['stop_file']).write_text('stop',encoding='utf-8')
        print('正式启动器未完成：'+str(exc),flush=True);return 1
    finally:
        if lock:lock.release()


if __name__=='__main__':raise SystemExit(main())
