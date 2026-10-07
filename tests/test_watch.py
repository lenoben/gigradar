"""Run: .venv/Scripts/python -m unittest discover -s tests   (fake searcher: no network)"""

import logging
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gigradar.config import load_config
from gigradar.embed import EmbedderError
from gigradar.notify import Alert
from gigradar.score import Score
from gigradar.search import SearchBlocked
from gigradar.store import established_searches, open_store, record_searches, seen_count
from gigradar.tokens import TokenUnavailable
from gigradar.watch import (EXIT_CONFIG, EXIT_ERROR, EXIT_OK, EXIT_STOPPED, SCORING_BUDGET_S, SCORING_CHUNK,
                            build_score_fn, main, run_watch)
from test_store import make_v1_store
from upwork_search import Job

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
TOML = """
[[searches]]
name = "a"
query = "python"

[[searches]]
name = "b"
query = "rust"
"""


def reset_logging() -> None:
    """Close and detach file handlers so Windows can delete the temp dir and later tests
    don't write to a deleted file."""
    for name in ("gigradar", "pywebview"):
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            handler.close()
        logger.handlers[:] = []


def job(cipher: str) -> Job:
    return Job(f"job {cipher}", f"https://www.upwork.com/jobs/{cipher}", None, None, None, None, None, None, "", "")


class FakeSearcher:
    """Runs `work` on a separate thread, like pywebview does."""
    name = "fake"

    def __init__(self, outcomes: dict[str, object], fail_before: Exception | None) -> None:
        self.outcomes = outcomes
        self.fail_before = fail_before
        self.requests: list[str] = []
        self.finished = False  # True once run() returned (the WebView watchdog is off then)

    def run(self, work):
        if self.fail_before:
            raise self.fail_before
        box = {}

        def search(spec):
            self.requests.append(spec.name)
            outcome = self.outcomes[spec.name]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        thread = threading.Thread(target=lambda: box.update(result=work(search)))
        thread.start()
        thread.join()
        self.finished = True
        return box["result"]


class FakeNotifier:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str]] = []
        self.batches: list[list[str]] = []  # job ids per notify_jobs call
        self.alerts: list[Alert] = []

    def notify(self, title: str, message: str) -> None:
        if self.fail:
            raise RuntimeError("toast broke")
        self.sent.append((title, message))

    def notify_jobs(self, alerts: list[Alert]) -> None:
        if self.fail:
            raise RuntimeError("telegram broke")
        self.batches.append([a.job.url.rsplit("/", 1)[-1] for a in alerts])
        self.alerts.extend(alerts)


class WatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "gigradar.toml").write_text(TOML, encoding="utf-8")
        self.cfg = load_config(self.dir / "gigradar.toml", {})
        self.conn = open_store(Path(":memory:"), [], NOW)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def establish(self, *names: str) -> None:
        """Searches that already ran before; ~1 was seen back then."""
        record_searches(self.conn, [(name, []) for name in names], [job("~1")], NOW)

    def run_once(self, outcomes, notifier, fail_before=None, score_fn=None, clock=time.monotonic):
        searcher = FakeSearcher(outcomes, fail_before)
        return run_watch(self.cfg, searcher, notifier, self.conn, NOW, score_fn, clock), searcher

    def test_first_run_seeds_silently(self) -> None:
        notifier = FakeNotifier(False)
        code, _ = self.run_once({"a": [job("~1"), job("~2")], "b": [job("~2"), job("~3")]}, notifier)
        self.assertEqual((code, seen_count(self.conn), notifier.batches), (EXIT_OK, 3, []))
        self.assertEqual(established_searches(self.conn), {"a", "b"})
        code, _ = self.run_once({"a": [job("~4")], "b": [job("~3")]}, notifier)
        self.assertEqual(notifier.batches, [["~4"]])  # second run alerts normally

    def test_added_search_seeds_silently_while_established_alerts(self) -> None:
        self.establish("a")
        notifier = FakeNotifier(False)
        self.run_once({"a": [job("~2")], "b": [job("~3"), job("~4")]}, notifier)
        self.assertEqual(notifier.batches, [["~2"]])  # b's 2 old jobs: no blast
        self.assertEqual(seen_count(self.conn), 4)
        self.run_once({"a": [], "b": [job("~3"), job("~5")]}, notifier)
        self.assertEqual(notifier.batches, [["~2"], ["~5"]])  # b is established now

    def test_job_found_by_established_and_new_search_alerts_once(self) -> None:
        self.establish("a")
        notifier = FakeNotifier(False)
        self.run_once({"a": [job("~2")], "b": [job("~2"), job("~3")]}, notifier)
        self.assertEqual(notifier.batches, [["~2"]])
        found_by = self.conn.execute("SELECT job_id, search_name FROM job_search ORDER BY 1, 2").fetchall()
        self.assertEqual(found_by, [("~2", "a"), ("~2", "b"), ("~3", "b")])

    def test_failed_notification_keeps_seeding_and_new_search(self) -> None:
        self.establish("a")
        with self.assertRaises(RuntimeError):
            self.run_once({"a": [job("~2")], "b": [job("~3")]}, FakeNotifier(True))
        self.assertEqual(seen_count(self.conn), 2)  # ~1 + seeded ~3; ~2 stays unseen
        self.assertEqual(established_searches(self.conn), {"a", "b"})
        notifier = FakeNotifier(False)
        self.run_once({"a": [job("~2")], "b": [job("~3")]}, notifier)
        self.assertEqual(notifier.batches, [["~2"]])  # re-sent; ~3 not re-seeded or alerted

    def test_stoprun_registers_only_searches_that_ran(self) -> None:
        code, _ = self.run_once({"a": [job("~2")], "b": SearchBlocked("HTTP 403")}, FakeNotifier(False))
        self.assertEqual((code, established_searches(self.conn)), (EXIT_STOPPED, {"a"}))

    def test_v1_store_migrated_without_realerts(self) -> None:
        path = self.dir / "data" / "gigradar.db"
        make_v1_store(path, ["~1", "~2", "~3"])
        conn = open_store(path, ["a", "b"], NOW)  # what main() does with this config
        try:
            notifier = FakeNotifier(False)
            run_watch(self.cfg, FakeSearcher({"a": [job("~1"), job("~2")], "b": [job("~3")]}, None),
                      notifier, conn, NOW, None, time.monotonic)
            self.assertEqual(notifier.batches, [])  # all seen under v1
            run_watch(self.cfg, FakeSearcher({"a": [job("~2"), job("~4")], "b": [job("~3")]}, None),
                      notifier, conn, NOW, None, time.monotonic)
            self.assertEqual(notifier.batches, [["~4"]])  # adopted searches alert at once
        finally:
            conn.close()

    def test_main_store_error_exit_code(self) -> None:
        toml = TOML + '[store]\npath = "x.db"\n[search]\nbackend = "curl"\n'
        (self.dir / "gigradar.toml").write_text(toml, encoding="utf-8")
        conn = sqlite3.connect(str(self.dir / "x.db"))
        conn.execute("PRAGMA user_version = 99")
        conn.close()
        log_file = self.dir / "logs" / "gigradar.log"
        try:
            self.assertEqual(main(["--config", str(self.dir / "gigradar.toml"), "--log-file", str(log_file)]),
                             EXIT_ERROR)
            self.assertIn("newer than this code", log_file.read_text(encoding="utf-8"))
        finally:
            reset_logging()

    def test_new_jobs_notified_once_and_marked(self) -> None:
        self.establish("a", "b")
        notifier = FakeNotifier(False)
        code, _ = self.run_once({"a": [job("~1"), job("~2")], "b": [job("~3")]}, notifier)
        self.assertEqual((code, notifier.batches), (EXIT_OK, [["~2", "~3"]]))
        code, _ = self.run_once({"a": [job("~2")], "b": [job("~3")]}, notifier)
        self.assertEqual(len(notifier.batches), 1)  # nothing new the second time

    def test_failed_notification_does_not_mark_seen(self) -> None:
        self.establish("a", "b")
        with self.assertRaises(RuntimeError):
            self.run_once({"a": [job("~2")], "b": []}, FakeNotifier(True))
        self.assertEqual(seen_count(self.conn), 1)  # ~2 will be re-sent next run

    def test_stoprun_mid_way_keeps_partial_results_and_stops_requests(self) -> None:
        self.establish("a", "b")
        notifier = FakeNotifier(False)
        code, searcher = self.run_once({"a": [job("~2")], "b": SearchBlocked("HTTP 403")}, notifier)
        self.assertEqual(code, EXIT_STOPPED)
        self.assertEqual(searcher.requests, ["a", "b"])
        self.assertEqual(notifier.batches, [["~2"]])

    def test_stoprun_on_first_search_makes_no_more_requests(self) -> None:
        self.establish("a", "b")
        code, searcher = self.run_once({"a": SearchBlocked("HTTP 403"), "b": [job("~2")]}, FakeNotifier(False))
        self.assertEqual((code, searcher.requests), (EXIT_STOPPED, ["a"]))

    def test_no_token_before_searching(self) -> None:
        code, searcher = self.run_once({}, FakeNotifier(False), TokenUnavailable("no click"))
        self.assertEqual((code, searcher.requests), (EXIT_STOPPED, []))

    def test_main_config_error_exit_code(self) -> None:
        self.assertEqual(main(["--config", str(self.dir / "missing.toml")]), EXIT_CONFIG)

    def test_log_file_is_created_and_written(self) -> None:
        log_file = self.dir / "logs" / "gigradar.log"
        try:
            self.assertEqual(main(["--config", str(self.dir / "missing.toml"), "--log-file", str(log_file)]),
                             EXIT_CONFIG)
            text = log_file.read_text(encoding="utf-8")
            self.assertIn("run start", text)
            self.assertIn("ERROR [gigradar] config:", text)
            logging.getLogger("pywebview").error("WebView2 initialization failed")  # captured too
            self.assertIn("[pywebview] WebView2 initialization failed", log_file.read_text(encoding="utf-8"))
        finally:
            reset_logging()


