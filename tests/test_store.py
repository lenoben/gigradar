"""Run: .venv/Scripts/python -m unittest discover -s tests   (stdlib only, no network)"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gigradar.store import filter_new, job_id, mark_seen, open_store, seen_count
from upwork_search import Job

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def make_job(cipher: str) -> Job:
    url = f"https://www.upwork.com/jobs/{cipher}" if cipher else ""
    return Job(title=f"job {cipher}", url=url, job_type="HOURLY", published="2026-10-07T10:00:00Z",
               hourly_min="50.0", hourly_max="80.0", fixed_budget=None, tier="ExpertLevel",
               skills="Python", description="desc")


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_store(Path(":memory:"))

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
            conn = open_store(path)
            mark_seen(conn, [make_job("~01")], NOW)
            conn.close()
            conn = open_store(path)
            self.assertEqual(filter_new(conn, [make_job("~01")]), [])
            conn.close()

    def test_rejects_newer_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gigradar.db"
            conn = open_store(path)
            conn.execute("PRAGMA user_version = 99")
            conn.close()
            with self.assertRaises(RuntimeError):
                open_store(path)


if __name__ == "__main__":
    unittest.main()
