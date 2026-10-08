"""Run: .venv/Scripts/python -m unittest discover -s tests   (offline: in-memory store, fake embedder)"""

import io
import tempfile
import unittest
from dataclasses import replace
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from gigradar.config import load_config
from gigradar.evaluate import DIFF_MIN, claude_diff, print_claude_diff, spearman
from gigradar.label import diff_report, eval_labels, main, parse_date
from gigradar.score import Score
from gigradar.store import mark_seen, open_store, save_job_score, save_label
from test_embed_score import PROFILE2, FakeEmbedder
from test_score import make_job

NOW = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
LONG_TITLE = "A very long job title that certainly goes beyond the sixty characters limit " * 2


def job(n: int, title: str):
    return make_job(url=f"https://www.upwork.com/jobs/~{n:02d}", title=title)


# ~00..~04 labeled and scored in both versions; ~05 unlabeled; ~06 labeled but only in version A.
JOBS = [job(0, "Zero"), job(1, "One"), job(2, LONG_TITLE), job(3, "Three"), job(4, "Four"),
        job(5, "Unlabeled"), job(6, "Only in A")]
LABELS = {"~00": 1, "~01": 1, "~02": -1, "~03": -1, "~04": 1, "~06": -1}
A = {"~00": 80, "~01": 60, "~02": 30, "~03": 50, "~04": 70, "~05": 99, "~06": 10}
B = {"~00": 55, "~01": 62, "~02": 60, "~03": 35, "~04": 90, "~05": 1}
EMBED = {"~00": 90, "~01": 70, "~02": 20, "~03": 40, "~04": 60, "~06": 5}


def scores(values: dict[str, int], version: str) -> dict[str, Score]:
    return {jid: Score(v, f"r{v}", "claude", version) for jid, v in values.items()}


class SpearmanTest(unittest.TestCase):
    def test_spearman(self) -> None:
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [1, 3, 2, 4]), 0.8)       # 1 - 6*2 / (4*15)
        self.assertAlmostEqual(spearman([1, 2, 2, 4], [1, 2, 3, 4]), 0.9486832980505138)  # ties handled
        self.assertIsNone(spearman([5, 5, 5], [1, 2, 3]))                          # constant: undefined
        self.assertIsNone(spearman([1], [1]))
        with self.assertRaises(ValueError):
            spearman([1, 2], [1])


class ClaudeDiffTest(unittest.TestCase):
    def result(self):
        return claude_diff(JOBS, LABELS, EMBED, scores(A, "1"), scores(B, "2"), "1", "2")

    def test_only_labeled_jobs_with_both_versions_are_compared(self) -> None:
        r = self.result()
        self.assertEqual((r.compared, r.labeled), (5, 6))   # ~05 unlabeled, ~06 lacks version B

    def test_rows_over_the_threshold_sorted_by_absolute_difference(self) -> None:
        r = self.result()
        self.assertEqual([(x.title[:5], x.diff) for x in r.rows],
                         [("A ver", 30), ("Zero", -25), ("Four", 20), ("Three", -15)])  # -15 is exactly the minimum
        self.assertNotIn("One", [x.title for x in r.rows])        # +2 is under the threshold
        self.assertEqual(DIFF_MIN, 15)
        self.assertEqual((r.rows[0].label, r.rows[0].score_a, r.rows[0].score_b, r.rows[0].embed), (-1, 30, 60, 20))

    def test_summaries_use_the_compared_jobs(self) -> None:
        a, b = self.result().summaries
        self.assertEqual((a.version, a.mean_good, a.mean_bad), ("1", 70.0, 40.0))
        self.assertEqual((b.version, b.mean_good, b.mean_bad), ("2", (55 + 62 + 90) / 3, 47.5))
        self.assertAlmostEqual(a.spearman, 0.9)    # hand-computed: 1 - 6*2 / (5*24)
        self.assertAlmostEqual(b.spearman, 0.1)    # 1 - 6*18 / (5*24)

    def test_output_table(self) -> None:
        out = io.StringIO()
        print_claude_diff(self.result(), "1", "2", out)
        lines = out.getvalue().splitlines()
        self.assertIn("version 1 vs 2: 5 of 6 labeled jobs have both", lines[0])
        header = next(i for i, line in enumerate(lines) if line.startswith("label"))
        self.assertEqual(lines[header].split(), ["label", "v1", "v2", "diff", "embed", "title"])
        first = lines[header + 1]
        self.assertTrue(first.startswith("👎"))
        self.assertIn("+30", first)
        self.assertTrue(first.rstrip().endswith("…"))
        self.assertEqual(len(first.split("  ")[-1].strip()), 60)              # title cut to 60 characters
        self.assertTrue(lines[header + 2].startswith("👍"))
        self.assertIn("-25", lines[header + 2])
        self.assertIn("version 1: mean claude score 👍 70.0, 👎 40.0; Spearman with embedding +0.90", lines[-2])
        self.assertIn("version 2: mean claude score 👍 69.0, 👎 47.5; Spearman with embedding +0.10", lines[-1])

    def test_nothing_in_common(self) -> None:
        r = claude_diff(JOBS, LABELS, EMBED, scores(A, "1"), {}, "1", "2")
        out = io.StringIO()
        print_claude_diff(r, "1", "2", out)
        self.assertEqual(r.compared, 0)
        self.assertIn("nothing to compare", out.getvalue())

    def test_no_row_over_the_threshold(self) -> None:
        same = scores(A, "2")
        r = claude_diff(JOBS, LABELS, EMBED, scores(A, "1"), same, "1", "2")
        out = io.StringIO()
        print_claude_diff(r, "1", "2", out)
        self.assertEqual(r.rows, [])
        self.assertIn("(none)", out.getvalue())


