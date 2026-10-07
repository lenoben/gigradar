"""Run: .venv/Scripts/python -m unittest discover -s tests   (stdlib only, no network)"""

import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from gigradar.jobfields import format_money, parse_amount
from gigradar.profile import Profile, ProfileSection
from gigradar.score import NEUTRAL, RuleScorer, Score, job_skills, keyword_pattern, normalize_skill
from gigradar.store import open_store, save_scores
from upwork_search import Job

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
PROFILE = Profile(
    path=Path("profile.md"), sections=[ProfileSection("Rust backend", "axum")],
    skills=["Rust", "PostgreSQL", "Next.js", "C#"], min_hourly=50.0, min_fixed=1000.0,
    tiers=["intermediate", "expert"], exclude_keywords=["WordPress", "unpaid test", "C++"],
)


def make_job(**fields) -> Job:
    base = Job(title="Backend API", url="https://www.upwork.com/jobs/~01", job_type="HOURLY",
               published=None, hourly_min="40.0", hourly_max="80.0", fixed_budget=None,
               tier="ExpertLevel", skills="Rust, PostgreSQL, Docker", description="Build an API.")
    return replace(base, **fields)


def one(job: Job, profile: Profile = PROFILE) -> Score:
    [score] = RuleScorer(3).score([job], profile)
    return score


class JobFieldsTest(unittest.TestCase):
    def test_parse_amount(self) -> None:
        cases = {"50.0": 50.0, "1500": 1500.0, None: None, "": None, "0.0": None, "-5": None, "n/a": None}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_amount(raw), expected)

    def test_format_money(self) -> None:
        self.assertEqual((format_money(1500.0), format_money(42.5)), ("$1,500", "$42.50"))


