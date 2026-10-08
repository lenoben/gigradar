"""Run: .venv/Scripts/python -m unittest discover -s tests   (stdlib only, no network)"""

import contextlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gigradar.score import Score
from gigradar.store import (SCHEMA_VERSION, load_labels_since, save_label, delete_scores, score_rows, StoreError, established_searches, filter_new, job_id, load_scores,
                            mark_seen, open_existing, open_store, save_job_score, seen_count, stored_job)
from upwork_search import Job

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def make_job(cipher: str) -> Job:
    url = f"https://www.upwork.com/jobs/{cipher}" if cipher else ""
    return Job(title=f"job {cipher}", url=url, job_type="HOURLY", published="2026-10-07T10:00:00Z",
               hourly_min="50.0", hourly_max="80.0", fixed_budget=None, tier="ExpertLevel",
               skills="Python", description="desc")


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_store(Path(":memory:"), [], NOW)

    def tearDown(self) -> None:
        self.conn.close()

    def test_job_id_from_url(self) -> None:
        self.assertEqual(job_id(make_job("~021abc")), "~021abc")
        self.assertIsNone(job_id(make_job("")))

    def test_first_run_everything_is_new(self) -> None:
        jobs = [make_job("~01"), make_job("~02")]
        self.assertEqual(filter_new(self.conn, jobs), jobs)

    def test_seen_jobs_are_filtered(self) -> None:
        mark_seen(self.conn, [make_job("~01")], NOW)
        new = filter_new(self.conn, [make_job("~01"), make_job("~02")])
        self.assertEqual([job_id(j) for j in new], ["~02"])

    def test_batch_duplicates_and_idless_jobs_dropped(self) -> None:
        new = filter_new(self.conn, [make_job("~01"), make_job("~01"), make_job("")])
        self.assertEqual([job_id(j) for j in new], ["~01"])

    def test_mark_seen_is_idempotent(self) -> None:
        jobs = [make_job("~01"), make_job("~02")]
        self.assertEqual(mark_seen(self.conn, jobs, NOW), 2)
        self.assertEqual(mark_seen(self.conn, jobs, NOW), 0)
        self.assertEqual(seen_count(self.conn), 2)

    def test_persists_across_connections_and_creates_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "gigradar.db"
            conn = open_store(path, [], NOW)
            mark_seen(conn, [make_job("~01")], NOW)
            conn.close()
            conn = open_store(path, [], NOW)
            self.assertEqual(filter_new(conn, [make_job("~01")]), [])
            conn.close()

    def test_rejects_newer_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gigradar.db"
            conn = open_store(path, [], NOW)
            conn.execute("PRAGMA user_version = 99")
            conn.close()
            with self.assertRaises(StoreError):
                open_store(path, [], NOW)

    def test_new_store_is_v2_without_adoption(self) -> None:
        self.assertEqual(user_version(self.conn), 2)
        self.assertEqual(tables(self.conn), V2_TABLES)
        self.assertEqual(established_searches(self.conn), set())


# Frozen copy of the v1 schema (as shipped in Phase 1); never import it from gigradar.store.
V1_SQL = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    job_id     TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    url        TEXT NOT NULL,
    title      TEXT NOT NULL,
    published  TEXT,
    payload    TEXT NOT NULL
);
"""
V2_TABLES = ["embeddings", "job_search", "labels", "scores", "search_runs", "seen_jobs"]


def make_v1_store(path: Path, ciphers: list[str]) -> None:
    """A Phase 1 database: seen_jobs only, user_version 1."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    with conn:
        conn.executescript(V1_SQL)
        conn.executemany("INSERT INTO seen_jobs VALUES (?, ?, ?, ?, ?, ?)",
                         [(c, "2026-10-07T09:00:00+00:00", f"https://www.upwork.com/jobs/{c}", f"job {c}",
                           None, json.dumps({"title": f"job {c}"})) for c in ciphers])
        conn.execute("PRAGMA user_version = 1")
    conn.close()


def user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]


def seen_rows(path: Path) -> list[tuple]:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT * FROM seen_jobs ORDER BY job_id").fetchall()
    finally:
        conn.close()


class MigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "data" / "gigradar.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def backups(self) -> list[Path]:
        return sorted(self.path.parent.glob("*.bak"))

    def test_v1_to_v2_keeps_rows_and_adopts_searches(self) -> None:
        make_v1_store(self.path, ["~01", "~02"])
        before = seen_rows(self.path)
        conn = open_store(self.path, ["py", "next", "py"], NOW)
        try:
            self.assertEqual(user_version(conn), 2)
            self.assertEqual(tables(conn), V2_TABLES)
            self.assertEqual(established_searches(conn), {"py", "next"})
            self.assertEqual(filter_new(conn, [make_job("~01"), make_job("~02"), make_job("~03")]),
                             [make_job("~03")])
        finally:
            conn.close()
        self.assertEqual(seen_rows(self.path), before)  # byte-identical rows

    def test_backup_is_a_v1_copy(self) -> None:
        make_v1_store(self.path, ["~01", "~02"])
        before = seen_rows(self.path)
        open_store(self.path, [], NOW).close()
        [bak] = self.backups()
        self.assertEqual(bak.name, "gigradar.db.v1-20261007T120000Z.bak")
        self.assertEqual(seen_rows(bak), before)
        conn = sqlite3.connect(str(bak))
        try:
            self.assertEqual((user_version(conn), tables(conn)), (1, ["seen_jobs"]))
        finally:
            conn.close()

    def test_failed_migration_rolls_back(self) -> None:
        make_v1_store(self.path, ["~01"])
        conn = sqlite3.connect(str(self.path))
        conn.execute("CREATE TABLE scores (unrelated TEXT)")  # makes CREATE TABLE scores fail
        conn.close()
        before = seen_rows(self.path)
        with self.assertRaises(sqlite3.OperationalError):
            open_store(self.path, ["py"], NOW)
        conn = sqlite3.connect(str(self.path))
        try:
            self.assertEqual(user_version(conn), 1)
            self.assertEqual(tables(conn), ["scores", "seen_jobs"])  # no half-created tables
        finally:
            conn.close()
        self.assertEqual(seen_rows(self.path), before)

    def test_existing_backup_is_never_overwritten(self) -> None:
        make_v1_store(self.path, ["~01"])
        bak = self.path.with_name("gigradar.db.v1-20261007T120000Z.bak")
        bak.write_bytes(b"older backup")
        with self.assertRaises(StoreError):
            open_store(self.path, [], NOW)
        self.assertEqual(bak.read_bytes(), b"older backup")
        conn = sqlite3.connect(str(self.path))
        try:
            self.assertEqual(user_version(conn), 1)  # no backup -> no migration
        finally:
            conn.close()

    def test_reopen_v2_is_idempotent_and_makes_no_backup(self) -> None:
        open_store(self.path, ["py"], NOW).close()  # new store: v2, nothing adopted
        conn = open_store(self.path, ["py"], NOW)
        try:
            self.assertEqual((user_version(conn), established_searches(conn)), (2, set()))
        finally:
            conn.close()
        self.assertEqual(self.backups(), [])

    def test_unversioned_seen_jobs_file_is_treated_as_v1(self) -> None:
        make_v1_store(self.path, ["~01"])
        conn = sqlite3.connect(str(self.path))
        conn.execute("PRAGMA user_version = 0")
        conn.close()
        conn = open_store(self.path, ["py"], NOW)
        try:
            self.assertEqual((user_version(conn), seen_count(conn)), (2, 1))
            self.assertEqual(established_searches(conn), {"py"})
        finally:
            conn.close()
        self.assertEqual(len(self.backups()), 1)


class JobScoreHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_store(Path(":memory:"), [], NOW)
        mark_seen(self.conn, [make_job("~01")], NOW)

    def tearDown(self) -> None:
        self.conn.close()

    def test_stored_job(self) -> None:
        self.assertEqual(stored_job(self.conn, "~01").title, "job ~01")
        self.assertIsNone(stored_job(self.conn, "~nope"))

    def test_save_and_load_scores_per_scorer_and_version(self) -> None:
        save_job_score(self.conn, "~01", Score(40, "a", "claude", "1"), NOW)
        save_job_score(self.conn, "~01", Score(90, "b", "claude", "1"), NOW)   # same key: newest wins
        save_job_score(self.conn, "~01", Score(10, "old", "claude", "0"), NOW)
        save_job_score(self.conn, "~01", Score(70, "e", "embed", "2"), NOW)
        self.assertEqual(load_scores(self.conn, "claude", "1"), {"~01": Score(90, "b", "claude", "1")})
        self.assertEqual(load_scores(self.conn, "claude", "0"), {"~01": Score(10, "old", "claude", "0")})
        self.assertEqual(load_scores(self.conn, "embed", "2"), {"~01": Score(70, "e", "embed", "2")})
        self.assertEqual(load_scores(self.conn, "claude", "9"), {})


class DeleteScoresTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_store(Path(":memory:"), [], NOW)
        mark_seen(self.conn, [make_job("~01"), make_job("~02")], NOW)
        for jid, scorer, version, value in (("~01", "claude", "1", 10), ("~02", "claude", "1", 20),
                                            ("~02", "claude", "2", 30), ("~01", "embed", "2", 40)):
            save_job_score(self.conn, jid, Score(value, "r", scorer, version), NOW)

    def tearDown(self) -> None:
        self.conn.close()

    def test_score_rows_per_scorer_and_version(self) -> None:
        self.assertEqual([(r[0], r[2], r[3]) for r in score_rows(self.conn, "claude", None)],
                         [("~01", "1", 10), ("~02", "1", 20), ("~02", "2", 30)])
        self.assertEqual([r[0] for r in score_rows(self.conn, "claude", "2")], ["~02"])
        self.assertEqual(score_rows(self.conn, "claude", "9"), [])

    def test_delete_one_version_only(self) -> None:
        self.assertEqual(delete_scores(self.conn, "claude", "1"), 2)
        self.assertEqual(len(score_rows(self.conn, "claude", None)), 1)
        self.assertEqual(len(score_rows(self.conn, "embed", None)), 1)

    def test_delete_all_versions_of_one_scorer_leaves_the_others(self) -> None:
        self.assertEqual(delete_scores(self.conn, "claude", None), 3)
        self.assertEqual(score_rows(self.conn, "claude", None), [])
        self.assertEqual(len(score_rows(self.conn, "embed", None)), 1)
        self.assertEqual(delete_scores(self.conn, "claude", None), 0)


class LabelsSinceTest(unittest.TestCase):
    def test_inclusive_by_timestamp(self) -> None:
        conn = open_store(Path(":memory:"), [], NOW)
        for jid, label, day in (("~01", 1, 6), ("~02", -1, 7), ("~03", 1, 8), ("~04", -1, 9)):
            save_label(conn, jid, label, datetime(2026, 10, day, 0 if jid == "~03" else 12, 0, tzinfo=timezone.utc))
        since = datetime(2026, 10, 8, tzinfo=timezone.utc)
        self.assertEqual(load_labels_since(conn, since), {"~03": 1, "~04": -1})      # 10-08 00:00 is included
        self.assertEqual(load_labels_since(conn, datetime(2026, 10, 10, tzinfo=timezone.utc)), {})
        self.assertEqual(len(load_labels_since(conn, datetime(2026, 1, 1, tzinfo=timezone.utc))), 4)
        conn.close()


class OpenExistingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_opens_a_current_store(self) -> None:
        open_store(self.dir / "ok.db", [], NOW).close()
        conn = open_existing(self.dir / "ok.db", 1.0)
        self.assertEqual(seen_count(conn), 0)
        conn.close()

    def test_never_creates_or_migrates(self) -> None:
        missing = self.dir / "missing.db"
        with self.assertRaises(StoreError):
            open_existing(missing, 1.0)
        self.assertFalse(missing.exists())
        old = self.dir / "old.db"
        with contextlib.closing(sqlite3.connect(old)) as raw:
            raw.execute("CREATE TABLE seen_jobs (job_id TEXT)")
            raw.execute("PRAGMA user_version = 1")
        with self.assertRaises(StoreError):
            open_existing(old, 1.0)
        with contextlib.closing(sqlite3.connect(old)) as raw:
            self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], 1)   # untouched
        self.assertEqual(SCHEMA_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
