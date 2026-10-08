"""Run: .venv/Scripts/python -m unittest discover -s tests   (offline; the SDK tests skip without `mcp`)"""

import asyncio
import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gigradar import mcp_tools
from gigradar.config import load_config
from gigradar.mcp_server import config_environ, print_config
from gigradar.rubric import RUBRIC, RUBRIC_VERSION, SCORER
from gigradar.score import Score
from gigradar.store import (load_labels, load_scores, mark_seen, open_store, save_job_score, save_label,
                            seen_count)
from test_score import PROFILE, make_job

try:
    import mcp  # noqa: F401
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
EMBED_REASON = "SENTINEL-EMBED-REASON"
JOB_KEYS = {"job_id", "title", "url", "pay", "job_type", "tier", "hourly_min", "hourly_max", "fixed_budget",
            "published", "skills", "description_untrusted", "description_truncated"}


def job(n: int, **fields):
    return make_job(url=f"https://www.upwork.com/jobs/~{n:02d}", title=f"Job {n}", **fields)


class StoreCase(unittest.TestCase):
    """A real store file (the SQLite authorizer needs one) with 4 jobs, two labels and an embed score."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "data" / "gigradar.db"
        setup = open_store(self.path, [], NOW)
        for n in range(1, 5):  # job 4 is the newest
            mark_seen(setup, [job(n, description=f"Description {n}")], NOW + timedelta(minutes=n))
        save_label(setup, "~01", 1, NOW)
        save_label(setup, "~02", -1, NOW)
        save_job_score(setup, "~01", Score(77, EMBED_REASON, "embed", "2"), NOW)
        setup.close()
        self.conn = mcp_tools.open_scoring_store(self.path)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def other_writer(self, sql: str, rows: list[tuple]) -> None:
        """Another process' write (the watcher), on its own connection."""
        with contextlib.closing(sqlite3.connect(self.path)) as other, other:
            other.executemany(sql, rows)


class BlindScoringTest(StoreCase):
    def test_scoring_tools_never_return_labels_or_other_scores(self) -> None:
        listing = mcp_tools.unscored_jobs(self.conn, 10)
        self.assertEqual(set(listing), {"notice", "jobs"})
        self.assertEqual(len(listing["jobs"]), 4)
        for view in listing["jobs"]:
            self.assertEqual(set(view), JOB_KEYS)
        single = mcp_tools.get_job(self.conn, "~01", False)
        self.assertEqual(set(single), JOB_KEYS | {"notice"})
        for result in (listing, single, mcp_tools.profile_view(PROFILE)):
            text = json.dumps(result).lower()
            for leak in (EMBED_REASON.lower(), '"label"', '"scores"', '"scorer"', "embed", "cosine", "77"):
                self.assertNotIn(leak, text)

    def test_inspection_view_shows_label_and_every_score(self) -> None:
        mcp_tools.set_score(self.conn, "~01", 60, "fits", NOW)
        view = mcp_tools.get_job(self.conn, "~01", True)
        self.assertEqual(view["label"], 1)
        self.assertEqual({(s["scorer"], s["value"]) for s in view["scores"]}, {("embed", 77), ("claude", 60)})
        self.assertIsNone(mcp_tools.get_job(self.conn, "~03", True)["label"])
        self.assertEqual(mcp_tools.get_job(self.conn, "~03", True)["scores"], [])


class UnscoredJobsTest(StoreCase):
    def test_newest_first_and_skips_jobs_with_a_current_claude_score(self) -> None:
        self.assertEqual([j["job_id"] for j in mcp_tools.unscored_jobs(self.conn, 10)["jobs"]],
                         ["~04", "~03", "~02", "~01"])  # ~01 has an embed score only: still unscored
        mcp_tools.set_score(self.conn, "~03", 50, "ok", NOW)
        self.assertEqual([j["job_id"] for j in mcp_tools.unscored_jobs(self.conn, 10)["jobs"]],
                         ["~04", "~02", "~01"])

    def test_a_score_from_another_rubric_version_does_not_count(self) -> None:
        save_job_score(self.conn, "~04", Score(50, "old rubric", SCORER, "0"), NOW)
        self.assertIn("~04", [j["job_id"] for j in mcp_tools.unscored_jobs(self.conn, 10)["jobs"]])

    def test_limit(self) -> None:
        self.assertEqual(len(mcp_tools.unscored_jobs(self.conn, 2)["jobs"]), 2)
        for bad in (0, -3):
            with self.assertRaises(mcp_tools.ToolInputError):
                mcp_tools.unscored_jobs(self.conn, bad)

    def test_limit_is_capped(self) -> None:
        rows = [(f"~x{i:03d}", NOW.isoformat(), "u", "t", None, json.dumps(asdict(job(i)))) for i in range(60)]
        self.other_writer("INSERT INTO seen_jobs VALUES (?, ?, ?, ?, ?, ?)", rows)
        self.assertEqual(len(mcp_tools.unscored_jobs(self.conn, 500)["jobs"]), mcp_tools.MAX_LIMIT)

    def test_descriptions_are_cut_in_the_listing_but_whole_in_get_job(self) -> None:
        long_text = "word " * 1000
        self.other_writer("UPDATE seen_jobs SET payload = json_set(payload, '$.description', ?) "
                          "WHERE job_id = '~04'", [(long_text,)])
        listed = mcp_tools.unscored_jobs(self.conn, 1)["jobs"][0]
        self.assertTrue(listed["description_truncated"])
        self.assertLessEqual(len(listed["description_untrusted"]), mcp_tools.DESCRIPTION_CHARS + 2)
        full = mcp_tools.get_job(self.conn, "~04", False)
        self.assertEqual(full["description_untrusted"], long_text)
        self.assertFalse(full["description_truncated"])

    def test_untrusted_notice_everywhere(self) -> None:
        self.assertIn("untrusted", mcp_tools.unscored_jobs(self.conn, 1)["notice"])
        self.assertIn("untrusted", mcp_tools.get_job(self.conn, "~01", False)["notice"])

    def test_unknown_job(self) -> None:
        with self.assertRaises(mcp_tools.ToolInputError):
            mcp_tools.get_job(self.conn, "~nope", False)


