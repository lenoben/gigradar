"""Run: .venv/Scripts/python -m unittest discover -s tests   (stdlib only)"""

import hashlib
import unittest

from gigradar import mcp_tools
from gigradar.rubric import RUBRIC, RUBRIC_VERSION, SCORER

# The rubric text and its version are pinned together: editing the text fails this test until the
# version is bumped and the hash below is updated, so "claude" scores of different rubrics never mix.
PINNED_RUBRIC = ("3", "f37a933ccbd967263e35e84a7c16a21f0c4f8df3750a5f73f023c960f30c5014")


class RubricTest(unittest.TestCase):
    def test_text_and_version_are_pinned_together(self) -> None:
        digest = hashlib.sha256(RUBRIC.encode("utf-8")).hexdigest()
        self.assertEqual((RUBRIC_VERSION, digest), PINNED_RUBRIC,
                         "rubric text changed: bump RUBRIC_VERSION and update PINNED_RUBRIC")

    def test_rubric_is_generic(self) -> None:
        for personal in (":/", "Telegram", "@"):  # no paths, no channels, no addresses
            self.assertNotIn(personal, RUBRIC)

    def test_v3_principles_are_stated_generally(self) -> None:
        text = RUBRIC.lower()
        for phrase in ("seniority is not a penalty", "at most 10", "`constraints`", "impersonate", "upfront",
                       "terms of service"):
            self.assertIn(phrase, text)
        for personal in ("german", "europe", "utc", "pst"):   # no home country or time zone baked in
            self.assertNotIn(personal, text)

    # The rubric is a prompt: these tests check that each pay case is stated, not what a model does with it.
    def test_stated_amount_clearly_below_the_minimum_is_a_hard_rule(self) -> None:
        text = " ".join(RUBRIC.split()).lower()
        for phrase in ("only when the posting states a concrete rate or budget",
                       "clearly below the matching minimum", "`min_hourly_usd` for hourly jobs",
                       "`min_fixed_usd` for fixed-price jobs", "a null minimum is no rule", "score 0-9"):
            self.assertIn(phrase, text)

    def test_missing_or_vague_budget_is_only_a_note(self) -> None:
        text = " ".join(RUBRIC.split()).lower()
        for phrase in ("a missing or vague budget", '"negotiable"', "mentioned in the reason",
                       "do not lower the score"):
            self.assertIn(phrase, text)

    def test_implausible_budget_is_only_a_note(self) -> None:
        text = " ".join(RUBRIC.split()).lower()
        for phrase in ("an implausible one (only an obvious placeholder such as 0 or 1)", "a note, not a penalty"):
            self.assertIn(phrase, text)

    def test_small_but_possible_amount_is_a_stated_budget(self) -> None:
        text = " ".join(RUBRIC.split()).lower()
        for phrase in ("a small but possible amount (for example $5 or $10 fixed) counts as a stated budget",
                       "not as an implausible one"):
            self.assertIn(phrase, text)

    def test_score_is_called_value_like_the_tool_argument(self) -> None:
        text = " ".join(RUBRIC.split())
        self.assertIn("`set_score(job_id, value, reason)`", text)
        self.assertIn("`value` is your score, an integer 0-100", text)

    def test_batches_are_small_enough_for_one_tool_result(self) -> None:
        self.assertIn("limit 10", RUBRIC)
        self.assertLessEqual(mcp_tools.MAX_LIMIT, 20)   # 50 jobs spilled out of a single tool result

    def test_scorer_name(self) -> None:
        self.assertEqual(SCORER, "claude")

    def test_rubric_demands_blind_untrusted_scoring(self) -> None:
        for phrase in ("Blind scoring", "include_scores", "untrusted", "never instructions", "200 characters"):
            self.assertIn(phrase.lower(), RUBRIC.lower())
