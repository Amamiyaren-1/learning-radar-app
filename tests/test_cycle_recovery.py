"""周期异常路径：占位后未捕获异常的状态/用量/锁、锁存活判断、一次性恢复。"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from learnradar import cli, cloud
from learnradar.cycle import TaskLock
from learnradar.screening import ScreeningOutcome
from tests.test_cooldown import CONFIG, NOW, HistoryTestCase, days_ago
from tests.test_weekly import WeeklyTestCase


class WeeklyErrorPathTests(WeeklyTestCase):
    """周期占位之后出现未捕获异常：状态可诊断、用量保留、锁释放、不重跑、不发邮件。"""

    def _run_with_crash(self, *, crash_message: str) -> tuple[int, str]:
        items = self.items(["a/one", "a/two", "a/three"])
        screening = ScreeningOutcome(
            entries=[{"repo": "a/one", "decision": "keep", "fit": "high", "reason": "相关", "gate": ""}]
        )

        def screening_stage(*args: object, **kwargs: object):
            usage = kwargs.get("usage_entries")
            if isinstance(usage, list):
                usage.append(
                    {
                        "stage": "screening",
                        "role": "screening",
                        "model_id": "deepseek-flash",
                        "calls": 1,
                        "ok": True,
                        "input_tokens": 11,
                        "output_tokens": 22,
                    }
                )
            return screening

        buffer = io.StringIO()
        with mock.patch.object(cli, "_client", return_value=self.fake_client(items)), \
             mock.patch.object(cli, "_run_screening_stage", side_effect=screening_stage), \
             mock.patch.object(cli, "build_adapter", side_effect=RuntimeError(crash_message)), \
             mock.patch("learnradar.collector.fetch", side_effect=self.fake_fetch), \
             contextlib.redirect_stdout(buffer):
            code = cloud.cmd_weekly(
                self.settings, argparse.Namespace(topic=None, cycle="2026-W39", force_redo=False)
            )
        return code, buffer.getvalue()

    def test_unexpected_error_marks_failed_with_sanitized_diagnostics(self) -> None:
        # 故意写成“token=…”的形式：既能验证脱敏，又不会被当成 sk- 风格的真实密钥
        secret_like = "FAKE-ABCDEFGH12345678"
        crash_key = f"token={secret_like}"
        code, output = self._run_with_crash(
            crash_message=f"boom at /var/lib/learning-radar/runs/x and key {crash_key}"
        )
        self.assertNotEqual(code, 0, output)
        cycle = self.store.get_cycle("2026-W39")
        self.assertEqual(cycle["status"], "failed", "异常后不能把周期留在 running")
        self.assertIn("未捕获异常", cycle["notes"])
        self.assertIn("RuntimeError", cycle["notes"])
        self.assertIn("已保留用量快照：1 次模型调用", cycle["notes"], "已发生的调用要留在记录里")
        self.assertNotIn(secret_like, cycle["notes"], "疑似凭据要脱敏")
        self.assertNotIn("/var/lib/learning-radar", cycle["notes"], "绝对路径要脱敏")

        run_dir = self.data_dir / "runs" / "2026-W39-agent-memory"
        self.assertTrue((run_dir / "weekly-error.txt").exists(), "要留下可诊断文件")
        detail = (run_dir / "weekly-error.txt").read_text(encoding="utf-8")
        self.assertNotIn(secret_like, detail)
        self.assertIn("<已隐藏>", detail)
        usage = json.loads((run_dir / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual(usage["totals"]["calls"], 1, "异常前发生的调用不能丢")
        self.assertEqual(self.emails(), [], "异常退出不生成、也不发送邮件")
        self.assertFalse((self.data_dir / "locks" / "weekly.lock").exists(), "锁必须释放")
        self.assertIn("--force-redo", output)

    def test_failed_cycle_refuses_repeat_but_force_redo_recovers(self) -> None:
        code, _output = self._run_with_crash(crash_message="boom")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.store.get_cycle("2026-W39")["status"], "failed")

        code2, output2, adapter2 = self.run_weekly(["a/one", "a/two", "a/three"])
        self.assertNotEqual(code2, 0, output2)
        self.assertIn("该周期已经处理过", output2)
        self.assertEqual(adapter2.calls, 0, "已记录的周期不自动重跑")
        self.assertEqual(self.emails(), [])

        code3, output3, adapter3 = self.run_weekly(["a/one", "a/two", "a/three"], force_redo=True)
        self.assertEqual(code3, 0, output3)
        self.assertEqual(adapter3.calls, 1)
        self.assertEqual(self.store.get_cycle("2026-W39")["status"], "completed")
        self.assertEqual(len(self.emails()), 1)


class CycleLockAndRecoverTests(WeeklyTestCase):
    """锁的存活判断与一次性恢复：不把还在跑的周期误判为中断。"""

    def _seed_running_cycle(self, *, hours_ago: float = 3.0) -> None:
        started = (
            datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        ).replace(microsecond=0).isoformat()
        self.store.save_cycle("2026-W39", status="running", topic="agent-memory", started_at=started)

    def _write_lock(self, pid: int) -> Path:
        lock = self.data_dir / "locks" / "weekly.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(f"pid={pid} started_at=2026-09-24T22:17:00+08:00\n", encoding="utf-8")
        return lock

    def test_live_holder_is_not_marked_interrupted(self) -> None:
        self._seed_running_cycle()
        self._write_lock(os.getpid())
        with mock.patch("learnradar.cycle.os.kill") as killer:
            code, output, adapter = self.run_weekly(["a/one", "a/two", "a/three"])
        killer.assert_not_called()
        self.assertNotEqual(code, 0, output)
        self.assertIn("旧 weekly 锁仍存在", output)
        self.assertEqual(
            self.store.get_cycle("2026-W39")["status"], "running", "不能把还在跑的周期标成中断"
        )
        self.assertEqual(adapter.calls, 0)

    def test_dead_holder_is_reported_but_not_touched(self) -> None:
        self._seed_running_cycle()
        lock = self._write_lock(999999)
        with mock.patch("learnradar.cycle.os.kill") as killer:
            code, output, adapter = self.run_weekly(["a/one", "a/two", "a/three"])
        killer.assert_not_called()
        self.assertNotEqual(code, 0, output)
        self.assertIn("旧 weekly 锁仍存在", output)
        self.assertTrue(lock.exists(), "不自动删别人的锁文件")
        self.assertEqual(self.store.get_cycle("2026-W39")["status"], "running")
        self.assertEqual(adapter.calls, 0)

    def test_non_posix_never_probes_pid(self) -> None:
        """Windows 上 os.kill(pid, 0) 会真的终止进程，所以那里绝不探测。"""
        lock = self._write_lock(999999)
        task_lock = TaskLock(lock)
        with mock.patch("learnradar.cycle.os.name", "nt"), mock.patch(
            "learnradar.cycle.os.kill"
        ) as killer:
            self.assertTrue(task_lock.holder_alive())
            killer.assert_not_called()

    def test_cycle_recover_marks_interrupted_after_checks(self) -> None:

        self._seed_running_cycle()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cloud.cmd_cycle_recover(
                self.settings, argparse.Namespace(cycle="2026-W39", backup_out=None)
            )
        output = buffer.getvalue()
        self.assertEqual(code, 0, output)
        self.assertIn("已把 2026-W39 记为 interrupted", output)
        self.assertEqual(self.store.get_cycle("2026-W39")["status"], "interrupted")
        self.assertTrue(
            (self.data_dir / "backups" / "2026-W39-before-recover.sqlite").exists(),
            "改状态前要先做一致性备份",
        )

    def test_cycle_recover_refuses_when_conditions_not_met(self) -> None:
        self._seed_running_cycle()
        self.store.save_cycle("2026-W39", status="running", run_id="2026-W39-agent-memory")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cloud.cmd_cycle_recover(
                self.settings, argparse.Namespace(cycle="2026-W39", backup_out=None)
            )
        output = buffer.getvalue()
        self.assertNotEqual(code, 0, output)
        self.assertIn("不满足", output)
        self.assertEqual(
            self.store.get_cycle("2026-W39")["status"], "running", "条件不满足就不能改状态"
        )


class UnreadableRunContextTests(HistoryTestCase):
    """迁移后目录权限不对时：读不到 run-context.json 不能把整轮拖垮。"""

    def test_unreadable_run_context_is_skipped_with_warning(self) -> None:
        # 只有 runs 行、没有 report：目标要从 run-context.json 里读，才会走到被加固的那段
        self.seed(
            "run-locked", target="owner/proj", run_status="model-failed", report_status=None,
            finished=days_ago(2),
        )
        real_exists = Path.exists

        def fake_exists(self: Path) -> bool:  # noqa: N805
            if self.name == "run-context.json" and "run-locked" in str(self.parent):
                raise PermissionError(13, "Permission denied")
            return real_exists(self)

        from learnradar.history import filter_items, target_history

        warnings: list[str] = []
        with mock.patch.object(Path, "exists", fake_exists):
            history = target_history(
                self.store, runs_dir=self.data_dir / "runs", warnings=warnings
            )
        self.assertNotIn("owner/proj", history, "读不到就按“没有目标记录”处理，不猜")
        self.assertEqual(len(warnings), 1)
        self.assertIn("run-context.json 读不到", warnings[0])
        self.assertIn("PermissionError", warnings[0])

        decisions = filter_items([{"full_name": "owner/proj"}], history, config=CONFIG, now=NOW)
        self.assertNotEqual(decisions[0].state, "skipped", "读不到历史时按可参与处理")


if __name__ == "__main__":
    unittest.main()
