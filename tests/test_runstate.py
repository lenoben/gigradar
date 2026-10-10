"""Run: .venv/Scripts/python -m unittest discover -s tests   (offline: fake notifier, fake pid check)"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from gigradar.notify import LogNotifier, MultiNotifier, ToastNotifier, warning_notifier
from gigradar.runstate import (STATE_FILE, STILL_RUNNING_FOR, RunState, begin_run, end_run, pid_alive, read_state,
                               state_path, write_state)
from gigradar.telegram import TelegramNotifier
from gigradar.watch import EXIT_ERROR, main
from test_watch import TOML, reset_logging

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
DEAD = lambda pid: False  # noqa: E731
ALIVE = lambda pid: True  # noqa: E731


class FakeWarn:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str]] = []

    def notify(self, title: str, message: str) -> None:
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((title, message))

    def notify_jobs(self, alerts) -> None:
        raise AssertionError("a crash warning is a status message, not a job alert")


class RunStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "data" / STATE_FILE
        self.warn = FakeWarn()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def begin(self, minutes: int, alive=DEAD, pid: int = 100) -> RunState | None:
        return begin_run(self.path, self.warn, NOW + timedelta(minutes=minutes), pid, alive)

    def test_first_run_does_not_warn_and_marks_started(self) -> None:
        state = self.begin(0)
        self.assertEqual(self.warn.sent, [])
        self.assertEqual(read_state(self.path), RunState(NOW.isoformat(), 100, False, False))
        self.assertEqual(state, read_state(self.path))
        self.assertEqual(os.listdir(self.path.parent), [STATE_FILE])  # no temp file left behind

    def test_completed_run_leaves_no_warning(self) -> None:
        end_run(self.path, self.begin(0))
        self.assertEqual(read_state(self.path), RunState(NOW.isoformat(), 100, True, False))
        self.begin(30)
        self.assertEqual(self.warn.sent, [])

    def test_unfinished_run_warns_exactly_once_until_a_run_completes(self) -> None:
        self.begin(0)                       # run 1 starts and "dies": never ends
        self.begin(30)                      # run 2 sees it: ONE warning
        self.assertEqual(len(self.warn.sent), 1)
        title, message = self.warn.sent[0]
        self.assertIn("did not finish", title)
        self.assertIn("gigradar.log", message)
        self.assertTrue(read_state(self.path).warned)
        self.begin(60)                      # run 2 died as well: silence
        self.begin(90)
        self.assertEqual(len(self.warn.sent), 1)
        end_run(self.path, self.begin(120))  # a run completes: re-armed
        self.assertEqual(read_state(self.path).warned, False)
        self.begin(150)                     # this one dies ...
        self.begin(180)                     # ... and is reported
        self.assertEqual(len(self.warn.sent), 2)

    def test_a_run_still_in_progress_is_not_a_crash(self) -> None:
        self.begin(0, pid=100)
        self.begin(5, alive=ALIVE, pid=200)   # overlapping start, other pid alive, 5 min
        self.assertEqual(self.warn.sent, [])

    def test_alive_pid_but_too_old_is_a_crash(self) -> None:
        self.begin(0, pid=100)                # pid reuse: alive now, but started long ago
        self.begin(STILL_RUNNING_FOR.seconds // 60 + 1, alive=ALIVE, pid=200)
        self.assertEqual(len(self.warn.sent), 1)

    def test_failed_warning_is_retried_next_run(self) -> None:
        self.begin(0)
        self.warn.fail = True
        self.begin(30)                        # warning fails: logged, run continues
        self.assertFalse(read_state(self.path).warned)
        self.warn.fail = False
        self.begin(60)                        # retried and delivered
        self.assertEqual(len(self.warn.sent), 1)
        self.assertTrue(read_state(self.path).warned)

    def test_corrupt_marker_is_replaced_without_a_warning(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertLogs("gigradar", level="WARNING"):
            self.begin(0)
        self.assertEqual(self.warn.sent, [])
        self.assertEqual(read_state(self.path).started, NOW.isoformat())

    def test_marker_with_missing_keys_counts_as_no_marker(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"started": NOW.isoformat()}), encoding="utf-8")
        with self.assertLogs("gigradar", level="WARNING"):
            self.assertIsNone(read_state(self.path))

    def test_unwritable_marker_does_not_stop_the_run(self) -> None:
        blocker = self.dir / "file"
        blocker.write_text("x", encoding="utf-8")
        path = blocker / STATE_FILE            # parent is a file: every write fails
        with self.assertLogs("gigradar", level="ERROR"):
            state = begin_run(path, self.warn, NOW, 100, DEAD)
        self.assertIsNone(state)
        end_run(path, state)                   # no-op, no error

    def test_state_path_is_next_to_the_store(self) -> None:
        self.assertEqual(state_path(Path("data") / "gigradar.db"), Path("data") / STATE_FILE)

    def test_write_is_atomic_replace(self) -> None:
        write_state(self.path, RunState("a", 1, False, False))
        with mock.patch("gigradar.runstate.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                write_state(self.path, RunState("b", 2, True, True))
        self.assertEqual(read_state(self.path), RunState("a", 1, False, False))  # old marker intact


class PidAliveTest(unittest.TestCase):
    def test_own_pid_is_alive_and_a_finished_child_is_not(self) -> None:
        self.assertTrue(pid_alive(os.getpid()))
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertFalse(pid_alive(child.pid))


class WarningChannelsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.log = LogNotifier()
        self.toast = object.__new__(ToastNotifier)   # skip __init__: it needs windows-toasts
        self.telegram = TelegramNotifier("tok", "chat", lambda url, body: (200, b"{}"))

    def test_log_and_telegram_but_not_toast(self) -> None:
        chosen = warning_notifier(MultiNotifier([self.log, self.toast, self.telegram]))
        self.assertEqual(chosen.notifiers, [self.log, self.telegram])

    def test_toast_only_when_telegram_is_not_configured(self) -> None:
        chosen = warning_notifier(MultiNotifier([self.log, self.toast]))
        self.assertEqual(chosen.notifiers, [self.log, self.toast])

    def test_log_only_when_nothing_else_is_configured(self) -> None:
        self.assertEqual(warning_notifier(MultiNotifier([self.log])).notifiers, [self.log])


class MainIntegrationTest(unittest.TestCase):
    """main() with a store that fails to open (exit 1 after begin_run): the marker flow still runs."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "gigradar.toml").write_text(
            TOML + '[store]\npath = "x.db"\n[search]\nbackend = "curl"\n', encoding="utf-8")
        conn = sqlite3.connect(str(self.dir / "x.db"))
        conn.execute("PRAGMA user_version = 99")
        conn.close()
        self.marker = self.dir / STATE_FILE
        self.warn = FakeWarn()

    def tearDown(self) -> None:
        reset_logging()
        self._tmp.cleanup()

    def run_main(self) -> int:
        with mock.patch("gigradar.watch.warning_notifier", lambda notifier: self.warn):
            return main(["--config", str(self.dir / "gigradar.toml")])

    def test_dead_unfinished_marker_gives_one_warning_then_silence_until_completion(self) -> None:
        write_state(self.marker, RunState(NOW.isoformat(), 2 ** 22 + 7, False, False))  # dead pid, old
        self.assertEqual(self.run_main(), EXIT_ERROR)
        self.assertEqual(len(self.warn.sent), 1)
        done = read_state(self.marker)       # a handled failure (exit 1) still counts as completed
        self.assertEqual((done.completed, done.warned), (True, False))
        write_state(self.marker, RunState(NOW.isoformat(), 2 ** 22 + 7, False, True))    # crashed again, warned
        self.run_main()
        self.assertEqual(len(self.warn.sent), 1)                                          # silent
        self.assertTrue(read_state(self.marker).completed)

    def test_normal_runs_never_warn(self) -> None:
        self.run_main()
        self.run_main()
        self.assertEqual(self.warn.sent, [])


if __name__ == "__main__":
    unittest.main()
