"""Run: .venv/Scripts/python -m unittest discover -s tests   (stdlib only, no network, fake embedder)"""

import io
import random
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from gigradar.config import ConfigError, load_config
from gigradar.evaluate import (MIN_PER_CLASS, Variant, auc, average_ranks, bm25, bootstrap_auc, bootstrap_diff, build_variants,
                               claude_variant, combined_variant, precision_at, report, suggest_min_score, threshold_rows)
from gigradar.label import mixed_order, render, session
from gigradar.score import MemoryEmbeddingCache, ReadOnlyEmbeddingCache, Score
from gigradar.store import delete_label, load_labels, mark_seen, open_store, save_label
from test_embed_score import PROFILE2, FakeEmbedder
from test_score import make_job

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def jobs(n: int):
    return [make_job(url=f"https://www.upwork.com/jobs/~{i:02d}", title=f"Job {i}") for i in range(n)]


def scores_for(js, values):
    return {j.url.rsplit("/", 1)[-1]: Score(v, f"reason {v}", "embed", "2") for j, v in zip(js, values)}


class Keys:
    """Injected input(): returns the next key, records prompts."""

    def __init__(self, *keys: str) -> None:
        self.keys, self.prompts = list(keys), 0

    def __call__(self, prompt: str) -> str:
        self.prompts += 1
        return self.keys.pop(0)


class LabelStoreTest(unittest.TestCase):
    def test_save_replace_delete(self) -> None:
        conn = open_store(Path(":memory:"), [], NOW)
        save_label(conn, "~01", 1, NOW)
        save_label(conn, "~02", -1, NOW)
        save_label(conn, "~01", -1, NOW)  # changed my mind
        self.assertEqual(load_labels(conn), {"~01": -1, "~02": -1})
        delete_label(conn, "~02")
        self.assertEqual(load_labels(conn), {"~01": -1})
        with self.assertRaises(ValueError):
            save_label(conn, "~03", 0, NOW)
        conn.close()


class OrderAndRenderTest(unittest.TestCase):
    def test_mixed_order_spans_the_range(self) -> None:
        js = jobs(9)
        order = mixed_order(js, scores_for(js, [10, 90, 50, 20, 80, 60, 30, 70, 40]))
        values = [{"~00": 10, "~01": 90, "~02": 50, "~03": 20, "~04": 80, "~05": 60, "~06": 30, "~07": 70,
                   "~08": 40}[j.url[-3:]] for j in order]
        self.assertEqual(values, [90, 60, 30, 80, 50, 20, 70, 40, 10])  # top, middle, bottom in turn

    def test_mixed_order_without_scores_is_newest_first(self) -> None:
        js = jobs(3)
        self.assertEqual(mixed_order(js, None), js[::-1])

    def test_render(self) -> None:
        job = make_job(skills="Rust, next js, Docker", description="word " * 400)
        text = render(job, {"rust", "nextjs"}, None, "[1/5]")
        self.assertIn("[1/5]  Backend API", text)
        self.assertIn("Skills: ✓Rust, ✓next js, Docker", text)
        self.assertIn("word …", text)
        self.assertNotIn("[score", text)
        self.assertIn("[score 82 · Web]", render(job, set(), Score(82, "Web", "embed", "2"), "[1/5]"))


class SessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_store(Path(":memory:"), [], NOW)
        self.js = jobs(4)
        self.scores = scores_for(self.js, [40, 30, 20, 10])

    def tearDown(self) -> None:
        self.conn.close()

    def run_session(self, keys: Keys, show_score: bool = False, limit: int = 50) -> str:
        out = io.StringIO()
        session(self.conn, self.js, self.scores, set(), show_score, limit, keys, out, lambda: NOW)
        return out.getvalue()

    def test_answers_saved_skip_and_quit(self) -> None:
        text = self.run_session(Keys("y", "s", "n", "q"))
        self.assertEqual(len(load_labels(self.conn)), 2)
        self.assertIn("saved 2 labels this session; total 1 👍 / 1 👎", text)
        self.assertNotIn("[score", text)  # hidden by default

    def test_resume_never_asks_a_labeled_job_again(self) -> None:
        self.run_session(Keys("y", "q"))
        first = set(load_labels(self.conn))
        keys = Keys("n", "n", "n")
        self.run_session(keys)
        self.assertEqual(keys.prompts, 3)  # the 3 remaining jobs only
        self.assertEqual(len(load_labels(self.conn)), 4)
        self.assertTrue(first <= set(load_labels(self.conn)))

    def test_undo_removes_and_asks_again(self) -> None:
        text = self.run_session(Keys("y", "u", "n", "q"))
        self.assertEqual(list(load_labels(self.conn).values()), [-1])
        self.assertIn("(undone: asking that job again)", text)

    def test_unknown_key_and_empty_undo_reprompt(self) -> None:
        keys = Keys("u", "x", "y", "q")
        text = self.run_session(keys)
        self.assertIn("(nothing to undo)", text)
        self.assertIn("(unknown key)", text)
        self.assertEqual(len(load_labels(self.conn)), 1)

    def test_limit_and_show_score(self) -> None:
        keys = Keys("y", "y", "y", "y")
        text = self.run_session(keys, show_score=True, limit=2)
        self.assertEqual(keys.prompts, 2)
        self.assertIn("[score 40 · reason 40]", text)


