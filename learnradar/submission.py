"""Synchronously submit/wait through the durable queue without recursively invoking CLI or weekly."""
import uuid

from .tasks import TaskError, TaskQueue

RUN_OPTIONS=("candidate","compare","model","per_page","limit","candidates_from","screening_only","screening_from")
REPLAY_OPTIONS=("run","target","compare","model")


def queue_for(settings):
    return TaskQueue(settings.data_dir,demo=bool(settings.app.get("demo_mode")),
                     daily_limit=settings.limit("manual_tasks_per_day",3))


def fixture_options(args):
    if not getattr(args,"demo",False):
        return {}
    return {"_fixture_delay":args.demo_delay,"_fixture_scenario":args.demo_scenario}


def submit_cli(settings,args,*,replay=False):
    from .cli import _find_topic
    from .worker import wait_for_task, task_exit_code
    topic="replay" if replay else str(_find_topic(settings,getattr(args,"topic",None))["id"])
    options={name:getattr(args,name,None) for name in (REPLAY_OPTIONS if replay else RUN_OPTIONS)}
    # Defaults match the parser and preserve old Namespace callers/tests.
    if not replay:
        for name,value in (("per_page",20),("limit",20),("screening_only",False)):
            if options[name] is None: options[name]=value
    options.update(fixture_options(args))
    if not replay:
        from .discovery import request_options
        selected = getattr(args, 'topic', None)
        mode = getattr(args, 'discovery_mode', None)
        if selected:
            mode = 'focused'  # Explicit legacy --topic always keeps its scope.
        if not mode and (args.candidate or args.candidates_from or args.screening_from):
            mode, selected = 'focused', topic
        topic, discovery = request_options(settings, mode=mode, topic=selected)
        if discovery['discovery_mode'] == 'broad':
            discovery['discovery_limits']['per_direction'] = min(discovery['discovery_limits']['per_direction'],
                max(1, options['per_page'], options['limit']))
        options.update(discovery)
    try:
        queue=queue_for(settings)
        task=queue.submit(topic,"cli-"+uuid.uuid4().hex,options=options,
            kind="replay_analysis" if replay else "manual_recommendation",
            run_id=getattr(args,"run_id",None),notify_mode="none")
        print(f"任务已保存：{task['task_id']} · run_id={task['run_id']}；同步等待处理终态",flush=True)
        finished=wait_for_task(settings,queue,task["task_id"])
        print(f"任务终态：{finished['status']} · run_id={finished['run_id']}",flush=True)
        return task_exit_code(finished)
    except TaskError as exc:
        print(f"未执行：{exc}",flush=True)
        return 1
