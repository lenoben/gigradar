"""Run: .venv/Scripts/python -m unittest discover -s tests   (offline)

Round 2 of the desktop app: jobs below the minimum score are counted in the message and hidden in the list, and old
jobs are pruned when (and only when) the configuration has a [retention] table."""

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gigradar import appsettings
from gigradar.config import ConfigError, load_config
from gigradar.notify import Alert, alerts_link, below_text, summarize_alerts
from gigradar.score import Score
from gigradar.store import mark_seen, open_store, save_job_score, save_label, seen_count, stored_job
from gigradar.telegram import format_digest
from gigradar.watch import EXIT_OK, run_watch
from test_app_m3 import ANSWERS, write_home
from test_cli import NOW, CliCase, make_job
from test_telegram import FakePost, alert as plain_alert, notifier
from test_watch import TOML, FakeNotifier, FakeScoreFn, FakeSearcher, job

RETENTION = "\n[retention]\nkeep_days = 30\nkeep_rated = true\n"


class RetentionConfigTest(unittest.TestCase):
    def load(self, extra: str):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "gigradar.toml").write_text(TOML + extra, encoding="utf-8")
        return load_config(tmp / "gigradar.toml", {}).retention

    def test_without_the_table_nothing_is_ever_pruned(self) -> None:
        self.assertIsNone(self.load(""))

    def test_values(self) -> None:
        r = self.load(RETENTION)
        self.assertEqual((r.keep_days, r.keep_rated), (30, True))
        r = self.load("[retention]\nkeep_days = 0\nkeep_rated = false\n")
        self.assertEqual((r.keep_days, r.keep_rated), (0, False))
        self.assertEqual(self.load("[retention]\n").keep_days, 0)

    def test_invalid_values_are_refused(self) -> None:
        for bad in ("keep_days = -1", 'keep_days = "30"', "keep_rated = 1", "keep = 30"):
            with self.subTest(bad), self.assertRaises(ConfigError):
                self.load(f"[retention]\n{bad}\n")


def alert(cipher: str, value: int | None = None) -> Alert:
    return plain_alert(cipher, None if value is None else Score(value, "Web", "embed", "2"))


class BelowMinimumMessageTest(unittest.TestCase):
    def test_the_count_includes_the_jobs_left_out_and_says_how_many(self) -> None:
        title, message = summarize_alerts([alert("~1", 82)], 2)
        self.assertEqual(title, "Gigradar: 3 new jobs (top 82)")
        self.assertEqual(message, "2 below your minimum score\n• 82 Job ~1")
        self.assertEqual(below_text(0), "")

    def test_a_single_listed_job_with_others_left_out_opens_the_list_of_that_run(self) -> None:
        one = [Alert(make_job("~1"), None)]
        self.assertIn("gigradar://job/", alerts_link(one, "R", 0))
        self.assertIn("gigradar://jobs?run=", alerts_link(one, "R", 2))

    def test_telegram_briefs_start_with_the_count_and_the_digest_header_carries_it(self) -> None:
        post = FakePost([])
        n, _ = notifier(post)
        n.notify_jobs([alert("~1", 82)], "run", 2)
        self.assertEqual(len(post.calls), 2)
        self.assertEqual(post.calls[0][1]["text"], "<b>Gigradar: 3 new jobs</b> — 2 below your minimum score")
        self.assertTrue(post.calls[1][1]["text"].startswith("<b>Job ~1</b>"))
        [digest] = format_digest([alert(f"~{i}", 50) for i in range(7)], 3)
        self.assertTrue(digest.startswith("<b>Gigradar: 10 new jobs</b> — 3 below your minimum score"))

    def test_nothing_left_out_changes_nothing(self) -> None:
        post = FakePost([])
        n, _ = notifier(post)
        n.notify_jobs([alert("~1", 82)], "run", 0)
        self.assertEqual(len(post.calls), 1)


class WatchBelowAndPruneTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        (self.dir / "gigradar.toml").write_text(TOML, encoding="utf-8")
        self.cfg = load_config(self.dir / "gigradar.toml", {})
        self.conn = open_store(Path(":memory:"), [], NOW)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO search_runs VALUES ('a', 'x', 'x'), ('b', 'x', 'x')")
        self.conn.commit()

    def run_watch(self, outcomes, notifier=None):
        notifier = notifier or FakeNotifier(False)
        searcher = FakeSearcher(outcomes, None)
        return run_watch(self.cfg, searcher, notifier, self.conn, NOW, FakeScoreFn(), lambda: 0.0), notifier

    def test_the_notifier_is_told_how_many_were_left_out(self) -> None:
        self.cfg = replace(self.cfg, scoring=replace(self.cfg.scoring, min_score=50))
        belows = []

        class Spy(FakeNotifier):
            def notify_jobs(self, alerts, run, below):
                belows.append(below)
                super().notify_jobs(alerts, run, below)

        _, notifier = self.run_watch({"a": [job("~3"), job("~9")], "b": [job("~1")]}, Spy(False))
        self.assertEqual((notifier.batches, belows), ([["~9"]], [2]))

    def test_old_jobs_are_pruned_only_with_a_retention_table(self) -> None:
        mark_seen(self.conn, [make_job("~11")], NOW - timedelta(days=100))
        self.run_watch({"a": [], "b": []})
        self.assertEqual(seen_count(self.conn), 1)                      # no [retention]: kept
        self.cfg = replace(self.cfg, retention=load_config_retention(self.dir, RETENTION))
        self.run_watch({"a": [], "b": []})
        self.assertEqual(seen_count(self.conn), 0)
        self.run_watch({"a": [make_job("~11")], "b": []})              # found again: remembered, no alert
        self.assertEqual(seen_count(self.conn), 0)

    def test_forever_prunes_nothing_and_rated_jobs_survive(self) -> None:
        mark_seen(self.conn, [make_job("~11"), make_job("~12")], NOW - timedelta(days=100))
        save_label(self.conn, "~12", 1, NOW)
        self.cfg = replace(self.cfg, retention=load_config_retention(self.dir, "[retention]\nkeep_days = 0\n"))
        self.run_watch({"a": [], "b": []})
        self.assertEqual(seen_count(self.conn), 2)
        self.cfg = replace(self.cfg, retention=load_config_retention(self.dir, RETENTION))
        _, _ = self.run_watch({"a": [], "b": []})
        self.assertIsNotNone(stored_job(self.conn, "~12"))
        self.assertIsNone(stored_job(self.conn, "~11"))


