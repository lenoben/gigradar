"""Run: .venv/Scripts/python -m unittest discover -s tests   (stdlib only, no network, fake embedder)"""

import hashlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from gigradar.config import ConfigError, load_config
from gigradar.embed import EmbedderError, FastEmbedder, cosine, normalize
from gigradar.profile import ProfileSection
from gigradar.score import (EmbeddingScorer, MemoryEmbeddingCache, RuleScorer, dry_run, job_text,
                            load_stored_jobs, scale, section_key)
from gigradar.store import SqliteEmbeddingCache, StoreError, decode_vector, encode_vector, mark_seen, open_store
from test_score import PROFILE, make_job

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parent.parent
HAS_FASTEMBED = importlib.util.find_spec("fastembed") is not None
DIM = 64


class FakeEmbedder:
    """Hashed bag of words: texts sharing words get a high cosine. Counts embedded texts."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            vec = [0.0] * DIM
            for word in text.lower().replace(",", " ").split():
                vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
            vectors.append(normalize(vec))
        return vectors

    def embedded(self) -> int:
        return sum(len(c) for c in self.calls)


PROFILE2 = replace(PROFILE, sections=[
    ProfileSection("Rust backend", "rust axum tokio api services backend"),
    ProfileSection("Frontend", "nextjs react typescript tailwind dashboard frontend"),
])


def scorer(embedder, cache=None, semantic=0.7, skills=0.3, low=0.0, high=1.0) -> EmbeddingScorer:
    return EmbeddingScorer(embedder, RuleScorer(3), cache or MemoryEmbeddingCache(), semantic, skills, low, high)


class MathTest(unittest.TestCase):
    def test_normalize_and_cosine(self) -> None:
        self.assertEqual(normalize([3.0, 4.0]), [0.6, 0.8])
        self.assertEqual(normalize([0.0, 0.0]), [0.0, 0.0])
        self.assertAlmostEqual(cosine(normalize([1.0, 1.0]), normalize([1.0, 1.0])), 1.0)
        with self.assertRaises(ValueError):
            cosine([1.0], [1.0, 2.0])

    def test_scale_clamps(self) -> None:
        for value, expected in zip((0.2, 0.4, 0.6, 0.8, 0.95), (0.0, 0.0, 0.5, 1.0, 1.0)):
            self.assertAlmostEqual(scale(value, 0.4, 0.8), expected)

    def test_job_text_order(self) -> None:
        self.assertEqual(job_text(make_job(title="T", skills="Rust", description="D")), "T\nSkills: Rust\nD")


class EmbeddingScorerTest(unittest.TestCase):
    def test_best_section_wins_and_names_the_reason(self) -> None:
        rust = make_job(title="Rust axum backend", description="tokio api services", skills="Rust, Docker")
        web = make_job(url="https://www.upwork.com/jobs/~02", title="Nextjs dashboard", skills="React",
                       description="react typescript tailwind frontend")
        [r1, r2] = scorer(FakeEmbedder("fake")).evaluate([rust, web], PROFILE2)
        self.assertEqual((r1.section, r2.section), ("Rust backend", "Frontend"))
        self.assertEqual(r1.score.reason, "Rust backend · matched: Rust")
        self.assertEqual(r2.score.reason, "Frontend")  # no matched skills: heading only
        self.assertEqual((r1.score.scorer, r1.score.version), ("embed", "1"))

    def test_weighted_mean(self) -> None:
        job = make_job(skills="Rust, PostgreSQL, Docker")  # overlap 2/3
        s = scorer(FakeEmbedder("fake"))
        [r] = s.evaluate([job], PROFILE2)
        semantic = scale(r.cosine, 0.0, 1.0)
        self.assertEqual(r.score.value, round(100 * (0.7 * semantic + 0.3 * 2 / 3)))

    def test_no_skills_listed_uses_semantic_only(self) -> None:
        [r] = scorer(FakeEmbedder("fake")).evaluate([make_job(skills="")], PROFILE2)
        self.assertEqual(r.score.value, round(100 * scale(r.cosine, 0.0, 1.0)))

    def test_rejected_is_zero_even_with_a_strong_match(self) -> None:
        job = make_job(title="Rust axum backend", description="rust axum tokio api services backend WordPress")
        [r] = scorer(FakeEmbedder("fake")).evaluate([job], PROFILE2)
        self.assertGreater(r.cosine, 0.5)
        self.assertEqual((r.score.value, r.score.reason), (0, '✗ excluded keyword "WordPress"'))

    def test_cosine_range_maps_to_0_and_100(self) -> None:
        job = make_job(skills="")
        [low] = scorer(FakeEmbedder("fake"), low=0.98, high=0.99).evaluate([job], PROFILE2)
        [high] = scorer(FakeEmbedder("fake"), low=0.0, high=0.01).evaluate([job], PROFILE2)
        self.assertEqual((low.score.value, high.score.value), (0, 100))

    def test_cache_avoids_re_embedding_and_is_per_model(self) -> None:
        cache = MemoryEmbeddingCache()
        jobs = [make_job(), make_job(url="https://www.upwork.com/jobs/~02"), make_job(url="")]
        fake = FakeEmbedder("model-a")
        first = scorer(fake, cache).score(jobs, PROFILE2)
        self.assertEqual(fake.embedded(), 2 + 3)  # 2 sections + 3 jobs
        second = scorer(fake, cache).score(jobs, PROFILE2)
        self.assertEqual(fake.embedded(), 5 + 1)  # sections cached too: only the id-less job
        self.assertEqual(first, second)
        other = FakeEmbedder("model-b")
        scorer(other, cache).score(jobs, PROFILE2)
        self.assertEqual(other.embedded(), 2 + 3)  # different model: nothing cached for it


class StoreCacheTest(unittest.TestCase):
    def test_vector_blob_roundtrip(self) -> None:
        vec = normalize([0.1, -2.5, 3.25, 0.0])
        blob = encode_vector(vec)
        self.assertEqual(len(blob), 16)  # float32
        for got, want in zip(decode_vector(blob, 4), vec, strict=True):
            self.assertAlmostEqual(got, want, places=6)  # float32 precision
        with self.assertRaises(StoreError):
            decode_vector(blob, 5)

    def test_sqlite_cache_with_scorer(self) -> None:
        conn = open_store(Path(":memory:"), [], NOW)
        try:
            cache = SqliteEmbeddingCache(conn, NOW)
            fake = FakeEmbedder("model-a")
            jobs = [make_job(), make_job(url="https://www.upwork.com/jobs/~02")]
            first = scorer(fake, cache).score(jobs, PROFILE2)
            rows = conn.execute("SELECT job_id, model, dim FROM embeddings ORDER BY 1").fetchall()
            keys = [section_key(f"{s.heading}\n{s.text}") for s in PROFILE2.sections]
            self.assertEqual(rows, sorted([(k, "model-a", DIM) for k in keys]
                                          + [("~01", "model-a", DIM), ("~02", "model-a", DIM)]))
            again = FakeEmbedder("model-a")
            self.assertEqual(scorer(again, cache).score(jobs, PROFILE2), first)
            self.assertEqual(again.embedded(), 0)  # jobs and sections all cached
        finally:
            conn.close()


class ScoringConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def load(self, toml: str, env: dict):
        path = self.dir / "gigradar.toml"
        path.write_text(toml + '\n[[searches]]\nname = "a"\n', encoding="utf-8")
        return load_config(path, env).scoring

    def test_defaults(self) -> None:
        s = self.load("", {"LOCALAPPDATA": str(self.dir / "lad")})
        self.assertEqual((s.model, s.model_dir), ("BAAI/bge-small-en-v1.5", self.dir / "lad" / "gigradar" / "models"))
        self.assertEqual((s.weight_semantic, s.weight_skills, s.cos_low, s.cos_high, s.skill_saturation),
                         (0.7, 0.3, 0.45, 0.8, 3))
        self.assertEqual(self.load("", {}).model_dir, Path.home() / ".local" / "share" / "gigradar" / "models")

    def test_full(self) -> None:
        s = self.load('[scoring]\nmodel = "BAAI/bge-base-en-v1.5"\nmodel_dir = "models"\n'
                      'weights = { semantic = 1, skills = 0 }\ncos_low = 0.5\ncos_high = 0.9\n'
                      'skill_saturation = 4\n', {})
        self.assertEqual((s.model, s.model_dir), ("BAAI/bge-base-en-v1.5", self.dir / "models"))
        self.assertEqual((s.weight_semantic, s.weight_skills, s.cos_low, s.cos_high, s.skill_saturation),
                         (1.0, 0.0, 0.5, 0.9, 4))

    def test_errors(self) -> None:
        cases = {
            "[scoring]\nmodle = 'x'\n": "unknown keys ['modle']",
            "[scoring]\nweights = { semantic = 0.7, skill = 0.3 }\n": "scoring.weights: unknown keys",
            "[scoring]\nweights = { semantic = 0, skills = 0 }\n": "must be > 0",
            "[scoring]\nweights = { semantic = -1 }\n": "non-negative number",
            "[scoring]\ncos_low = 0.9\ncos_high = 0.5\n": "cos_low < cos_high",
            "[scoring]\ncos_high = 1.5\n": "cos_high <= 1",
            "[scoring]\nskill_saturation = 0\n": "skill_saturation must be >= 1",
            "[scoring]\nskill_saturation = 2.5\n": "must be int",
            "scoring = 3\n": "[scoring] must be a table",
        }
        for toml, fragment in cases.items():
            with self.subTest(fragment=fragment), self.assertRaises(ConfigError) as ctx:
                self.load(toml, {})
            self.assertIn(fragment, str(ctx.exception))

    def test_example_toml_scoring_block_is_valid(self) -> None:
        """The commented [scoring] block in gigradar.example.toml, uncommented, must load."""
        lines = (REPO / "gigradar.example.toml").read_text(encoding="utf-8").splitlines()
        start = lines.index("# [scoring]")
        block = []
        for line in lines[start:]:
            if not line.startswith("# "):
                break
            block.append(line[2:])
        s = self.load("\n".join(block), {})
        self.assertEqual((s.model, s.cos_low, s.cos_high), ("BAAI/bge-small-en-v1.5", 0.45, 0.8))


class DryRunTest(unittest.TestCase):
    def test_reads_store_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "gigradar.db"
            conn = open_store(db, [], NOW)
            jobs = [make_job(title="Rust axum backend"),
                    make_job(url="https://www.upwork.com/jobs/~02", tier="EntryLevel"),
                    make_job(url="https://www.upwork.com/jobs/~03", title="Nextjs dashboard", skills="")]
            mark_seen(conn, jobs, NOW)
            conn.close()
            before = hashlib.sha256(db.read_bytes()).hexdigest()

            stored = load_stored_jobs(db)
            self.assertEqual(stored, jobs)
            out = io.StringIO()
            dry_run(stored, PROFILE2, scorer(FakeEmbedder("fake")), 5, out)
            text = out.getvalue()
            self.assertIn("3 jobs scored", text)
            self.assertIn("best-section cosine: min", text)
            self.assertIn("rejected by hard rules: 1 of 3", text)
            self.assertIn("✗ tier entry not wanted", text)
            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)
            self.assertEqual(list(Path(tmp).iterdir()), [db])  # no -journal/-wal files, no backup


class LazyImportTest(unittest.TestCase):
    def test_importing_does_not_load_fastembed(self) -> None:
        code = ("import sys, gigradar.score, gigradar.embed, gigradar.store, gigradar.watch;"
                "print('fastembed' in sys.modules)")
        result = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "False")

    def test_missing_fastembed_is_a_clear_error(self) -> None:
        saved = sys.modules.get("fastembed")
        sys.modules["fastembed"] = None  # makes `import fastembed` fail
        env = os.environ.get("HF_HUB_OFFLINE")
        try:
            with self.assertRaises(EmbedderError) as ctx:
                FastEmbedder("BAAI/bge-small-en-v1.5", Path("nowhere"), offline=False)
            self.assertIn("requirements-scoring.txt", str(ctx.exception))
        finally:
            if saved is None:
                del sys.modules["fastembed"]
            else:
                sys.modules["fastembed"] = saved
            _restore_env("HF_HUB_OFFLINE", env)


@unittest.skipUnless(HAS_FASTEMBED, "fastembed not installed (requirements-scoring.txt)")
class FastEmbedOfflineTest(unittest.TestCase):
    def test_offline_without_model_files_says_download(self) -> None:
        """Real fastembed, empty cache dir, offline: must fail fast without network."""
        env = os.environ.get("HF_HUB_OFFLINE")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(EmbedderError) as ctx:
                    FastEmbedder("BAAI/bge-small-en-v1.5", Path(tmp), offline=True)
            self.assertIn("gigradar.embed --download", str(ctx.exception))
        finally:
            _restore_env("HF_HUB_OFFLINE", env)

    def test_unknown_model(self) -> None:
        env = os.environ.get("HF_HUB_OFFLINE")
        try:
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(EmbedderError):
                FastEmbedder("nobody/no-such-model", Path(tmp), offline=True)
        finally:
            _restore_env("HF_HUB_OFFLINE", env)


def _restore_env(key: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
