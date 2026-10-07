"""Run: .venv/Scripts/python -m unittest discover -s tests   (no toast shown, no network)"""

import tempfile
import unittest
from pathlib import Path

from gigradar.config import ConfigError, load_config
from gigradar.notify import LogNotifier, MultiNotifier, build_notifier, summarize_jobs
from gigradar.telegram import TelegramNotifier
from upwork_search import Job


def job(cipher: str) -> Job:
    return Job(f"job {cipher}", f"https://www.upwork.com/jobs/{cipher}", None, None, None, None, None, None, "", "")


class Recorder:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.calls: list[object] = []

    def notify(self, title: str, message: str) -> None:
        self.calls.append(("notify", title))
        if self.fail:
            raise RuntimeError("down")

    def notify_jobs(self, jobs: list[Job]) -> None:
        self.calls.append(("jobs", len(jobs)))
        if self.fail:
            raise RuntimeError("down")


class NotifyTest(unittest.TestCase):
    def test_summary(self) -> None:
        self.assertEqual(summarize_jobs([job("~1")]), ("gigradar: 1 new job", "• job ~1"))
        title, message = summarize_jobs([job(f"~{i}") for i in range(5)])
        self.assertEqual(title, "gigradar: 5 new jobs")
        self.assertTrue(message.endswith("…and 2 more"))

    def test_multi_fans_out_and_raises_after_trying_all(self) -> None:
        bad, good = Recorder(True), Recorder(False)
        multi = MultiNotifier([bad, good])
        with self.assertRaises(RuntimeError) as ctx:
            multi.notify_jobs([job("~1")])
        self.assertEqual((bad.calls, good.calls), ([("jobs", 1)], [("jobs", 1)]))
        self.assertIn("Recorder: down", str(ctx.exception))
        MultiNotifier([good]).notify("t", "m")
        self.assertEqual(good.calls[-1], ("notify", "t"))

    def test_build_notifier_with_telegram(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gigradar.toml"
            path.write_text('[notify]\nchannels = ["telegram"]\n[[searches]]\nname = "a"\n', encoding="utf-8")
            with self.assertRaises(ConfigError):  # secrets missing
                load_config(path, {})
            cfg = load_config(path, {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42"})
        notifier = build_notifier(cfg)
        self.assertEqual([type(n) for n in notifier.notifiers], [LogNotifier, TelegramNotifier])


if __name__ == "__main__":
    unittest.main()
