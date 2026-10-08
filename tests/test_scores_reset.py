"""Run: .venv/Scripts/python -m unittest discover -s tests   (offline, temp store)"""

import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gigradar.scores_reset import main, reset
from gigradar.score import Score
from gigradar.store import load_labels, mark_seen, open_store, save_job_score, save_label, score_rows, seen_count
from test_store import make_job

NOW = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)


class ResetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.db = self.dir / "data" / "gigradar.db"
        self.conn = open_store(self.db, [], NOW)
        mark_seen(self.conn, [make_job("~01"), make_job("~02")], NOW)
        save_label(self.conn, "~01", 1, NOW)
        for jid, scorer, version, value in (("~01", "claude", "1", 10), ("~02", "claude", "1", 20),
                                            ("~02", "claude", "2", 30), ("~01", "embed", "2", 40)):
            save_job_score(self.conn, jid, Score(value, "because", scorer, version), NOW)
        self.out = io.StringIO()

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def backups(self) -> list[Path]:
        return sorted(self.db.parent.glob("claude-scores-backup-*.json"))

    def test_yes_deletes_all_claude_versions_after_writing_a_backup(self) -> None:
        self.assertEqual(reset(self.conn, self.db, None, True, self.refuse, NOW, self.out), 3)
        self.assertEqual(score_rows(self.conn, "claude", None), [])
        [backup] = self.backups()
        rows = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual([(r["job_id"], r["version"], r["value"], r["reason"]) for r in rows],
                         [("~01", "1", 10, "because"), ("~02", "1", 20, "because"), ("~02", "2", 30, "because")])
        self.assertIn("deleted 3", self.out.getvalue())

    def test_only_the_claude_scorer_is_touched(self) -> None:
        reset(self.conn, self.db, None, True, self.refuse, NOW, self.out)
        self.assertEqual(len(score_rows(self.conn, "embed", None)), 1)
        self.assertEqual(load_labels(self.conn), {"~01": 1})
        self.assertEqual(seen_count(self.conn), 2)

    def test_one_version_only(self) -> None:
        self.assertEqual(reset(self.conn, self.db, "1", True, self.refuse, NOW, self.out), 2)
        self.assertEqual([r[2] for r in score_rows(self.conn, "claude", None)], ["2"])

    def test_declining_deletes_and_writes_nothing(self) -> None:
        self.assertEqual(reset(self.conn, self.db, None, False, lambda prompt: "n", NOW, self.out), 0)
        self.assertEqual(len(score_rows(self.conn, "claude", None)), 3)
        self.assertEqual(self.backups(), [])
        self.assertIn("nothing deleted", self.out.getvalue())

    def test_confirming_deletes(self) -> None:
        prompts = []
        self.assertEqual(reset(self.conn, self.db, None, False, lambda prompt: prompts.append(prompt) or "y", NOW,
                               self.out), 3)
        self.assertIn("Delete 3", prompts[0])

    def test_nothing_to_delete(self) -> None:
        self.assertEqual(reset(self.conn, self.db, "9", True, self.refuse, NOW, self.out), 0)
        self.assertEqual(self.backups(), [])
        self.assertIn("no 'claude' scores", self.out.getvalue())

    def test_existing_backup_is_never_overwritten(self) -> None:
        reset(self.conn, self.db, "1", True, self.refuse, NOW, self.out)
        before = self.backups()[0].read_text(encoding="utf-8")
        from gigradar.store import StoreError
        with self.assertRaises(StoreError):
            reset(self.conn, self.db, None, True, self.refuse, NOW, self.out)   # same second: same file name
        self.assertEqual(self.backups()[0].read_text(encoding="utf-8"), before)
        self.assertEqual(len(score_rows(self.conn, "claude", None)), 1)          # nothing more deleted

    def test_main_with_a_missing_store_fails_clearly(self) -> None:
        (self.dir / "gigradar.toml").write_text('[[searches]]\nname = "a"\n[store]\npath = "nope/x.db"\n',
                                                encoding="utf-8")
        self.assertEqual(main(["--config", str(self.dir / "gigradar.toml"), "--yes"]), 2)

    def test_main_end_to_end(self) -> None:
        (self.dir / "gigradar.toml").write_text('[[searches]]\nname = "a"\n[store]\npath = "data/gigradar.db"\n',
                                                encoding="utf-8")
        self.conn.close()
        self.assertEqual(main(["--config", str(self.dir / "gigradar.toml"), "--yes", "--version", "1"]), 0)
        self.conn = open_store(self.db, [], NOW)
        self.assertEqual([r[2] for r in score_rows(self.conn, "claude", None)], ["2"])

    @staticmethod
    def refuse(prompt: str) -> str:
        raise AssertionError("must not ask: " + prompt)


if __name__ == "__main__":
    unittest.main()