class DiffReportTest(unittest.TestCase):
    """The whole command on a tiny in-memory store: it reads, and writes nothing."""

    def setUp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gigradar.toml"
            path.write_text('[[searches]]\nname = "a"\n', encoding="utf-8")
            self.scoring = replace(load_config(path, {}).scoring, cos_low=0.0, cos_high=1.0)
        self.conn = open_store(Path(":memory:"), [], NOW)
        mark_seen(self.conn, JOBS, NOW)
        for jid, label in LABELS.items():
            save_label(self.conn, jid, label, NOW - timedelta(days=2) if jid in ("~00", "~01", "~02") else NOW)
        for version, values in (("1", A), ("2", B)):
            for jid, value in values.items():
                save_job_score(self.conn, jid, Score(value, "r", "claude", version), NOW)
        self.conn.execute("PRAGMA query_only = ON")

    def tearDown(self) -> None:
        self.conn.close()

    def counts(self) -> dict[str, int]:
        return {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("seen_jobs", "scores", "labels", "embeddings", "job_search", "search_runs")}

    def test_prints_the_comparison_and_writes_nothing(self) -> None:
        before = self.counts()
        out = io.StringIO()
        diff_report(self.conn, PROFILE2, self.scoring, FakeEmbedder("fake"), "1", "2", None, out)
        text = out.getvalue()
        self.assertIn("claude scores in the store: version 1: 7, version 2: 6", text)
        self.assertIn("5 of 6 labeled jobs have both", text)
        self.assertIn("Spearman with embedding", text)
        self.assertEqual(self.counts(), before)       # no embeddings cached, nothing else touched
        self.assertEqual(before["embeddings"], 0)

    def test_labeled_after_restricts_the_labels(self) -> None:
        out = io.StringIO()
        since = datetime(2026, 10, 8, tzinfo=timezone.utc)      # NOW is 10-08 15:00; ~00..~02 were labeled 10-06
        diff_report(self.conn, PROFILE2, self.scoring, FakeEmbedder("fake"), "1", "2", since, out)
        text = out.getvalue()
        self.assertIn("labels created on or after 2026-10-08 (UTC) only: 3 of 6", text)
        self.assertIn("2 of 3 labeled jobs have both", text)        # ~03, ~04 (~06 lacks version B)
        self.assertNotIn("Zero", text)                              # an old label's job is gone from the table
        self.assertEqual(self.counts()["embeddings"], 0)

    def test_without_labels_it_still_runs(self) -> None:
        self.conn.execute("PRAGMA query_only = OFF")
        self.conn.execute("DELETE FROM labels")
        self.conn.execute("PRAGMA query_only = ON")
        out = io.StringIO()
        diff_report(self.conn, PROFILE2, self.scoring, FakeEmbedder("fake"), "1", "2", None, out)
        self.assertIn("nothing to compare", out.getvalue())


class LabeledAfterTest(unittest.TestCase):
    def test_parse_date(self) -> None:
        self.assertEqual(parse_date("2026-10-22"), datetime(2026, 10, 22, tzinfo=timezone.utc))
        for bad in ("22.10.2026", "2026-13-01", "2026-10", "tomorrow", ""):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                parse_date(bad)

    def test_eval_labels(self) -> None:
        conn = open_store(Path(":memory:"), [], NOW)
        save_label(conn, "~01", 1, datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc))
        save_label(conn, "~02", -1, datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc))
        out = io.StringIO()
        self.assertEqual(eval_labels(conn, None, out), {"~01": 1, "~02": -1})
        self.assertEqual(out.getvalue(), "")                        # no filter: no extra output
        self.assertEqual(eval_labels(conn, datetime(2026, 10, 8, tzinfo=timezone.utc), out), {"~02": -1})
        self.assertIn("2026-10-08 (UTC) only: 1 of 2", out.getvalue())
        conn.close()


class ArgumentsTest(unittest.TestCase):
    def test_labeled_after_needs_eval_and_a_real_date(self) -> None:
        for argv in (["--labeled-after", "2026-10-22"], ["--eval", "--labeled-after", "soon"]):
            with self.subTest(argv=argv), mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as ctx:
                main(argv)
            self.assertEqual(ctx.exception.code, 2)

    def test_needs_eval_and_two_different_versions(self) -> None:
        for argv in (["--claude-diff", "1", "2"], ["--eval", "--claude-diff", "1", "1"], ["--eval", "--claude-diff", "1"]):
            with self.subTest(argv=argv), mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as ctx:
                main(argv)
            self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
