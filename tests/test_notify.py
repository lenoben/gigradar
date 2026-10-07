"""Run: .venv/Scripts/python -m unittest discover -s tests   (no toast shown, no network)"""

import tempfile
import unittest
from pathlib import Path

from gigradar.config import ConfigError, load_config
from gigradar.notify import SCORE_CHARS, Alert, LogNotifier, MultiNotifier, build_notifier, score_text, summarize_alerts
from gigradar.score import Score
from gigradar.telegram import TelegramNotifier
from upwork_search import Job


def job(cipher: str) -> Job:
    return Job(f"job {cipher}", f"https://www.upwork.com/jobs/{cipher}", None, None, None, None, None, None, "", "")


def alert(cipher: str, value: int | None = None, reason: str = "Full-stack web") -> Alert:
    return Alert(job(cipher), None if value is None else Score(value, reason, "embed", "2"))


class Recorder:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.calls: list[object] = []

    def notify(self, title: str, message: str) -> None:
        self.calls.append(("notify", title))
        if self.fail:
            raise RuntimeError("down")

    def notify_jobs(self, alerts: list[Alert]) -> None:
        self.calls.append(("jobs", len(alerts)))
        if self.fail:
            raise RuntimeError("down")


class NotifyTest(unittest.TestCase):
    def test_summary_unscored(self) -> None:
        self.assertEqual(summarize_alerts([alert("~1")]), ("gigradar: 1 new job", "• job ~1"))
        title, message = summarize_alerts([alert(f"~{i}") for i in range(5)])
        self.assertEqual(title, "gigradar: 5 new jobs")
        self.assertTrue(message.endswith("…and 2 more"))

    def test_summary_scored_keeps_order_and_shows_top(self) -> None:
        title, message = summarize_alerts([alert("~1", 82), alert("~2", 40), alert("~3")])
        self.assertEqual(title, "gigradar: 3 new jobs (top 82)")
        self.assertEqual(message, "• 82 job ~1\n• 40 job ~2\n• job ~3")

    def test_score_text(self) -> None:
        self.assertEqual(score_text(None), "Score n/a")
        self.assertEqual(score_text(Score(82, "Full-stack web · matched: Next.js", "embed", "2")),
                         "82 · Full-stack web · matched: Next.js")
        long = score_text(Score(90, "Web · matched: " + ", ".join(f"S{i}" for i in range(100)), "embed", "2"))
        self.assertLessEqual(len(long), SCORE_CHARS)
        self.assertTrue(long.endswith("…"))
        self.assertNotIn(", …", long)  # cut cleanly, no dangling separator

    def test_log_notifier_logs_every_alert(self) -> None:
        with self.assertLogs("gigradar", "INFO") as logs:
            LogNotifier().notify_jobs([alert("~1", 82, "Full-stack web · matched: Rust"), alert("~2")])
        self.assertIn("gigradar: 2 new jobs (top 82)", logs.output[0])
        self.assertTrue(logs.output[1].endswith("82 · Full-stack web · matched: Rust | job ~1"))
        self.assertTrue(logs.output[2].endswith("Score n/a | job ~2"))

    def test_multi_fans_out_and_raises_after_trying_all(self) -> None:
        bad, good = Recorder(True), Recorder(False)
        multi = MultiNotifier([bad, good])
        with self.assertRaises(RuntimeError) as ctx:
            multi.notify_jobs([alert("~1")])
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
