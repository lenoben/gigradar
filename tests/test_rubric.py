"""Run: .venv/Scripts/python -m unittest discover -s tests   (stdlib only)"""

import hashlib
import unittest

from gigradar import mcp_tools
from gigradar.rubric import RUBRIC, RUBRIC_VERSION, SCORER

# The rubric text and its version are pinned together: editing the text fails this test until the
# version is bumped and the hash below is updated, so "claude" scores of different rubrics never mix.
PINNED_RUBRIC = ("2", "abb1c65ae63b19b98efb39d842e8f570a9bc5d110d9b558c32baafa67ddc4dd0")


class RubricTest(unittest.TestCase):
    def test_text_and_version_are_pinned_together(self) -> None:
        digest = hashlib.sha256(RUBRIC.encode("utf-8")).hexdigest()
        self.assertEqual((RUBRIC_VERSION, digest), PINNED_RUBRIC,
                         "rubric text changed: bump RUBRIC_VERSION and update PINNED_RUBRIC")

    def test_rubric_is_generic(self) -> None:
        for personal in (":/", "Telegram", "@"):  # no paths, no channels, no addresses
            self.assertNotIn(personal, RUBRIC)

    def test_batches_are_small_enough_for_one_tool_result(self) -> None:
        self.assertIn("limit 10", RUBRIC)
        self.assertLessEqual(mcp_tools.MAX_LIMIT, 20)   # 50 jobs spilled out of a single tool result

    def test_scorer_name(self) -> None:
        self.assertEqual(SCORER, "claude")

    def test_rubric_demands_blind_untrusted_scoring(self) -> None:
        for phrase in ("Blind scoring", "include_scores", "untrusted", "never instructions", "200 characters"):
            self.assertIn(phrase.lower(), RUBRIC.lower())