class MetricsTest(unittest.TestCase):
    def test_auc(self) -> None:
        self.assertEqual(auc([3, 2, 1, 0], [1, 1, -1, -1]), 1.0)
        self.assertEqual(auc([0, 1, 2, 3], [1, 1, -1, -1]), 0.0)
        self.assertEqual(auc([5, 5, 5, 5], [1, -1, 1, -1]), 0.5)
        self.assertAlmostEqual(auc([3, 1, 2, 0], [1, 1, -1, -1]), 0.75)
        self.assertIsNone(auc([1, 2], [1, 1]))

    def test_precision_at(self) -> None:
        self.assertEqual(precision_at([9, 8, 7, 1], [1, -1, 1, 1], 2), (1, 2))
        self.assertEqual(precision_at([9, 8], [1, -1], 5), (1, 2))  # fewer jobs than k

    def test_thresholds_and_suggestion(self) -> None:
        scores, labels = [90, 70, 50, 30, 60, 20, 10, 40], [1, 1, 1, 1, -1, -1, -1, -1]
        self.assertEqual(threshold_rows(scores, labels, [40]), [(40, 0.75, 0.5)])
        self.assertEqual(suggest_min_score(scores, labels, 0.95), 30)  # keeps all 4 👍

    def test_bootstrap_is_deterministic_and_brackets_auc(self) -> None:
        scores =[i + (5 if i % 2 else 0) for i in range(30)]
        labels = [1 if i % 2 else -1 for i in range(30)]
        lo, hi = bootstrap_auc(scores, labels, random.Random(7), 300)
        self.assertLessEqual(lo, auc(scores, labels))
        self.assertGreaterEqual(hi, auc(scores, labels))
        self.assertEqual((lo, hi), bootstrap_auc(scores, labels, random.Random(7), 300))
        same = bootstrap_diff(scores, scores, labels, random.Random(7), 100)
        self.assertEqual(same, (0.0, 0.0))

    def test_bm25_prefers_matching_documents(self) -> None:
        s = bm25(["rust axum backend api", "logo design for a bakery", "rust cli tool"], "rust backend", 1.5, 0.75)
        self.assertEqual(max(range(3), key=s.__getitem__), 0)
        self.assertEqual(s[1], 0.0)


class ReportTest(unittest.TestCase):
    def variants(self, labels):
        good = [80.0 if label > 0 else 20.0 for label in labels]
        return [Variant("current config", good, ["r"] * len(labels)),
                Variant("worse", [50.0] * len(labels), ["r"] * len(labels))]

    def test_too_few_labels_warns_and_recommends_nothing(self) -> None:
        labels = [1] * 4 + [-1] * 12
        out = io.StringIO()
        report(jobs(len(labels)), labels, self.variants(labels), 60, False, out)
        text = out.getvalue()
        self.assertTrue(text.startswith("LABELS: 4 👍 good / 12 👎 bad   (16 labeled of 60 stored jobs)"))
        self.assertIn(f"UNRELIABLE: fewer than {MIN_PER_CLASS}", text)
        self.assertNotIn("RECOMMENDATION", text)
        self.assertNotIn("SUGGESTED min_score", text)

    def test_one_class_only(self) -> None:
        out = io.StringIO()
        report(jobs(3), [1, 1, 1], [], 3, False, out)
        self.assertIn("Need at least one 👍 and one 👎", out.getvalue())

    def test_reliable_report_keeps_current_and_suggests_min_score(self) -> None:
        labels = [1] * 12 + [-1] * 12
        out = io.StringIO()
        report(jobs(len(labels)), labels, self.variants(labels), 70, True, out)
        text = out.getvalue()
        self.assertIn("RECOMMENDATION: keep the current config", text)
        self.assertIn("SUGGESTED min_score = 80: keeps 100% of 👍, filters 100% of 👎.", text)
        self.assertIn("👍 jobs it scored lowest", text)

    def test_clearly_better_variant_is_recommended(self) -> None:
        labels = [1] * 12 + [-1] * 12
        noisy = [60.0 if i % 3 else 20.0 for i in range(24)]  # mediocre current config
        perfect = [80.0 if label > 0 else 20.0 for label in labels]
        out = io.StringIO()
        report(jobs(24), labels, [Variant("current config", noisy, ["r"] * 24),
                                       Variant("better", perfect, ["r"] * 24)], 24, False, out)
        self.assertIn("RECOMMENDATION: 'better'", out.getvalue())