class SetScoreTest(StoreCase):
    def test_writes_one_claude_row_at_the_rubric_version(self) -> None:
        result = mcp_tools.set_score(self.conn, "~02", 64, "  decent\n fit ", NOW)
        self.assertEqual(result, {"job_id": "~02", "scorer": "claude", "version": RUBRIC_VERSION,
                                  "value": 64, "reason": "decent fit"})  # whitespace collapsed
        self.assertEqual(load_scores(self.conn, SCORER, RUBRIC_VERSION),
                         {"~02": Score(64, "decent fit", SCORER, RUBRIC_VERSION)})

    def test_rescoring_replaces_and_leaves_other_scorers_alone(self) -> None:
        mcp_tools.set_score(self.conn, "~01", 10, "first", NOW)
        mcp_tools.set_score(self.conn, "~01", 90, "second", NOW)
        self.assertEqual(load_scores(self.conn, SCORER, RUBRIC_VERSION)["~01"].value, 90)
        self.assertEqual(load_scores(self.conn, "embed", "2"), {"~01": Score(77, EMBED_REASON, "embed", "2")})

    def test_no_writes_to_seen_jobs_or_labels(self) -> None:
        before = (seen_count(self.conn), load_labels(self.conn))
        for n in range(1, 5):
            mcp_tools.set_score(self.conn, f"~0{n}", n * 10, "x", NOW)
        self.assertEqual((seen_count(self.conn), load_labels(self.conn)), before)

    def test_validation(self) -> None:
        bad = [("~01", 101, "x"), ("~01", -1, "x"), ("~01", True, "x"), ("~01", "50", "x"), ("~01", 50.5, "x"),
               ("~01", 50, ""), ("~01", 50, "  \n "), ("~01", 50, "x" * 201), ("~01", 50, None), ("~nope", 50, "x")]
        for jid, value, reason in bad:
            with self.subTest(value=value, reason=reason, jid=jid), self.assertRaises(mcp_tools.ToolInputError):
                mcp_tools.set_score(self.conn, jid, value, reason, NOW)
        self.assertEqual(load_scores(self.conn, SCORER, RUBRIC_VERSION), {})
        mcp_tools.set_score(self.conn, "~01", 0, "x" * 200, NOW)   # both boundaries are fine
        mcp_tools.set_score(self.conn, "~02", 100, "x", NOW)


