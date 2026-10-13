"""推荐流程的最小输入/回调/结果契约；CLI 与网页任务共用一个实现。"""
from dataclasses import dataclass, field
from typing import Any, Callable

from .cycle import collect_outcome
from .store import Store


@dataclass(frozen=True)
class RecommendationInput:
    topic: str
    run_id: str
    options: dict = field(default_factory=dict)
    kind: str = "manual_recommendation"


@dataclass
class RunHooks:
    stage: Callable[[str], None] = lambda stage: None
    budget: Any = None
    client_factory: Any = None
    adapter_factory: Any = None
    collector: Any = None
    profile_loader: Any = None


def classify_recommendation_result(run_status: str, report_status: str) -> str | None:
    """从保存结果判断处理终态；None 表示尚无可确认的终态。"""
    if report_status or run_status in {
        "all-candidates-in-cooldown", "no-suitable-candidate", "insufficient-comparators", "screening-ok"
    }:
        return "completed"
    if run_status in {"failed", "screening-failed", "model-failed", "no-evidence"}:
        return "failed"
    return None


def execute_recommendation(settings, request: RecommendationInput, hooks: RunHooks):
    from .cli import build_parser, run_recommendation, run_replay_analysis
    if request.kind == "replay_analysis":
        args=build_parser().parse_args(["replay-analysis","--run",request.options["run"]])
        engine=run_replay_analysis
    else:
        args=build_parser().parse_args(["run","--topic",request.topic])
        engine=run_recommendation
    vars(args).update(request.options)
    args.run_id=request.run_id
    code=engine(settings,args,hooks=hooks)
    # 状态来自数据库/保存产物，不由退出码或终端输出推测。
    with Store(settings.data_dir / "learning-radar.sqlite") as store:
        result=collect_outcome(store, settings, request.run_id)
    result["exit_code"]=code
    return result