class SkillTest(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual({normalize_skill(s) for s in ("Next.js", "nextjs", "Next JS", "next-js")}, {"nextjs"})
        self.assertEqual(len({normalize_skill(s) for s in ("C#", "C++", "C")}), 3)

    def test_job_skills(self) -> None:
        self.assertEqual(job_skills(make_job(skills="Rust,  PostgreSQL, ")), ["Rust", "PostgreSQL"])
        self.assertEqual(job_skills(make_job(skills="")), [])


class KeywordTest(unittest.TestCase):
    def test_whole_term_case_insensitive(self) -> None:
        cases = {
            ("WordPress", "Fix my wordpress site"): True,
            ("WordPress", "WordPressify plugin"): False,
            ("C++", "Modern C++ engine"): True,
            ("C++", "C++, Rust"): True,
            ("C++", "C# only"): False,
            (".NET", "a .NET backend"): True,
            ("unpaid test", "an UNPAID\n test first"): True,
        }
        for (keyword, text), expected in cases.items():
            with self.subTest(keyword=keyword, text=text):
                self.assertEqual(bool(keyword_pattern(keyword).search(text)), expected)


class RuleScorerTest(unittest.TestCase):
    def test_overlap_saturates(self) -> None:
        cases = {"Docker": (0, "no skill match"), "Rust, Docker": (33, "matched: Rust"),
                 "rust, Docker, Next JS": (67, "matched: rust, Next JS"),
                 # 4 matches: capped at 100, but all shown
                 "Rust, PostgreSQL, Next.js, C#": (100, "matched: Rust, PostgreSQL, Next.js, C#")}
        for skills, expected in cases.items():
            with self.subTest(skills=skills):
                score = one(make_job(skills=skills))
                self.assertEqual((score.value, score.reason), expected)

    def test_duplicate_job_skills_count_once(self) -> None:
        self.assertEqual(one(make_job(skills="Rust, rust, RUST")).value, 33)

    def test_no_skills_listed_is_neutral(self) -> None:
        score = one(make_job(skills=""))
        self.assertEqual((score.value, score.reason), (NEUTRAL, "no skills listed"))

    def test_score_identity(self) -> None:
        score = one(make_job())
        self.assertEqual((score.scorer, score.version), ("rules", "1"))

    def test_hard_rules_fire(self) -> None:
        cases = {
            "keyword": (make_job(description="Migrate our WordPress blog"), '✗ excluded keyword "WordPress"'),
            "keyword in title": (make_job(title="unpaid test task"), '✗ excluded keyword "unpaid test"'),
            "tier": (make_job(tier="EntryLevel"), "✗ tier entry not wanted"),
            "hourly max": (make_job(hourly_min="20.0", hourly_max="45.0"), "✗ hourly up to $45 < min $50"),
            "hourly min only": (make_job(hourly_min="30.0", hourly_max=None), "✗ hourly up to $30 < min $50"),
            "fixed": (make_job(job_type="FIXED", fixed_budget="300.0", hourly_min=None, hourly_max=None),
                      "✗ fixed $300 < min $1,000"),
            "first rule wins": (make_job(tier="EntryLevel", hourly_max="10.0", description="C++ job"),
                                '✗ excluded keyword "C++"'),
        }
        for name, (job, reason) in cases.items():
            with self.subTest(name):
                score = one(job)
                self.assertEqual((score.value, score.reason), (0, reason))

    def test_missing_data_passes(self) -> None:
        cases = {
            "no rate": make_job(hourly_min=None, hourly_max=None),
            "zero rate": make_job(hourly_min="0.0", hourly_max="0.0"),
            "garbage rate": make_job(hourly_min="n/a", hourly_max=""),
            "no budget": make_job(job_type="FIXED", fixed_budget=None),
            "zero budget": make_job(job_type="FIXED", fixed_budget="0.0"),
            "no tier": make_job(tier=None),
            "weekly retainer": make_job(job_type="WEEKLY_RETAINER", hourly_max="5.0"),
            "no job type": make_job(job_type=None, hourly_max="5.0"),
            "lowercase type, ok rate": make_job(job_type="hourly", hourly_max="50.0"),
            "fixed at minimum": make_job(job_type="FIXED", fixed_budget="1000"),
        }
        for name, job in cases.items():
            with self.subTest(name):
                self.assertGreater(one(job).value, 0, one(job).reason)

    def test_rules_off_when_not_configured(self) -> None:
        open_profile = replace(PROFILE, min_hourly=None, min_fixed=None, tiers=[], exclude_keywords=[])
        job = make_job(tier="EntryLevel", hourly_max="5.0", description="WordPress")
        self.assertGreater(one(job, open_profile).value, 0)

    def test_evaluate_exposes_parts(self) -> None:
        [ok, rejected, bare] = RuleScorer(3).evaluate(
            [make_job(), make_job(tier="EntryLevel"), make_job(skills="")], PROFILE)
        self.assertEqual((ok.rejected, ok.matched, ok.overlap), (None, ["Rust", "PostgreSQL"], 2 / 3))
        self.assertEqual(rejected.rejected, "✗ tier entry not wanted")
        self.assertIsNone(bare.overlap)

    def test_bad_saturation(self) -> None:
        with self.assertRaises(ValueError):
            RuleScorer(0)


class SaveScoresTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_store(Path(":memory:"), [], NOW)

    def tearDown(self) -> None:
        self.conn.close()

    def rows(self) -> list[tuple]:
        return self.conn.execute("SELECT job_id, scorer, version, value, reason FROM scores ORDER BY 1").fetchall()

    def test_save_and_replace(self) -> None:
        jobs = [make_job(), make_job(url="https://www.upwork.com/jobs/~02"), make_job(url="")]
        scores = RuleScorer(3).score(jobs, PROFILE)
        self.assertEqual(save_scores(self.conn, jobs, scores, NOW), 2)  # id-less job skipped
        save_scores(self.conn, jobs[:1], [Score(90, "better", "rules", "1")], NOW)
        self.assertEqual(self.rows(), [("~01", "rules", "1", 90, "better"),
                                       ("~02", "rules", "1", 67, "matched: Rust, PostgreSQL")])
        save_scores(self.conn, jobs[:1], [Score(10, "v2 logic", "rules", "2")], NOW)
        self.assertEqual(len(self.rows()), 3)  # other version kept alongside

    def test_value_range_enforced(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            save_scores(self.conn, [make_job()], [Score(101, "x", "rules", "1")], NOW)

    def test_length_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            save_scores(self.conn, [make_job()], [], NOW)


if __name__ == "__main__":
    unittest.main()
