"""Run: .venv/Scripts/python -m unittest discover -s tests   (fake searcher: no network)"""

import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gigradar.config import load_config
from gigradar.search import SearchBlocked
from gigradar.store import mark_seen, open_store, seen_count
from gigradar.tokens import TokenUnavailable
from gigradar.watch import EXIT_CONFIG, EXIT_OK, EXIT_STOPPED, main, run_watch
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

    def notify(self, title: str, message: str) -> None:
        if self.fail:
            raise RuntimeError("toast broke")
        self.sent.append((title, message))


class WatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "gigradar.toml").write_text(TOML, encoding="utf-8")
        self.cfg = load_config(self.dir / "gigradar.toml", {})
        self.conn = open_store(Path(":memory:"))

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def run_once(self, outcomes, notifier, fail_before=None) -> tuple[int, FakeSearcher]:
        searcher = FakeSearcher(outcomes, fail_before)
        return run_watch(self.cfg, searcher, notifier, self.conn, NOW), searcher

    def test_first_run_seeds_silently(self) -> None:
        notifier = FakeNotifier(False)
        code, _ = self.run_once({"a": [job("~1"), job("~2")], "b": [job("~2"), job("~3")]}, notifier)
        self.assertEqual((code, seen_count(self.conn), notifier.sent), (EXIT_OK, 3, []))

    def test_new_jobs_notified_once_and_marked(self) -> None:
        mark_seen(self.conn, [job("~1")], NOW)
        notifier = FakeNotifier(False)
        code, _ = self.run_once({"a": [job("~1"), job("~2")], "b": [job("~3")]}, notifier)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(notifier.sent[0][0], "gigradar: 2 new jobs")
        self.assertIn("job ~2", notifier.sent[0][1])
        code, _ = self.run_once({"a": [job("~2")], "b": [job("~3")]}, notifier)
        self.assertEqual(len(notifier.sent), 1)  # nothing new the second time

    def test_many_new_jobs_are_summarized(self) -> None:
        mark_seen(self.conn, [job("~0")], NOW)
        notifier = FakeNotifier(False)
        self.run_once({"a": [job(f"~{i}") for i in range(1, 6)], "b": []}, notifier)
        self.assertEqual(notifier.sent[0][0], "gigradar: 5 new jobs")
        self.assertIn("…and 2 more", notifier.sent[0][1])

    def test_failed_notification_does_not_mark_seen(self) -> None:
        mark_seen(self.conn, [job("~1")], NOW)
        with self.assertRaises(RuntimeError):
            self.run_once({"a": [job("~2")], "b": []}, FakeNotifier(True))
        self.assertEqual(seen_count(self.conn), 1)  # ~2 will be re-sent next run

    def test_stoprun_mid_way_keeps_partial_results_and_stops_requests(self) -> None:
        mark_seen(self.conn, [job("~1")], NOW)
        notifier = FakeNotifier(False)
        code, searcher = self.run_once({"a": [job("~2")], "b": SearchBlocked("HTTP 403")}, notifier)
        self.assertEqual(code, EXIT_STOPPED)
        self.assertEqual(searcher.requests, ["a", "b"])
        self.assertEqual(notifier.sent[0][0], "gigradar: 1 new job")

    def test_stoprun_on_first_search_makes_no_more_requests(self) -> None:
        mark_seen(self.conn, [job("~1")], NOW)
        code, searcher = self.run_once({"a": SearchBlocked("HTTP 403"), "b": [job("~2")]}, FakeNotifier(False))
        self.assertEqual((code, searcher.requests), (EXIT_STOPPED, ["a"]))

    def test_no_token_before_searching(self) -> None:
        code, searcher = self.run_once({}, FakeNotifier(False), TokenUnavailable("no click"))
        self.assertEqual((code, searcher.requests), (EXIT_STOPPED, []))

    def test_main_config_error_exit_code(self) -> None:
        self.assertEqual(main(["--config", str(self.dir / "missing.toml")]), EXIT_CONFIG)


if __name__ == "__main__":
    unittest.main()