class FakeScoreFn:
    """Scores by the number in the job id (~7 -> 70); records calls; can fail or check a flag."""

    def __init__(self, fail_on_call: int | None = None, must_be_true=None) -> None:
        self.calls: list[list[str]] = []
        self.fail_on_call = fail_on_call
        self.must_be_true = must_be_true

    def __call__(self, jobs: list[Job]) -> list[Score]:
        if self.must_be_true is not None:
            assert self.must_be_true(), "scored while the searcher was still running"
        self.calls.append([j.url.rsplit("/", 1)[-1] for j in jobs])
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("onnx exploded")
        return [Score(min(100, int(j.url.rsplit("~", 1)[-1]) * 10), "Sec · matched: X", "embed", "2") for j in jobs]


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ScoringWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "gigradar.toml").write_text(TOML, encoding="utf-8")
        self.cfg = load_config(self.dir / "gigradar.toml", {})
        self.conn = open_store(Path(":memory:"), [], NOW)
        record_searches(self.conn, [("a", []), ("b", [])], [], NOW)  # both established

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def run_with(self, outcomes, score_fn, notifier=None, clock=time.monotonic):
        notifier = notifier or FakeNotifier(False)
        searcher = FakeSearcher(outcomes, None)
        if isinstance(score_fn, FakeScoreFn):
            score_fn.must_be_true = lambda: searcher.finished
        code = run_watch(self.cfg, searcher, notifier, self.conn, NOW, score_fn, clock)
        return code, notifier

    def stored_scores(self) -> list[tuple]:
        return self.conn.execute("SELECT job_id, value FROM scores ORDER BY job_id").fetchall()

    def test_alerts_scored_sorted_and_saved_after_the_search(self) -> None:
        score_fn = FakeScoreFn()
        code, notifier = self.run_with({"a": [job("~3"), job("~9")], "b": [job("~5")]}, score_fn)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(notifier.batches, [["~9", "~5", "~3"]])  # best first
        self.assertEqual([a.score.value for a in notifier.alerts], [90, 50, 30])
        self.assertEqual(self.stored_scores(), [("~3", 30), ("~5", 50), ("~9", 90)])
        self.assertEqual(seen_count(self.conn), 3)

    def test_no_new_jobs_means_no_scoring_call(self) -> None:
        score_fn = FakeScoreFn()
        self.run_with({"a": [], "b": []}, score_fn)
        self.assertEqual(score_fn.calls, [])  # the real model is never loaded in such runs

    def test_seeded_jobs_are_not_scored(self) -> None:
        self.conn.execute("DELETE FROM search_runs WHERE search_name = 'b'")  # b: first run
        self.conn.commit()
        score_fn = FakeScoreFn()
        _, notifier = self.run_with({"a": [job("~2")], "b": [job("~4"), job("~6")]}, score_fn)
        self.assertEqual(score_fn.calls, [["~2"]])
        self.assertEqual(notifier.batches, [["~2"]])

    def test_scoring_failure_alerts_unscored_and_marks_seen(self) -> None:
        jobs = [job(f"~{i}") for i in range(1, SCORING_CHUNK + 4)]  # two chunks; the second fails
        with self.assertLogs("gigradar", "ERROR") as logs:
            code, notifier = self.run_with({"a": jobs, "b": []}, FakeScoreFn(fail_on_call=2))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("scoring failed: 3 jobs alerted unscored", logs.output[0])
        values = [a.score.value if a.score else None for a in notifier.alerts]
        self.assertEqual(values[:SCORING_CHUNK], sorted(values[:SCORING_CHUNK], reverse=True))
        self.assertEqual(values[SCORING_CHUNK:], [None] * 3)  # unscored last
        self.assertEqual(seen_count(self.conn), len(jobs))
        self.assertEqual(len(self.stored_scores()), SCORING_CHUNK)

    def test_budget_exhausted_alerts_the_rest_unscored(self) -> None:
        jobs = [job(f"~{i}") for i in range(1, 3 * SCORING_CHUNK + 1)]
        clock, fake = ManualClock(), FakeScoreFn()

        def slow_score(chunk):  # the first chunk alone takes the whole budget
            clock.now += SCORING_BUDGET_S
            return fake(chunk)

        with self.assertLogs("gigradar", "WARNING") as logs:
            _, notifier = self.run_with({"a": jobs, "b": []}, slow_score, clock=clock)
        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(any("scoring budget" in line for line in logs.output))
        scored = [a for a in notifier.alerts if a.score is not None]
        self.assertEqual(len(scored), SCORING_CHUNK)
        self.assertEqual(len(notifier.alerts), len(jobs))  # nothing lost

    def test_notification_failure_keeps_scores_but_not_seen(self) -> None:
        with self.assertRaises(RuntimeError):
            self.run_with({"a": [job("~4")], "b": []}, FakeScoreFn(), FakeNotifier(True))
        self.assertEqual(self.stored_scores(), [("~4", 40)])
        self.assertEqual(seen_count(self.conn), 0)  # re-sent next run

    def test_build_score_fn(self) -> None:
        self.assertIsNone(build_score_fn(self.cfg, self.conn, NOW))  # no [profile]: scoring off
        (self.dir / "profile.md").write_text("## Web\nNext.js apps\n", encoding="utf-8")
        empty_models = self.dir / "no-models"
        toml = TOML + f'[profile]\nskills = ["Rust"]\n[scoring]\nmodel_dir = "{empty_models.as_posix()}"\n'
        (self.dir / "gigradar.toml").write_text(toml, encoding="utf-8")
        cfg = load_config(self.dir / "gigradar.toml", {})
        score_fn = build_score_fn(cfg, self.conn, NOW)  # lazy: building loads nothing
        self.assertIsNotNone(score_fn)
        env = os.environ.get("HF_HUB_OFFLINE")
        try:
            with self.assertRaises(EmbedderError):  # fastembed missing, or model not downloaded
                score_fn([job("~1")])
        finally:
            if env is None:
                os.environ.pop("HF_HUB_OFFLINE", None)
            else:
                os.environ["HF_HUB_OFFLINE"] = env


if __name__ == "__main__":
    unittest.main()
