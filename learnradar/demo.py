"""Explicit offline demo adapters: no private profiles, production configuration changes, or network calls."""
import dataclasses
import json
import smtplib
import time
import uuid
from pathlib import Path

from .collector import Collected
from .models import ModelError, ModelResponse, Usage
from .notify import EmailConfig, send_email
from .evidence import Evidence

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def demo_settings(settings, directory):
    offline = settings.models["offline"]
    screening = dataclasses.replace(offline, name="demo-screening", model_id="fixture-screening")
    analysis = dataclasses.replace(offline, name="demo-analysis", model_id="fixture-analysis")
    return dataclasses.replace(settings, app={**settings.app,"data_dir":str(directory),"demo_mode":True},
        models={**settings.models,screening.name:screening,analysis.name:analysis},
        roles={"screening":screening.name,"analysis":analysis.name},
        profile_path=settings.profile_example_path)


class DemoServices:
    def __init__(self, settings, run_id, *, delay=1, scenario="success"):
        self.settings, self.delay, self.scenario = settings, delay, scenario
        raw=(FIXTURES/"offline_evidence.json").read_text(encoding="utf-8")
        report=(FIXTURES/"offline_analysis.json").read_text(encoding="utf-8")
        # Use a distinct synthetic project per task while exercising actual cooldown rules.
        for name in ("target","baseline-a","baseline-b"):
            old=f"example-offline/{name}"
            new=f"example-offline/{name}-{run_id[-8:]}"
            raw,report=raw.replace(old,new),report.replace(old,new)
        self.evidence=[Evidence(**item) for item in json.loads(raw)]
        self.report=json.loads(report)
        self.report['archive_tags'] = [{'label':'工具调用','basis':'fixture README 原文含工具调用；未实测',
            'quote':'该项目声称提供工具调用示例', 'evidence_ids':['E-readme-fixture-target']}]
        self.evidence[0].extra['topics'] = ['fixture', 'agent-tools']
        self.names=[self.report["target"]["repo"],*[x["comparator"] for x in self.report["comparisons"]]]
        self.items=[{"id":i,"full_name":name,"html_url":f"https://github.com/{name}",
                     "stargazers_count":1234,"description":"fixture 离线占位项目；未实测",
                     "pushed_at":"2026-01-01T00:00:00Z","language":"Python","license":{"spdx_id":"MIT"}, 'topics':['fixture','agent-tools']}
                    for i,name in enumerate(self.names)]
        self.mode="fixture"
        self.notes=["fixture：离线演示，未执行 GitHub 搜索或网络取证。"]

    def client(self, settings, audit):
        return self

    def search_repositories(self, query, per_page):
        return list(self.items)

    def get_repository(self,name):
        return next(item for item in self.items if item["full_name"]==name)

    def collector(self, client, name, *, role, **kwargs):
        return Collected(full_name=name,role=role,repo=next(x for x in self.items if x["full_name"]==name),
                         evidence=[e for e in self.evidence if e.owner==name],readme_available=True,
                         learning_entry={"verified":False,"example_signal":False,"url":"","error":"fixture 未实测入口"})

    def profile(self, settings):
        import tomllib
        return tomllib.loads(settings.profile_example_path.read_text(encoding="utf-8")),True,settings.profile_example_path

    def adapter(self, candidate, *, audit):
        services=self
        class Adapter:
            def complete(self, **kwargs):
                time.sleep(services.delay)
                if services.scenario=="model-failed":
                    raise ModelError("fixture 假模型请求失败；未调用真实模型")
                if candidate.name=="demo-screening":
                    payload={"candidates":[{"repo":name,"decision":"keep","fit":"high" if i==0 else "medium","reason":"fixture 演示候选","gate":"未实测"} for i,name in enumerate(services.names)]}
                else:
                    payload=json.loads(json.dumps(services.report))
                    if services.scenario=="rejected":
                        payload["claims"][0]["quote"]="fixture fabricated quotation rejected by quality gate"
                return ModelResponse(text=json.dumps(payload,ensure_ascii=False),usage=Usage(100,200,0,{"fixture":True}),
                    model_id=candidate.model_id,provider="offline_fixture",latency_ms=0,endpoint="fixture://demo",raw={"fixture":True},fixture=True)
        return Adapter()

    def email_config(self, settings):
        return EmailConfig(host="fixture.invalid",port=587,security="starttls",sender="demo@example.invalid",
                           recipient="reader@example.invalid",subject_prefix="[fixture 演示]",username="")

    def sender(self, config, **kwargs):
        services=self
        class FakeSMTP:
            def __init__(self,*args,**kwargs): pass
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def ehlo(self): pass
            def starttls(self,**kwargs): pass
            def send_message(self,message):
                directory=services.settings.data_dir/"fixture-mail"
                directory.mkdir(parents=True,exist_ok=True)
                (directory/f"{uuid.uuid4().hex}.eml").write_text("X-LearningRadar-Fixture: true\n"+message.as_string(),encoding="utf-8")
                if services.scenario=="mail-failed":
                    raise smtplib.SMTPDataError(451,b"fixture failure")
                if services.scenario=="mail-unknown":
                    raise smtplib.SMTPServerDisconnected("fixture unknown acceptance")
        return send_email(config,smtp_factory=FakeSMTP,**kwargs)
