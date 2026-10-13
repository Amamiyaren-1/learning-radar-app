"""Windows 演示启动器：只管理自己记录的 PID + 内核创建时间，不按端口/进程名停止。"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser

from .config import load_settings
from .tasks import TaskError, TaskQueue, WorkerLock

ROOT=Path(__file__).resolve().parents[1]


def _handle(pid, *, terminate=False):
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetProcessTimes.argtypes=(wintypes.HANDLE,*(ctypes.POINTER(wintypes.FILETIME),)*4)
    kernel.GetExitCodeProcess.argtypes=(wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD))
    kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
    kernel.TerminateProcess.argtypes=(wintypes.HANDLE,wintypes.UINT)
    kernel.WaitForSingleObject.argtypes=(wintypes.HANDLE,wintypes.DWORD)
    handle=kernel.OpenProcess(0x1000 | 0x100000 | (1 if terminate else 0),False,pid)
    if not handle: return None
    times=[wintypes.FILETIME() for _ in range(4)]
    code=wintypes.DWORD()
    if not kernel.GetProcessTimes(handle,*(ctypes.byref(t) for t in times)) or not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259:
        kernel.CloseHandle(handle)
        return None
    birth=str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
    return kernel,handle,birth


def owned_alive(record):
    value=_handle(int(record["pid"]))
    if not value: return False
    kernel,handle,birth=value
    kernel.CloseHandle(handle)
    return birth==record["birth"]


def stop_owned(record):
    value=_handle(int(record["pid"]),terminate=True)
    if not value: return False
    kernel,handle,birth=value
    try:
        if birth!=record["birth"]: return False
        if not kernel.TerminateProcess(handle,130): raise OSError(ctypes.get_last_error(),"无法停止启动器进程")
        if kernel.WaitForSingleObject(handle,10000)!=0: raise OSError("等待启动器进程停止超时")
        return True
    finally: kernel.CloseHandle(handle)


def save(path,state):
    temporary=path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    parser=argparse.ArgumentParser(description="仅离线演示；不提交任务，不重跑分析")
    parser.add_argument("action",choices=("start","stop","status"),nargs="?",default="start")
    parser.add_argument("--data-dir",type=Path,default=ROOT/"data"/"demo-2-1")
    parser.add_argument("--open-browser",action="store_true")
    parser.add_argument("--demo-delay",type=float,default=1)
    args=parser.parse_args(argv)
    if os.name!="nt":
        print("此 PowerShell 启动器仅支持 Windows；其他平台请分别运行 task_cli web/worker。")
        return 1
    directory=args.data_dir.resolve()
    if directory==load_settings(local_overrides=False).data_dir.resolve():
        print("拒绝正式数据目录；启动器只支持显式隔离演示目录。")
        return 1
    folder=directory/"launcher"
    lock=WorkerLock(directory)
    lock.path=directory/"locks"/"demo-launcher.lock"
    if not lock.acquire():
        print("另一次启动/停止正在处理；未启动重复进程，请稍后再试。")
        return 1
    created=[]
    try:
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/"state.json"
        state=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"mode":"demo","directory":str(directory),"processes":[],"url":""}
        if not isinstance(state,dict) or state.get("mode")!="demo" or state.get("directory")!=str(directory):
            raise TaskError("启动器记录与演示目录不一致；未操作进程，请查看 state.json")
        records=state.get("processes")
        if not isinstance(records,list) or any(not isinstance(p,dict) or p.get("role") not in ("web","worker") or not isinstance(p.get("pid"),int) or p["pid"]<=0 or not isinstance(p.get("birth"),str) or not p["birth"].isdigit() for p in records):
            raise TaskError("启动器进程记录损坏；未操作进程，请查看 state.json")
        if args.action=="stop":
            for record in state["processes"]:
                stopped=stop_owned(record)
                print(f"{record['role']}：{'已停止' if stopped else '已退出或身份不符，未操作'}")
            state["processes"]=[]
            save(path,state)
            print(f"演示数据与额度保留：{directory}；中断任务在下次启动时按保存状态恢复，不自动重跑。")
            return 0
        alive={p["role"]:p for p in state["processes"] if owned_alive(p)}
        if args.action=="status":
            print(f"网页：{'运行中' if 'web' in alive else '未运行'}；执行进程：{'运行中' if 'worker' in alive else '未运行'}")
            if "web" in alive: print(state["url"])
            print(f"日志：{folder}")
            return 0
        TaskQueue(directory,demo=True)  # 模式检查，不提交任务；保留已有数据库与额度。
        state["processes"]=list(alive.values())
        save(path,state)
        for role in ("web","worker"):
            if role in alive: continue
            log=folder/f"{role}-{uuid.uuid4().hex}.log"
            ready=log.with_suffix(".ready.json")
            flags=["--port","0"] if role=="web" else ["--demo-delay",str(max(0,args.demo_delay)),"--ready-file",str(ready)]
            with log.open("w",encoding="utf-8") as stream:
                process=subprocess.Popen([sys.executable,"-m","learnradar.task_cli",role,"--demo","--data-dir",str(directory),*flags],
                    cwd=ROOT,env={**os.environ,"PYTHONIOENCODING":"utf-8","NO_PROXY":"127.0.0.1,localhost"},
                    stdout=stream,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            created.append(process)
            value=_handle(process.pid)
            if not value: raise TaskError(f"{role} 启动后立即退出；日志：{log}")
            kernel,handle,birth=value;kernel.CloseHandle(handle)
            state["processes"].append({"role":role,"pid":process.pid,"birth":birth,"log":str(log)})
            save(path,state)  # 每个新进程立即记录，启动器退出不会失去所有权信息。
            deadline=time.monotonic()+15
            if role=="web":
                while time.monotonic()<deadline:
                    if process.poll() is not None: raise TaskError(f"网页启动失败，退出码 {process.returncode}；日志：{log}")
                    match=re.search(r"http://127\.0\.0\.1:\d+",log.read_text(encoding="utf-8"))
                    if match:
                        url=match[0]
                        try:
                            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                            with opener.open(url+"/health",timeout=2) as response:
                                if response.status==200: break
                        except OSError: pass
                    time.sleep(.1)
                else: raise TaskError(f"网页未在期限内就绪；日志：{log}")
                state["url"]=url;save(path,state)
            else:
                while time.monotonic()<deadline:
                    if process.poll() is not None:
                        raise TaskError(f"worker 未持续运行（可能已有其他执行者持锁），退出码 {process.returncode}；日志：{log}")
                    if ready.exists() and json.loads(ready.read_text(encoding="utf-8")).get("pid")==process.pid: break
                    time.sleep(.1)
                else: raise TaskError(f"worker 未取得锁或未就绪；日志：{log}")
        print(f"fixture 离线演示：{state['url']}")
        print(f"数据/额度保留：{directory}\n日志：{folder}")
        print('停止：.\\scripts\\demo.ps1 -Action stop（使用同一 -DataDir）')
        if args.open_browser: webbrowser.open(state["url"])
        return 0
    except (TaskError,OSError,ValueError,KeyError) as exc:
        for process in created:
            if process.poll() is None:
                process.terminate();process.wait(timeout=10)  # Popen 自己的内核句柄，绝不按端口/进程名停止。
        print(f"演示启动器未完成：{exc}\n日志与所有权记录：{folder}；数据与额度未删除或重置。")
        return 1
    finally: lock.release()


if __name__=="__main__":
    raise SystemExit(main())
