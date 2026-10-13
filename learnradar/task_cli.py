"""2.1 本机网页/执行进程入口；演示必须显式指定隔离目录。"""
import argparse
import dataclasses
import ipaddress
from pathlib import Path

from .config import load_settings, ConfigError
from .tasks import TaskError, TaskQueue


def main(argv=None):
    parser=argparse.ArgumentParser(description="Learning Radar 2.1 本机持久任务队列")
    parser.add_argument("command",choices=("web","worker"))
    parser.add_argument("--data-dir",type=Path)
    parser.add_argument("--demo",action="store_true")
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--web-mode", choices=("loopback", "https"), help="显式回环开发模式或已配置 HTTPS 代理模式")
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--ready-file",type=Path,help=argparse.SUPPRESS)
    parser.add_argument("--poll-seconds",type=float,default=1)
    parser.add_argument("--demo-delay",type=float,default=1)
    parser.add_argument("--demo-scenario",choices=("success","rejected","model-failed","mail-failed","mail-unknown"),default="success")
    args=parser.parse_args(argv)
    if args.ready_file and (not args.demo or args.command!="worker"):
        parser.error("就绪文件仅用于演示 worker 启动器")
    settings=load_settings(local_overrides=not args.demo)
    if args.demo and not args.data_dir:
        parser.error("演示必须显式指定 --data-dir；网页与 worker 使用同一隔离目录")
    directory=(args.data_dir or settings.data_dir).resolve()
    if args.demo and directory==settings.data_dir.resolve():
        parser.error("演示目录不能是正式 data_dir")
    if args.demo:
        from .demo import demo_settings
        settings=demo_settings(settings,directory)
    else:
        settings=dataclasses.replace(settings,app={**settings.app,"data_dir":str(directory)})
    try:
        queue=TaskQueue(directory,demo=args.demo,daily_limit=settings.limit("manual_tasks_per_day",3))
        if args.command=="worker":
            from .worker import run_worker
            def ready():
                import json, os
                args.ready_file.write_text(json.dumps({"ready":True,"pid":os.getpid()}),encoding="utf-8")
            return run_worker(settings,queue,once=args.once,poll_seconds=max(.1,args.poll_seconds),
                demo_delay=max(0,args.demo_delay),scenario=args.demo_scenario,on_ready=ready if args.ready_file else None)
        host="127.0.0.1" if args.host=="localhost" else args.host
        if not ipaddress.ip_address(host).is_loopback:
            parser.error("2.1 只允许本机回环地址；公网访问留到登录与 HTTPS 完成后")
        from .web import serve
        from .web_config import load_web_config
        web_config = load_web_config(settings, mode=args.web_mode, demo=args.demo)
        try:
            server=serve(queue.path,host=host,port=args.port,settings=settings,queue=queue,web_config=web_config)
        except OSError as exc:
            code=getattr(exc,"winerror",None) or exc.errno
            reason={10013:"系统拒绝绑定；可能涉及端口保留或安全策略，需结合本机检查确认",
                    10048:"地址或端口已被占用",98:"地址或端口已被占用"}.get(code,str(exc))
            print(f"网页未启动：{host}:{args.port} 绑定失败（错误 {code}）：{reason}",flush=True)
            print(f'替代命令：python -m learnradar.task_cli web {"--demo " if args.demo else ""}--data-dir "{directory}" --port 0',flush=True)
            print("--port 0 由系统分配端口，请使用启动后打印的实际地址；保留原数据目录。",flush=True)
            return 1
        print(f"{'fixture 演示模式' if args.demo else '本机正式模式'} http://{host}:{server.server_port} 数据目录={directory}",flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    except (TaskError, ConfigError) as exc:
        print(str(exc),flush=True)
        return 1
    except KeyboardInterrupt:
        print("已停止；待执行任务与保存结果保留",flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
