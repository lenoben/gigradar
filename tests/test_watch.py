"""Run: .venv/Scripts/python -m unittest discover -s tests   (fake searcher: no network)"""

import logging
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gigradar.config import load_config
from gigradar.search import SearchBlocked
from gigradar.store import established_searches, open_store, record_searches, seen_count
from gigradar.tokens import TokenUnavailable
from gigradar.watch import EXIT_CONFIG, EXIT_ERROR, EXIT_OK, EXIT_STOPPED, main, run_watch
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
        return box["result"]


class FakeNotifier:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str]] = []
        self.batches: list[list[str]] = []  # job ids per notify_jobs call

    def notify(self, title: str, message: str) -> None:
        if self.fail:
            raise RuntimeError("toast broke")
        self.sent.append((title, message))

    def notify_jobs(self, jobs: list[Job]) -> None:
        if self.fail:
            raise RuntimeError("telegram broke")
        self.batches.append([j.url.rsplit("/", 1)[-1] for j in jobs])


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

    def run_once(self, outcomes, notifier, fail_before=None) -> tuple[int, FakeSearcher]:
        searcher = FakeSearcher(outcomes, fail_before)
        return run_watch(self.cfg, searcher, notifier, self.conn, NOW), searcher

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
                      notifier, conn, NOW)
            self.assertEqual(notifier.batches, [])  # all seen under v1
            run_watch(self.cfg, FakeSearcher({"a": [job("~2"), job("~4")], "b": [job("~3")]}, None),
                      notifier, conn, NOW)
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


if __name__ == "__main__":
    unittest.main()