class SqliteGuardTest(StoreCase):
    def test_connection_can_write_nothing_but_scores(self) -> None:
        for sql in ("INSERT INTO labels VALUES ('~03', 1, 'now')", "UPDATE labels SET label = 1",
                    "DELETE FROM seen_jobs", "DELETE FROM labels",
                    "INSERT INTO embeddings VALUES ('a', 'm', 1, x'00', 't')",
                    "CREATE TABLE evil (a)", "DROP TABLE scores", "ATTACH DATABASE ':memory:' AS other",
                    "PRAGMA user_version = 9"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                self.conn.execute(sql)
        self.assertEqual(load_labels(self.conn), {"~01": 1, "~02": -1})
        self.conn.execute("SELECT * FROM seen_jobs").fetchall()   # reads are fine


class ProfileAndConfigTest(unittest.TestCase):
    def test_profile_view_is_the_profile_only(self) -> None:
        view = mcp_tools.profile_view(PROFILE)
        self.assertEqual(set(view), {"skill_areas", "skills", "hard_rules"})
        self.assertEqual(view["hard_rules"]["min_hourly_usd"], 50.0)
        self.assertEqual(view["skill_areas"], [{"heading": "Rust backend", "text": "axum"}])

    def test_server_environment_has_no_real_secrets(self) -> None:
        env = config_environ({"TELEGRAM_BOT_TOKEN": "123:real", "TELEGRAM_CHAT_ID": "42",
                              "UPWORK_PROXY": "http://u:p@h", "PATH": "p"})
        self.assertEqual(env["PATH"], "p")
        self.assertNotIn("real", json.dumps(env))
        self.assertNotIn("http://u:p@h", json.dumps(env))

    def test_a_config_with_telegram_and_proxy_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            toml = Path(tmp) / "gigradar.toml"
            toml.write_text('[notify]\nchannels = ["telegram"]\n[token]\nuse_proxy = true\n'
                            '[[searches]]\nname = "a"\n', encoding="utf-8")
            cfg = load_config(toml, config_environ({"TELEGRAM_BOT_TOKEN": "123:real"}))
        self.assertEqual(cfg.telegram_bot_token, "unused")
        self.assertEqual(cfg.proxy, "unused")


class PrintConfigTest(unittest.TestCase):
    def test_command_is_computed_for_this_machine(self) -> None:
        out = io.StringIO()
        print_config(Path("some dir") / "gigradar.toml", out)
        text = out.getvalue()
        self.assertIn("claude mcp add --scope user gigradar", text)
        self.assertNotIn("--scope project", text)
        self.assertIn(f"PYTHONPATH={REPO.as_posix()}", text)
        self.assertIn("-m gigradar.mcp_server --config", text)
        self.assertNotIn("\\", text)   # forward slashes only, safe to paste into Git Bash


def server_command(config: Path) -> list[str]:
    return [sys.executable, "-m", "gigradar.mcp_server", "--config", str(config)]


@unittest.skipUnless(HAVE_MCP, "mcp is not installed (pip install -r requirements-mcp.txt)")
class StdioRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "profile.md").write_text("## Backend\nrust api services\n", encoding="utf-8")
        self.config = self.dir / "gigradar.toml"
        self.config.write_text('[[searches]]\nname = "a"\n[profile]\nskills = ["Rust"]\n', encoding="utf-8")
        conn = open_store(self.dir / "data" / "gigradar.db", [], NOW)
        mark_seen(conn, [job(1), job(2)], NOW)
        save_label(conn, "~01", 1, NOW)
        conn.close()
        self.env = {**os.environ, "PYTHONPATH": str(REPO), "TELEGRAM_BOT_TOKEN": "123:real-secret"}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_stdout_carries_only_protocol_messages(self) -> None:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        proc = subprocess.Popen(server_command(self.config), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=self.env)
        lines = []
        watchdog = threading.Timer(60, proc.kill)   # a hung server must fail the test, not block it
        watchdog.start()
        try:
            proc.stdin.write("\n".join(json.dumps(r) for r in requests) + "\n")
            proc.stdin.flush()
            # Read until the tools/list answer (id 2): closing stdin earlier would race with the replies.
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                lines.append(line.strip())
                if json.loads(line).get("id") == 2:
                    break
            proc.stdin.close()
            rest = proc.stdout.read()
            stderr = proc.stderr.read()
            proc.wait(timeout=30)
        finally:
            watchdog.cancel()
            proc.kill()
            for stream in (proc.stdout, proc.stderr):
                stream.close()
        lines += [line for line in rest.splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 2, stderr)
        for line in lines:
            self.assertEqual(json.loads(line).get("jsonrpc"), "2.0", line)
        self.assertNotIn("real-secret", "\n".join(lines) + stderr)

    def test_session_with_the_sdk_client(self) -> None:
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        params = StdioServerParameters(command=sys.executable, args=server_command(self.config)[1:], env=self.env)

        async def session() -> dict:
            async with Client(params) as client:
                got = {"tools": sorted(t.name for t in (await client.list_tools()).tools),
                       "prompts": [p.name for p in (await client.list_prompts()).prompts]}
                got["prompt_text"] = (await client.get_prompt("score_jobs")).messages[0].content.text
                got["profile"] = (await client.call_tool("get_profile", {})).content[0].text
                got["jobs"] = (await client.call_tool("get_unscored_jobs", {"limit": 5})).content[0].text
                got["ok"] = await client.call_tool("set_score", {"job_id": "~01", "value": 70, "reason": "fits"})
                got["bad"] = await client.call_tool("set_score", {"job_id": "~01", "value": 700, "reason": "x"})
                return got

        got = asyncio.run(session())
        self.assertEqual(got["tools"], ["get_job", "get_profile", "get_unscored_jobs", "set_score"])
        self.assertEqual(got["prompts"], ["score_jobs"])
        self.assertEqual(got["prompt_text"], RUBRIC)
        self.assertNotIn("real-secret", got["profile"])
        self.assertEqual(len(json.loads(got["jobs"])["jobs"]), 2)
        self.assertNotIn('"label"', got["jobs"])
        self.assertFalse(got["ok"].is_error)
        self.assertTrue(got["bad"].is_error)
        self.assertIn("0-100", got["bad"].content[0].text)   # the reason reaches Claude
        conn = open_store(self.dir / "data" / "gigradar.db", [], NOW)
        self.assertEqual(load_scores(conn, SCORER, RUBRIC_VERSION)["~01"].value, 70)
        conn.close()

    def test_missing_store_fails_clearly(self) -> None:
        (self.dir / "data" / "gigradar.db").unlink()
        proc = subprocess.run(server_command(self.config), input="", capture_output=True, text=True,
                              env=self.env, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no store at", proc.stderr)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