class BuildVariantsTest(unittest.TestCase):
    def test_variants_and_zero_match_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gigradar.toml"
            path.write_text('[[searches]]\nname = "a"\n', encoding="utf-8")
            current = load_config(path, {}).scoring
        current = replace(current, cos_low=0.0, cos_high=1.0)
        no_match = make_job(skills="Haskell, COBOL", url="https://www.upwork.com/jobs/~01")
        matched = make_job(skills="Rust", url="https://www.upwork.com/jobs/~02")
        cache = MemoryEmbeddingCache()
        variants = build_variants([no_match, matched], PROFILE2, current, FakeEmbedder("fake"), cache)
        names = [v.name for v in variants]
        self.assertEqual(names[0], "current config")
        self.assertIn("zero skill match = unknown", names)
        self.assertEqual(names[-2:], ["rules only (skill overlap)", "BM25 keywords (baseline)"])
        by_name = {v.name: v for v in variants}
        self.assertGreater(by_name["zero skill match = unknown"].scores[0], by_name["current config"].scores[0])
        self.assertEqual(by_name["zero skill match = unknown"].scores[1], by_name["current config"].scores[1])
        self.assertIn(("~01", "fake#w200"), cache.vectors)  # truncated texts cached separately


class ScoringKeysTest(unittest.TestCase):
    def load(self, toml: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gigradar.toml"
            path.write_text(toml + '\n[[searches]]\nname = "a"\n', encoding="utf-8")
            return load_config(path, {}).scoring

    def test_defaults_and_values(self) -> None:
        s = self.load("")
        self.assertEqual((s.zero_skill_match, s.max_words, s.min_score), ("zero", None, 0))
        s = self.load('[scoring]\nzero_skill_match = "unknown"\nmax_words = 200\nmin_score = 40\n')
        self.assertEqual((s.zero_skill_match, s.max_words, s.min_score), ("unknown", 200, 40))

    def test_errors(self) -> None:
        cases = {'[scoring]\nzero_skill_match = "none"\n': "zero_skill_match must be one of",
                 "[scoring]\nmax_words = 5\n": "max_words must be >= 20",
                 "[scoring]\nmin_score = 101\n": "min_score must be 0..100",
                 "[scoring]\nmin_score = 40.5\n": "must be int"}
        for toml, fragment in cases.items():
            with self.subTest(fragment=fragment), self.assertRaises(ConfigError) as ctx:
                self.load(toml)
            self.assertIn(fragment, str(ctx.exception))


class ClaudeRowTest(unittest.TestCase):
    JOBS = jobs(3)

    def test_row_needs_every_labeled_job_scored(self) -> None:
        stored = {"~00": Score(90, "a", "claude", "1"), "~01": Score(10, "b", "claude", "1")}
        variant, status = claude_variant(self.JOBS, stored)
        self.assertIsNone(variant)
        self.assertIn("2 of 3", status)
        variant, status = claude_variant(self.JOBS, {})
        self.assertIsNone(variant)
        self.assertIn("no stored scores", status)
        variant, status = claude_variant(self.JOBS, {**stored, "~02": Score(50, "c", "claude", "1")})
        self.assertEqual((variant.name, variant.scores, variant.reasons),
                         ("claude (stored scores)", [90, 10, 50], ["a", "b", "c"]))


class CombinedVariantTest(unittest.TestCase):
    def test_mean_per_job(self) -> None:
        combined = combined_variant(Variant("a", [10.0, 80.0], ["x", "y"]), Variant("b", [30.0, 90.0], ["x", "y"]))
        self.assertEqual((combined.name, combined.scores), ("combined: mean(embedding, claude)", [20.0, 85.0]))
        with self.assertRaises(ValueError):
            combined_variant(Variant("a", [1.0], ["x"]), Variant("b", [1.0, 2.0], ["x", "y"]))


class RankHelperTest(unittest.TestCase):
    def test_average_ranks_share_ties(self) -> None:
        self.assertEqual(average_ranks([30, 10, 20, 20]), [4.0, 1.0, 2.5, 2.5])
        self.assertEqual(average_ranks([]), [])

    def test_auc_is_unchanged_by_the_extraction(self) -> None:
        self.assertEqual(auc([1, 2, 3, 4], [-1, -1, 1, 1]), 1.0)
        self.assertEqual(auc([1, 1, 1, 1], [-1, -1, 1, 1]), 0.5)   # all tied: coin toss
        self.assertEqual(auc([4, 3, 2, 1], [-1, -1, 1, 1]), 0.0)


class ReadOnlyCacheTest(unittest.TestCase):
    def test_new_vectors_stay_in_memory(self) -> None:
        inner = MemoryEmbeddingCache()
        inner.put({"~a": [1.0]}, "m")
        cache = ReadOnlyEmbeddingCache(inner)
        cache.put({"~b": [2.0]}, "m")
        self.assertEqual(cache.get(["~a", "~b", "~c"], "m"), {"~a": [1.0], "~b": [2.0]})
        self.assertEqual(inner.get(["~b"], "m"), {})   # nothing reached the real cache


if __name__ == "__main__":
    unittest.main()
