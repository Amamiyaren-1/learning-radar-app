"""独立于网页生命周期的执行进程；网页、CLI、weekly、replay 共用系统锁与持久预算。"""
import argparse
import uuid

from .cloud import _send_saved_or_new, sanitize_error
from .config import ConfigError
from .net import now_iso
from .pipeline import RecommendationInput, RunHooks, classify_recommendation_result, execute_recommendation
from .tasks import QuotaWait, TaskBudget, WorkerLock


def notify_task(settings, queue, task, services=None, *, retry=False):
    args=argparse.Namespace(run_id=task["run_id"],email_id=None,cycle=task.get("cycle_id") or None,force=False)
    def before_send(store,email_id):
        # 进程可能在 SMTP 服务器接受后消失，必须预先保存不确定状态。
        store.update_email(email_id,status="unknown",error="发送尝试已开始，结果尚未确认",now=now_iso(),counted_attempt=False)
    try:
        _send_saved_or_new(settings,args,retry=retry,
            config_loader=services.email_config if services else None,
            sender=services.sender if services else None,before_send=before_send)
        with queue.store() as store:
            if not store.latest_email_for_run(task["run_id"]):
                store.create_email(task["run_id"],cycle_id=task.get("cycle_id", ""),run_id=task["run_id"],kind="failure",recipient="",subject="",body="",status="failed",now=now_iso())
                store.update_email(task["run_id"],status="failed",error="通知配置不完整，未发送；补齐后仅重试通知",now=now_iso(),counted_attempt=False)
    except Exception as exc:
        with queue.store() as store:
            email=store.latest_email_for_run(task["run_id"])
            if email:
                # before_send 已持久化 unknown，异常时不得改成可自动重发。
                if email["status"]!="unknown":
                    store.update_email(email["email_id"],status="failed",error=sanitize_error(exc,settings),now=now_iso(),counted_attempt=False)
            else:
                store.create_email(task["run_id"],cycle_id=task.get("cycle_id", ""),run_id=task["run_id"],kind="failure",recipient="",subject="",body="",status="failed",now=now_iso())
                store.update_email(task["run_id"],status="failed",error=sanitize_error(exc,settings),now=now_iso(),counted_attempt=False)


def mark_email_started(store,email_id):
    store.update_email(email_id,status="unknown",error="发送尝试已开始，结果尚未确认",now=now_iso(),counted_attempt=False)


def task_exit_code(task):
    if task["status"] in {"failed","interrupted","cancelled"}:
        return task.get("exit_code") or 2
    if task["status"]!="completed":
        raise RuntimeError("任务尚未处理完，不能返回成功")
    if task["notify_mode"]=="weekly":
        email=task.get("email") or {}
        if email.get("status")!="sent" or email.get("kind")=="failure":
            return 2
        return 0  # 明确失败的通知单独重试成功后，不沿用原发送失败退出码。
    return task["exit_code"] if task["exit_code"] is not None else 2


