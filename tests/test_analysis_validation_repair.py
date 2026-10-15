"""Bound publication-check repairs without fabricated evidence, relaxed gates, or overwritten source runs."""
from copy import deepcopy
import dataclasses
import io
import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from learnradar import cli
from learnradar.analysis import run_analysis
from learnradar.config import load_settings
from learnradar.evidence import Evidence
from learnradar.models import ModelError, ModelResponse, Usage
from learnradar.pipeline import RunHooks
from learnradar.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


class ScriptedAdapter:
    def __init__(self, script):
        self.script = list(script)
        self.prompts = []

    def complete(self, *, system, user, max_output_tokens=None):
        self.prompts.append(user)
        value = self.script[len(self.prompts) - 1]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            value, finish = value
        else:
            finish = "stop"
        return ModelResponse(
            text=json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value,
            usage=Usage(input_tokens=11, output_tokens=7), model_id="fixture-repair",
            provider="offline_fixture", latency_ms=1, endpoint="fixture://repair",
            raw={}, fixture=True, finish_reason=finish,
        )


class ValidationRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = load_settings(local_overrides=False)
        self.settings = dataclasses.replace(base, app={**base.app, "data_dir": self.tmp.name})
        self.good = json.loads((FIXTURES / "offline_analysis.json").read_text(encoding="utf-8"))
        self.evidence = [Evidence(**item) for item in json.loads(
            (FIXTURES / "offline_evidence.json").read_text(encoding="utf-8"))]
        self.target = self.good["target"]["repo"]
        self.comparators = [item["comparator"] for item in self.good["comparisons"]]
        self.bad = deepcopy(self.good)
        self.bad["novelty"]["evidence_ids"] = []
        self.bad["novelty"]["quote"] = ""

    def validate(self, report):
        return cli._validate_analysis_candidate(
            self.settings, report, evidence=self.evidence, target_name=self.target,
            comparator_names=self.comparators, require_quotes=True,
        )

    def run_script(self, script, *, max_calls=2):
        scripted = ScriptedAdapter(script)
        budget = cli.ModelBudget(max_calls)
        outcome = run_analysis(
            cli.BudgetedAdapter(scripted, budget), self.settings,
            profile={"profile_id": "fixture"}, profile_confirmed=False,
            target={"repo": self.target}, comparators=[{"repo": r} for r in self.comparators],
            evidence=self.evidence, max_calls=max_calls,
            report_validator=lambda report: self.validate(report).errors,
        )
        return scripted, budget, outcome

    def test_missing_novelty_evidence_is_repaired_and_rechecked(self):
        adapter, budget, result = self.run_script([self.bad, self.good])
        self.assertEqual(budget.used, 2)
        self.assertTrue(result.repaired)
        self.assertEqual(result.retry_kind, "validation_failed")
        self.assertEqual(result.selected_attempt, 2)
        self.assertEqual(len(result.validation_checks[0]["errors"]), 2)
        self.assertEqual(result.validation_checks[1]["errors"], [])
        self.assertIn("novelty.evidence_ids", adapter.prompts[1])
        self.assertIn("novelty.quote", adapter.prompts[1])
        self.assertIn(json.dumps(self.bad, ensure_ascii=False), adapter.prompts[1])
        self.assertTrue(self.validate(result.report).ok)

    def test_valid_first_response_uses_one_request(self):
        adapter, budget, result = self.run_script([self.good])
        self.assertEqual(len(adapter.prompts), 1)
        self.assertEqual(budget.used, 1)
        self.assertFalse(result.repaired)

    def test_no_budget_left_preserves_rejected_draft_without_request(self):
        adapter, budget, result = self.run_script([self.bad], max_calls=1)
        self.assertEqual(budget.used, 1)
        self.assertEqual(len(adapter.prompts), 1)
        self.assertEqual(result.report, self.bad)
        self.assertFalse(self.validate(result.report).ok)

    def test_repeated_failure_stops_after_one_repair_even_with_large_budget(self):
        adapter, budget, result = self.run_script([self.bad, self.bad], max_calls=4)
        self.assertEqual(budget.used, 2)
        self.assertEqual(len(adapter.prompts), 2)
        self.assertFalse(result.repaired)
        self.assertFalse(self.validate(result.report).ok)

    def test_json_repair_and_validation_share_the_second_request(self):
        adapter, budget, result = self.run_script(["not json", self.bad], max_calls=4)
        self.assertEqual(budget.used, 2)
        self.assertEqual(len(adapter.prompts), 2)
        self.assertEqual(result.retry_kind, "invalid_json")
        self.assertFalse(result.repaired)
        self.assertFalse(self.validate(result.report).ok)

    def test_fabricated_quote_cannot_pass_repair(self):
        second = deepcopy(self.good)
        second["novelty"]["quote"] = "此项目已经全面证明世界领先并且具有无限性能"
        _, _, result = self.run_script([self.bad, second])
        self.assertFalse(self.validate(result.report).ok)
        self.assertTrue(any("无法在所引证据中匹配" in e
                            for e in result.validation_checks[-1]["errors"]))

    def test_unknown_evidence_id_cannot_pass_repair(self):
        second = deepcopy(self.good)
        second["novelty"]["evidence_ids"] = ["E-invented"]
        _, _, result = self.run_script([self.bad, second])
        self.assertFalse(self.validate(result.report).ok)

    def test_target_cannot_be_changed_by_repair(self):
        second = deepcopy(self.good)
        second["target"]["repo"] = self.comparators[0]
        _, _, result = self.run_script([self.bad, second])
        self.assertFalse(self.validate(result.report).ok)

    def test_broken_repair_retains_first_parseable_draft_and_both_outputs(self):
        for second in ("broken json", ("", "length"), ("{", "length")):
            with self.subTest(second=second):
                _, budget, result = self.run_script([self.bad, second])
                self.assertEqual(budget.used, 2)
                self.assertEqual(result.report, self.bad)
                self.assertEqual(result.selected_attempt, 1)
                self.assertEqual(len(result.responses), 2)
                self.assertTrue(result.retry_error)
                self.assertFalse(self.validate(result.report).ok)

    def test_failed_repair_request_is_counted_and_retains_draft(self):
        _, budget, result = self.run_script([self.bad, ModelError("fixture repair failed")])
        self.assertEqual(budget.used, 2)
        self.assertEqual(len(result.responses), 1)
        self.assertEqual(len(result.failed_requests), 1)
        self.assertEqual(result.report, self.bad)

    def test_server_verification_does_not_mutate_raw_candidate(self):
        report = deepcopy(self.good)
        report.pop("verification", None)
        before = deepcopy(report)
        self.assertTrue(self.validate(report).ok)
        self.assertEqual(report, before)

    def test_replay_restores_readme_entry_with_the_same_signal_rule(self):
        source = deepcopy(self.evidence[1])
        source.excerpt = "```sh\npip install fixture-package\npython -m fixture_package\n```"
        with mock.patch("socket.socket.connect", side_effect=AssertionError("No network")):
            entry = cli._learning_entry_from_evidence([source], self.target)
        self.assertTrue(entry["verified"])
        self.assertTrue(entry["example_signal"])
        self.assertEqual(entry["evidence_id"], source.evidence_id)
        self.assertEqual(entry["source"], "retained_readme")

    def test_replay_does_not_borrow_another_repos_readme_signal(self):
        source = deepcopy(self.evidence[1])
        source.owner = self.comparators[0]
        source.excerpt = "```sh\npip install fixture-package\npython -m fixture_package\n```"
        entry = cli._learning_entry_from_evidence([source], self.target)
        self.assertFalse(entry["verified"])

    def test_replay_pipeline_persists_checks_usage_and_preserves_source(self):
        source = Path(self.tmp.name) / "runs" / "fixture-source"
        source.mkdir(parents=True)
        (source / "evidence.json").write_text(json.dumps(
            [e.to_dict() for e in self.evidence], ensure_ascii=False), encoding="utf-8")
        (source / "run-context.json").write_text(json.dumps(
            {"target": self.target, "comparators": self.comparators, "topic": "agent-memory"}),
            encoding="utf-8")
        before = {p.name: p.read_bytes() for p in source.iterdir()}
        scripted = ScriptedAdapter([self.bad, self.good])
        args = cli.build_parser().parse_args(["replay-analysis", "--run", "fixture-source"])
        args.run_id = "fixture-repair-new"
        hooks = RunHooks(budget=cli.ModelBudget(2),
                         adapter_factory=lambda *a, **k: scripted,
                         profile_loader=lambda settings: ({"profile_id": "fixture"}, True, Path("fixture")))
        with mock.patch("socket.socket.connect", side_effect=AssertionError("No network")), \
             contextlib.redirect_stdout(io.StringIO()):
            code = cli.run_replay_analysis(self.settings, args, hooks=hooks)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(hooks.budget.used, 2)
        run = Path(self.tmp.name) / "runs" / args.run_id
        trace = json.loads((run / "analysis-validation.json").read_text(encoding="utf-8"))
        self.assertEqual(trace["selected_attempt"], 2)
        self.assertEqual(trace["checks"][1]["errors"], [])
        raw = (run / "raw_model_output.txt").read_text(encoding="utf-8")
        self.assertIn(json.dumps(self.bad, ensure_ascii=False), raw)
        self.assertIn(json.dumps(self.good, ensure_ascii=False), raw)
        usage = json.loads((run / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual(usage["totals"]["calls"], 2)
        self.assertEqual({p.name: p.read_bytes() for p in source.iterdir()}, before)
        with Store(Path(self.tmp.name) / "learning-radar.sqlite") as store:
            self.assertEqual(store.get_report(args.run_id)["status"], "published")

    def test_replay_failed_repair_saves_request_failure_and_rejection(self):
        source = Path(self.tmp.name) / "runs" / "fixture-source"
        source.mkdir(parents=True)
        (source / "evidence.json").write_text(json.dumps(
            [e.to_dict() for e in self.evidence]), encoding="utf-8")
        (source / "run-context.json").write_text(json.dumps(
            {"target": self.target, "comparators": self.comparators}), encoding="utf-8")
        args = cli.build_parser().parse_args(["replay-analysis", "--run", "fixture-source"])
        args.run_id = "fixture-repair-rejected"
        scripted = ScriptedAdapter([self.bad, ModelError("fixture failure")])
        hooks = RunHooks(budget=cli.ModelBudget(2), adapter_factory=lambda *a, **k: scripted,
                         profile_loader=lambda settings: ({}, True, Path("fixture")))
        with mock.patch("socket.socket.connect", side_effect=AssertionError("No network")), \
             contextlib.redirect_stdout(io.StringIO()):
            code = cli.run_replay_analysis(self.settings, args, hooks=hooks)
        self.assertEqual(code, cli.EXIT_GATE_FAILED)
        run = Path(self.tmp.name) / "runs" / args.run_id
        usage = json.loads((run / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual(usage["totals"]["calls"], 2)
        self.assertEqual(usage["budget"]["used"], 2)
        self.assertEqual(len(usage["usage"]), 2)
        self.assertFalse(usage["usage"][1]["ok"])
        validation = json.loads((run / "validation.json").read_text(encoding="utf-8"))
        self.assertFalse(validation["ok"])
        self.assertTrue((run / "report.json").is_file())

    def run_persistent_normal_task(self, request_limit):
        from learnradar.demo import DemoServices, demo_settings
        from learnradar.tasks import TaskQueue
        from learnradar.worker import run_worker

        class RepairServices(DemoServices):
            def adapter(self, candidate, *, audit):
                inner = super().adapter(candidate, audit=audit)
                first = True

                class CorruptFirst:
                    def complete(adapter, **kwargs):
                        nonlocal first
                        response = inner.complete(**kwargs)
                        if candidate.name == "demo-analysis" and first:
                            first = False
                            payload = json.loads(response.text)
                            payload["novelty"]["evidence_ids"] = []
                            payload["novelty"]["quote"] = ""
                            response.text = json.dumps(payload, ensure_ascii=False)
                        return response
                return CorruptFirst()

        settings = demo_settings(self.settings, Path(self.tmp.name))
        settings = dataclasses.replace(settings, limits={**settings.limits,
                                                       "max_model_calls_per_run": request_limit})
        queue = TaskQueue(settings.data_dir, demo=True)
        task = queue.submit("agent-memory", "fixture-validation-key-20261013", notify_mode="none")
        with mock.patch("learnradar.demo.DemoServices", RepairServices), \
             mock.patch("socket.socket.connect", side_effect=AssertionError("No network")), \
             contextlib.redirect_stdout(io.StringIO()):
            code = run_worker(settings, queue, once=True, demo_delay=0)
        self.assertEqual(code, 0)
        return queue.get(task["task_id"]), settings

    def test_normal_task_uses_persistent_budget_for_screening_and_repair(self):
        task, settings = self.run_persistent_normal_task(4)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["report"]["status"], "published")
        self.assertEqual(task["model_requests"], 3)  # One screening request, one analysis request, and one repair request.
        self.assertFalse(task["uncertain"])
        run = settings.data_dir / "runs" / task["run_id"]
        usage = json.loads((run / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual(usage["budget"]["used"], 3)
        self.assertEqual(usage["budget"]["screening_requests"], 1)
        self.assertEqual(usage["budget"]["analysis_requests"], 2)
        self.assertEqual(usage["totals"]["calls"], 3)
        self.assertTrue((run / "analysis-validation.json").is_file())

    def test_normal_task_with_no_remaining_repair_budget_is_rejected(self):
        task, settings = self.run_persistent_normal_task(2)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["report"]["status"], "rejected")
        self.assertEqual(task["model_requests"], 2)
        self.assertFalse(task["uncertain"])
        run = settings.data_dir / "runs" / task["run_id"]
        trace = json.loads((run / "analysis-validation.json").read_text(encoding="utf-8"))
        self.assertEqual(len(trace["checks"]), 1)
        self.assertEqual(trace["selected_attempt"], 1)


if __name__ == "__main__":
    unittest.main()
