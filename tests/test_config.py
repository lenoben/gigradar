"""Run: .venv/Scripts/python -m unittest discover -s tests   (stdlib only, no network)"""

import tempfile
import unittest
from pathlib import Path

from gigradar.config import ConfigError, load_config, load_dotenv

REPO = Path(__file__).resolve().parent.parent

MINIMAL = """
[[searches]]
name = "py"
query = "python"
"""


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def load(self, toml: str, env: dict[str, str]):
        path = self.dir / "gigradar.toml"
        path.write_text(toml, encoding="utf-8")
        return load_config(path, env)

    def assertConfigError(self, toml: str, fragment: str) -> None:
        with self.assertRaises(ConfigError) as ctx:
            self.load(toml, {})
        self.assertIn(fragment, str(ctx.exception))

    def test_example_file_is_valid(self) -> None:
        cfg = load_config(REPO / "gigradar.example.toml", {})
        self.assertEqual([s.name for s in cfg.searches], ["python-expert-hourly", "nextjs-fixed"])
        self.assertEqual(cfg.searches[0].filters.tier, "expert")
        self.assertEqual(cfg.searches[0].filters.hourly_rate, "50-150")

    def test_defaults(self) -> None:
        cfg = self.load(MINIMAL, {})
        self.assertEqual(cfg.db_path, self.dir / "data" / "gigradar.db")
        self.assertEqual(cfg.searches[0].limit, 30)
        self.assertEqual(cfg.searches[0].filters.sort, "recency")
        self.assertEqual(cfg.token_sources, ["manual", "fetch"])
        self.assertFalse(cfg.use_proxy)
        self.assertIsNone(cfg.proxy)

    def test_secrets_come_from_env(self) -> None:
        env = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42", "UPWORK_PROXY": "http://p:1"}
        cfg = self.load(MINIMAL, env)
        self.assertEqual((cfg.telegram_bot_token, cfg.telegram_chat_id), ("123:abc", "42"))
        self.assertIsNone(cfg.proxy)  # set, but use_proxy is false
        cfg = self.load(MINIMAL + "[token]\nuse_proxy = true\n", env)
        self.assertEqual(cfg.proxy, "http://p:1")

    def test_secrets_in_toml_rejected(self) -> None:
        self.assertConfigError(MINIMAL + '[telegram]\nbot_token = "123:abc"\n', "put secrets in .env")
        self.assertConfigError(MINIMAL + '[token]\nproxy = "http://u:p@h:1"\n', "put secrets in .env")

    def test_use_proxy_without_env_rejected(self) -> None:
        self.assertConfigError(MINIMAL + "[token]\nuse_proxy = true\n", "UPWORK_PROXY")

    def test_validation_errors(self) -> None:
        cases = {
            "": "at least one [[searches]]",
            '[[searches]]\nquery = "x"\n': "non-empty name",
            '[[searches]]\nname = "a"\nqury = "x"\n': "unknown keys ['qury']",
            '[[searches]]\nname = "a"\ntier = "EXPERT"\n': "tier must be one of",
            '[[searches]]\nname = "a"\nhourly_rate = "1-2"\nfixed_budget = "5-"\n': "mutually exclusive",
            '[[searches]]\nname = "a"\nhourly_rate = "1-2"\njob_type = "fixed"\n': "hourly-only",
            '[[searches]]\nname = "a"\nfixed_budget = "5-"\njob_type = "hourly"\n': "fixed-only",
            '[[searches]]\nname = "a"\nlimit = 500\n': "limit must be 1..100",
            '[[searches]]\nname = "a"\nlimit = true\n': "must be int",
            MINIMAL + MINIMAL: "duplicate search names",
            MINIMAL + '[token]\nsources = ["browser"]\n': "token.sources",
            "[[searches]\n": "gigradar.toml",
        }
        for toml, fragment in cases.items():
            with self.subTest(fragment=fragment):
                self.assertConfigError(toml, fragment)

    def test_missing_file(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(self.dir / "nope.toml", {})

    def test_dotenv(self) -> None:
        path = self.dir / "dotenv"
        path.write_text('# c\n\nA=1\nexport B = "two words"\nC=\'3\'\nKEEP=from-file\n', encoding="utf-8")
        env = {"KEEP": "from-env"}
        load_dotenv(path, env)
        self.assertEqual(env, {"A": "1", "B": "two words", "C": "3", "KEEP": "from-env"})
        load_dotenv(self.dir / "missing", env)  # no error
        path.write_text("garbage\n", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_dotenv(path, {})


if __name__ == "__main__":
    unittest.main()
