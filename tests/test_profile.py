"""Run: .venv/Scripts/python -m unittest discover -s tests   (stdlib only, no network)"""

import tempfile
import unittest
from pathlib import Path

from gigradar.config import ConfigError, load_config
from gigradar.profile import ProfileError, ProfileSection, parse_sections, read_sections

REPO = Path(__file__).resolve().parent.parent

SEARCH = '[[searches]]\nname = "py"\nquery = "python"\n'
PROFILE_MD = "## Rust backend\nAPIs in axum.\n\n## Frontend\nNext.js apps.\n"


class ParseSectionsTest(unittest.TestCase):
    def test_sections(self) -> None:
        md = ("# Me\nintro is ignored\n\n## Rust backend\n<!-- hint -->\naxum services\n"
              "### sub\nstays in Rust\n\n##  Data \n<!-- multi\nline -->Postgres\n")
        self.assertEqual(parse_sections(md), [
            ProfileSection("Rust backend", "axum services\n### sub\nstays in Rust"),
            ProfileSection("Data", "Postgres"),
        ])

    def test_crlf(self) -> None:
        self.assertEqual(parse_sections("## A\r\ntext\r\n"), [ProfileSection("A", "text")])

    def test_errors(self) -> None:
        cases = {
            "just text\n# Title\n": "no '## ' sections",
            "": "no '## ' sections",
            "## A\n<!-- only a comment -->\n\n## B\nx\n": "section 'A' is empty",
            "## A\nx\n## A\ny\n": "duplicate section 'A'",
            "## \nx\n": "needs a heading",
        }
        for md, fragment in cases.items():
            with self.subTest(fragment=fragment), self.assertRaises(ProfileError) as ctx:
                parse_sections(md)
            self.assertIn(fragment, str(ctx.exception))

    def test_example_template_parses(self) -> None:
        headings = [s.heading for s in read_sections(REPO / "profile.example.md")]
        self.assertEqual(headings, ["Rust / Go backend", "Next.js / SvelteKit frontend", "PostgreSQL / data"])


class ProfileConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "profile.md").write_text(PROFILE_MD, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def load(self, profile_toml: str):
        path = self.dir / "gigradar.toml"
        path.write_text(profile_toml + "\n" + SEARCH, encoding="utf-8")
        return load_config(path, {})

    def test_no_profile_table_means_no_scoring(self) -> None:
        self.assertIsNone(self.load("").profile)

    def test_defaults(self) -> None:
        profile = self.load("[profile]\n").profile
        self.assertEqual(profile.path, self.dir / "profile.md")
        self.assertEqual([s.heading for s in profile.sections], ["Rust backend", "Frontend"])
        self.assertEqual((profile.skills, profile.tiers, profile.exclude_keywords), ([], [], []))
        self.assertIsNone(profile.min_hourly)
        self.assertIsNone(profile.min_fixed)
        self.assertIsNone(profile.constraints)

    def test_full(self) -> None:
        (self.dir / "me").mkdir()
        (self.dir / "me" / "p.md").write_text("## Only\nx\n", encoding="utf-8")
        profile = self.load('[profile]\npath = "me/p.md"\nskills = [" Rust ", "Next.js"]\n'
                            'min_hourly = 50\nmin_fixed = 999.5\ntiers = ["expert"]\n'
                            'exclude_keywords = ["WordPress"]\n').profile
        self.assertEqual(profile.path, self.dir / "me" / "p.md")
        self.assertEqual(profile.skills, ["Rust", "Next.js"])
        self.assertEqual((profile.min_hourly, profile.min_fixed), (50.0, 999.5))
        self.assertEqual((profile.tiers, profile.exclude_keywords), (["expert"], ["WordPress"]))

    def test_constraints(self) -> None:
        self.assertEqual(self.load('[profile]\nconstraints = "  Remote only  "\n').profile.constraints, "  Remote only  ")
        self.assertIsNone(self.load('[profile]\nconstraints = ""\n').profile.constraints)   # empty = none given

    def test_example_toml_block_is_valid(self) -> None:
        """The commented [profile] block in gigradar.example.toml, uncommented, must load."""
        lines = (REPO / "gigradar.example.toml").read_text(encoding="utf-8").splitlines()
        start = lines.index("# [profile]")
        block = []
        for line in lines[start:]:
            if not line.startswith("# "):
                break
            block.append(line[2:])
        profile = self.load("\n".join(block) + "\n").profile
        self.assertEqual(profile.tiers, ["intermediate", "expert"])
        self.assertEqual(profile.min_hourly, 50.0)
        self.assertIn("Remote only", profile.constraints)

    def test_errors(self) -> None:
        cases = {
            "profile = 1\n": "[profile] must be a table",
            "[profile]\nskils = []\n": "unknown keys ['skils']",
            '[profile]\npath = "nope.md"\n': "nope.md not found",
            '[profile]\nskills = "Rust"\n': "profile.skills must be list",
            '[profile]\nskills = ["Rust", 3]\n': "list of non-empty strings",
            '[profile]\nexclude_keywords = [" "]\n': "list of non-empty strings",
            '[profile]\ntiers = ["Expert"]\n': "profile.tiers must be a subset",
            "[profile]\nconstraints = 5\n": "profile.constraints must be str",
            "[profile]\nmin_hourly = -1\n": "non-negative number",
            "[profile]\nmin_fixed = true\n": "non-negative number",
            '[profile]\nmin_fixed = "1000"\n': "non-negative number",
        }
        for toml, fragment in cases.items():
            with self.subTest(fragment=fragment), self.assertRaises(ConfigError) as ctx:
                self.load(toml)
            self.assertIn(fragment, str(ctx.exception))

    def test_bad_profile_md_is_config_error(self) -> None:
        (self.dir / "profile.md").write_text("no sections\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            self.load("[profile]\n")
        self.assertIn("profile.md: no '## ' sections", str(ctx.exception))
        (self.dir / "profile.md").write_bytes(b"## A\n\xff\xfe\n")
        with self.assertRaises(ConfigError) as ctx:
            self.load("[profile]\n")
        self.assertIn("not UTF-8", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