def work_once(settings,queue,token,*,demo_delay=1,scenario="success"):
    from .demo import DemoServices
    from .cycle import TaskTimeout, schedule_config
    from .cloud import _finish_weekly, _handle_unexpected_error
    task=queue.claim_email_retry()
    if task:
        services=DemoServices(settings,task["run_id"],delay=demo_delay,scenario=scenario) if queue.demo else None
        queue.stage(task["task_id"],"notification")
        retry=bool((task.get("email") or {}).get("body"))
        notify_task(settings,queue,task,services,retry=retry)
        return True
    task=queue.claim(token)
    if not task:
        return False
    identifier=task["task_id"]
    options=task["input"]
    services=DemoServices(settings,task["run_id"],delay=options.get("_fixture_delay",demo_delay),scenario=options.get("_fixture_scenario",scenario)) if queue.demo else None
    if services and task["kind"]=="replay_analysis":
        # 演示重放仍只读所选隔离运行的证据；假响应里的项目名对应原材料。
        from .cli import _load_evidence_for_run, _repos_in_evidence
        evidence,_=_load_evidence_for_run(settings,options["run"],settings.data_dir/"runs"/options["run"])
        owners=_repos_in_evidence(evidence)
        if len(owners)>=3:
            import json
            raw=json.dumps(services.report,ensure_ascii=False)
            for old,new in zip(services.names,owners): raw=raw.replace(old,new)
            services.report=json.loads(raw)
    hooks=RunHooks(stage=lambda stage:queue.stage(identifier,stage),
                   budget=TaskBudget(queue,identifier,settings.limit("max_model_calls_per_run",4)))
    if services:
        hooks.client_factory=services.client
        hooks.collector=services.collector
        hooks.adapter_factory=services.adapter
        hooks.profile_loader=services.profile
    try:
        if not queue.demo:
            roles=("analysis",) if task["kind"]=="replay_analysis" else tuple(
                role for role in ("screening","analysis")
                if not (role=="screening" and options.get("screening_from"))
                and not (role=="analysis" and options.get("screening_only")))
            for role in roles:
                model=settings.models[options["model"]] if role=="analysis" and options.get("model") else settings.model_for(role)
                if model.is_fixture:
                    if role=="analysis" and options.get("model"):
                        continue  # CLI 显式指定原有 offline 候选；不做静默回退。
                    raise ConfigError("正式队列不接受假模型；请显式使用 --demo 和隔离数据目录")
                if model.follow_redirects:
                    raise ConfigError("统一队列模型请求必须关闭重定向，请配置最终端点；每次持久记账只能发送一次请求")
                if not model.api_key():
                    raise ConfigError(f"{role} 缺少模型凭据；未请求模型，不切换假数据")
        result=execute_recommendation(settings,RecommendationInput(task["topic"],task["run_id"],options,task["kind"]),hooks)
    except QuotaWait:
        return True
    except BaseException as exc:
        interrupted=isinstance(exc,(KeyboardInterrupt,SystemExit,TaskTimeout))
        status="interrupted" if interrupted else "failed"
        code=2 if interrupted else 1
        with queue.store() as store:
            if not store.conn.execute("SELECT 1 FROM runs WHERE run_id=?",(task["run_id"],)).fetchone():
                store.start_run(task["run_id"],kind=task["kind"],started_at=now_iso(),snapshot={"task_id":identifier})
            store.conn.execute("UPDATE runs SET status=?,finished_at=?,notes=? WHERE run_id=? AND status='running'",
                (status,now_iso(),sanitize_error(exc,settings),task["run_id"]))
            store.conn.commit()  # 通知读取运行终态；任务仍处于处理中，直到通知结束。
            if task["cycle_id"]:
                if interrupted:
                    store.save_cycle(task["cycle_id"],status="interrupted",run_id=task["run_id"],finished_at=now_iso(),notes="执行中断；未自动重跑或重发")
                else:
                    code=_handle_unexpected_error(settings,store,task["cycle_id"],task["run_id"],exc)
        if task["notify_mode"]=="manual" and not interrupted:
            queue.stage(identifier,"notification")
            notify_task(settings,queue,task,services)
        queue.finish(identifier,status,sanitize_error(exc,settings),exit_code=code)
        if interrupted:
            raise
        return True
    code=result.get("exit_code",2)
    queue.save_exit_code(identifier,code)
    processing=classify_recommendation_result(result.get("run_status",""),result.get("report_status","")) or "failed"
    if not result.get("found"):
        with queue.store() as store:
            store.start_run(task["run_id"],kind=task["kind"],started_at=now_iso(),snapshot={"task_id":identifier})
            store.finish_run(task["run_id"],status="failed",finished_at=now_iso(),notes=f"流程未产生完整结果，退出码 {code}")
    if task["notify_mode"]!="none":
        queue.stage(identifier,"notification")
        if task["notify_mode"]=="weekly":
            with queue.store() as store:
                code=_finish_weekly(settings,store,schedule_config(settings),task["cycle_id"],task["run_id"],code,
                    config_loader=(lambda settings,**kwargs:services.email_config(settings)) if services else None,
                    sender=services.sender if services else None,before_send=mark_email_started)
        else:
            notify_task(settings,queue,task,services)
    queue.finish(identifier,processing,"结果来自已保存运行记录；报告与通知状态分别展示",exit_code=code)
    return True


def wait_for_task(settings,queue,task_id,*,poll_seconds=.2):
    """同步入口可执行队列或等待已有 worker；只有目标任务终态才返回。"""
    import time
    while True:
        task=queue.get(task_id)
        if task["status"] in {"completed","failed","interrupted","cancelled"}:
            return task
        lock=WorkerLock(queue.directory)
        if lock.acquire():
            try:
                queue.recover(lock)
                token=uuid.uuid4().hex
                while queue.get(task_id)["status"] not in {"completed","failed","interrupted","cancelled"}:
                    if not work_once(settings,queue,token):
                        time.sleep(poll_seconds)
            finally:
                lock.release()
        else:
            time.sleep(poll_seconds)


def run_worker(settings,queue,*,once=False,poll_seconds=1,demo_delay=1,scenario="success",on_ready=None,stop_requested=None):
    import time
    lock=WorkerLock(queue.directory)
    if not lock.acquire():
        print("另一个统一队列执行进程持有系统锁；本进程未领取任务",flush=True)
        return 0
    try:
        queue.recover(lock)
        if on_ready: on_ready()
        token=uuid.uuid4().hex
        while True:
            if stop_requested and stop_requested():
                return 0  # finish an in-flight task before reaching this point
            handled=work_once(settings,queue,token,demo_delay=demo_delay,scenario=scenario)
            if once:
                return 0
            if not handled:
                time.sleep(poll_seconds)
    finally:
        lock.release()