def load_config_retention(directory: Path, extra: str):
    (directory / "gigradar.toml").write_text(TOML + extra, encoding="utf-8")
    return load_config(directory / "gigradar.toml", {}).retention


class StorageSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def test_a_config_without_the_table_reads_as_forever_and_is_not_changed_by_other_sections(self) -> None:
        write_home(self.home)
        storage = appsettings.read_settings(self.home)["storage"]
        self.assertEqual(storage, {"keep_days": None, "keep_rated": True})
        appsettings.save_section(self.home, "schedule", {"interval_minutes": 60}, NOW, {})
        self.assertNotIn("retention", (self.home / "gigradar.toml").read_text(encoding="utf-8"))

    def test_saving_writes_the_table_and_the_other_sections_stay(self) -> None:
        write_home(self.home, {**ANSWERS, "retention": {"keep_days": 30, "keep_rated": True}})
        before = appsettings.read_settings(self.home)
        self.assertEqual(before["storage"], {"keep_days": 30, "keep_rated": True})
        appsettings.save_section(self.home, "storage", {"keep_days": 7, "keep_rated": False}, NOW, {})
        after = appsettings.read_settings(self.home)
        self.assertEqual(after["storage"], {"keep_days": 7, "keep_rated": False})
        for section in ("profile", "preferences", "searches", "notifications", "schedule"):
            self.assertEqual(after[section], before[section], section)
        self.assertEqual(load_config(self.home / "gigradar.toml", {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "1"}).retention.keep_days, 7)

    def test_only_the_offered_choices_are_accepted(self) -> None:
        write_home(self.home)
        for days in (0, 7, 14, 30):
            appsettings.save_section(self.home, "storage", {"keep_days": days, "keep_rated": True}, NOW, {})
        for bad in ({"keep_days": 5, "keep_rated": True}, {"keep_days": True, "keep_rated": True},
                    {"keep_days": 30, "keep_rated": "yes"}, {"keep_days": 30}, {"keep_days": 30, "keep_rated": True, "x": 1}):
            with self.subTest(bad), self.assertRaises(appsettings.SettingsError):
                appsettings.save_section(self.home, "storage", bad, NOW, {})

    def test_the_minimum_score_is_read_saved_and_checked(self) -> None:
        write_home(self.home)
        self.assertEqual(appsettings.read_settings(self.home)["preferences"]["min_score"], 20)
        for score in (0, 80):
            appsettings.save_section(self.home, "preferences", {"min_score": score}, NOW, {})
            self.assertEqual(load_config(self.home / "gigradar.toml", {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "1"}).scoring.min_score, score)
        for bad in (-1, 81, 20.5, "20", True):
            with self.subTest(bad), self.assertRaises(appsettings.SettingsError):
                appsettings.save_section(self.home, "preferences", {"min_score": bad}, NOW, {})

    def test_a_config_without_the_key_shows_20_and_stays_without_it_until_preferences_are_saved(self) -> None:
        write_home(self.home, {k: v for k, v in ANSWERS.items() if k != "scoring"})
        text = (self.home / "gigradar.toml").read_text(encoding="utf-8")
        self.assertNotIn("min_score", text)
        self.assertEqual(appsettings.read_settings(self.home)["preferences"]["min_score"], 20)
        appsettings.save_section(self.home, "schedule", {"interval_minutes": 60}, NOW, {})
        self.assertNotIn("min_score", (self.home / "gigradar.toml").read_text(encoding="utf-8"))
        appsettings.save_section(self.home, "preferences", {"min_score": 20}, NOW, {})
        self.assertIn("min_score = 20", (self.home / "gigradar.toml").read_text(encoding="utf-8"))


class JobsListBelowTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_config("[scoring]\nmin_score = 20\n")
        conn = open_store(self.home / "data" / "gigradar.db", [], NOW)
        mark_seen(conn, [make_job("~hi"), make_job("~lo"), make_job("~na")], NOW)
        save_job_score(conn, "~hi", Score(80, "Web", "embed", "2"), NOW)
        save_job_score(conn, "~lo", Score(5, "Data", "embed", "2"), NOW)
        conn.close()

    def ids(self, *extra: str) -> set[str]:
        code, result = self.result("jobs", *extra)
        self.assertEqual(code, 0, result)
        return {j["id"] for j in result["jobs"]}

    def test_the_list_hides_jobs_below_the_minimum_unless_asked_and_unscored_ones_always_show(self) -> None:
        self.assertEqual(self.ids(), {"~hi", "~na"})
        self.assertEqual(self.ids("--include-below"), {"~hi", "~lo", "~na"})
        self.assertEqual(self.result("jobs")[1]["min_score"], 20)

    def test_the_limit_counts_only_the_jobs_that_are_shown(self) -> None:
        self.assertEqual(len(self.result("jobs", "--limit", "2")[1]["jobs"]), 2)
