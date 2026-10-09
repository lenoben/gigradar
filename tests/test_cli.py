"""Run: .venv/Scripts/python -m unittest discover -s tests   (offline: fake Telegram, fake window, temp home)"""

import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from gigradar import cli
from gigradar.config import HOME_ENV, app_home
from gigradar.webview2 import EVERGREEN_URL, WebView2Missing
from gigradar.store import mark_seen, open_store, save_job_score, save_label
from gigradar.score import Score
from gigradar.search import SearchBlocked
from gigradar.tokens import TokenUnavailable
from test_store import make_job

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)
TOKEN = "987654321:AAcanaryCANARYcanaryCANARYcanary_-xy"
CHAT = "424242"
SEARCH = {"name": "py", "query": "python", "job_type": "hourly"}
PROFILE_MD = "## Web\nI build web apps with Python and TypeScript.\n"


def reply(result: object, ok: bool = True, status: int = 200, description: str = "") -> tuple[int, bytes]:
    body = {"ok": ok, "result": result} if ok else {"ok": False, "description": description}
    return status, json.dumps(body).encode()


class FakeTelegram:
    """Answers the Bot API calls; remembers every URL (which contains the token) it was given."""

    def __init__(self, updates: list | None, me_status: int = 200) -> None:
        self.updates = [] if updates is None else updates
        self.me_status = me_status
        self.calls: list[str] = []
        self.sent: list[str] = []

    def __call__(self, url: str, body: bytes) -> tuple[int, bytes]:
        method = url.rsplit("/", 1)[-1]
        self.calls.append(method)
        if method == "getMe":
            if self.me_status != 200:
                return reply(None, ok=False, status=self.me_status, description=f"Unauthorized for {url}")
            return reply({"username": "my_gigradar_bot"})
        if method == "getUpdates":
            return reply(self.updates)
        if method == "sendMessage":
            self.sent.append(body.decode())
            return reply({"message_id": 1})
        raise AssertionError(method)


def private_update(chat_id: int, name: str) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": chat_id, "type": "private", "first_name": name}, "text": "hi"}}


class CliCase(unittest.TestCase):
    """A temp home, a fake Context, and the canary checks every command shares."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.out = io.StringIO()
        self.telegram = FakeTelegram([private_update(int(CHAT), "Ada")])
        self.tasks: dict[str, str | None] = {}
        self.pauses: list[float] = []
        self.webview2: str | None = "154.0.3.2"
        self.sizes: dict[str, int] = {}
        self.log_capture = io.StringIO()
        handler = logging.StreamHandler(self.log_capture)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        logging.getLogger().addHandler(handler)
        self.addCleanup(self._tmp.cleanup)

    def ctx(self, stdin: str, env: dict[str, str] | None) -> cli.Context:
        return cli.Context(environ={} if env is None else env, home=self.home, stdin=io.StringIO(stdin), stdout=self.out,
                           now=lambda: NOW, post=self.telegram, sleep=self.pauses.append,
                           task_query=lambda name: self.tasks.get(name), webview2_version=lambda: self.webview2,
                           content_length=lambda url: self.sizes.get(url))

    def run_cli(self, *argv: str, stdin: str = "", env: dict[str, str] | None = None) -> tuple[int, list[dict]]:
        self.out.seek(0)
        self.out.truncate()
        code = cli.run(list(argv), self.ctx(stdin, env))
        lines = [json.loads(line) for line in self.out.getvalue().splitlines()]   # every line must be JSON
        self.assert_no_token()
        return code, lines

    def assert_no_token(self) -> None:
        for text in (self.out.getvalue(), self.log_capture.getvalue()):
            self.assertNotIn(TOKEN, text)
            self.assertNotIn(TOKEN.split(":")[1], text)

    def result(self, *argv: str, stdin: str = "", env: dict[str, str] | None = None) -> tuple[int, dict]:
        code, lines = self.run_cli(*argv, stdin=stdin, env=env)
        return code, lines[-1]

    def write_config(self, extra: str = "", env_file: str | None = None) -> Path:
        path = self.home / "gigradar.toml"
        path.write_text('[notify]\nchannels = []\n' + extra + '\n[[searches]]\nname = "py"\nquery = "python"\n',
                        encoding="utf-8")
        if env_file is not None:
            (self.home / ".env").write_text(env_file, encoding="utf-8")
        return path

    def seed_store(self) -> None:
        conn = open_store(self.home / "data" / "gigradar.db", [], NOW)
        mark_seen(conn, [make_job("~01"), make_job("~02")], NOW)
        save_job_score(conn, "~02", Score(77, "Web · matched: Python", "embed", "2"), NOW)
        save_label(conn, "~01", -1, NOW)
        conn.close()


class OutputContractTest(CliCase):
    def test_usage_error_is_json_with_exit_2(self) -> None:
        code, result = self.result("no-such-command")
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "usage")

    def test_every_command_has_json_output_and_accepts_the_json_flag(self) -> None:
        code, result = self.result("doctor", "--json", "--offline")
        self.assertEqual(result["command"], "doctor")
        self.assertIn(code, (cli.EXIT_OK, cli.EXIT_FAIL))

    def test_no_stdout_is_fine(self) -> None:
        ctx = self.ctx("", {})
        ctx.stdout = None   # a windowless exe has no stdout
        self.assertEqual(cli.run(["doctor", "--offline"], ctx), cli.EXIT_FAIL)   # no config: fails, but without crashing


class Utf8Test(CliCase):
    def test_pipes_carry_utf8_whatever_the_console_code_page(self) -> None:
        self.write_config()
        conn = open_store(self.home / "data" / "gigradar.db", [], NOW)
        mark_seen(conn, [replace(make_job("~99"), title="Zoë 👍 · Café")], NOW)
        conn.close()
        env = {**os.environ, "GIGRADAR_HOME": str(self.home), "PYTHONUTF8": "0"}
        env.pop("PYTHONIOENCODING", None)
        done = subprocess.run([sys.executable, "-m", "gigradar.cli", "jobs"], capture_output=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout.decode("utf-8"))["jobs"][0]["title"], "Zoë 👍 · Café")


class AppHomeTest(unittest.TestCase):
    def test_resolution_order(self) -> None:
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        lad = {"LOCALAPPDATA": "C:/lad"}
        self.assertEqual(app_home(lad, False, repo), Path("C:/lad/gigradar"))                 # no repo config: user data dir
        (repo / "gigradar.toml").write_text("", encoding="utf-8")
        self.assertEqual(app_home(lad, False, repo), repo)                                     # source checkout keeps working
        self.assertEqual(app_home(lad, True, repo), Path("C:/lad/gigradar"))                   # a frozen app never uses it
        self.assertEqual(app_home({**lad, HOME_ENV: "D:/h"}, False, repo), Path("D:/h"))       # explicit override wins
        self.assertEqual(app_home({**lad, HOME_ENV: "D:/h"}, True, repo), Path("D:/h"))


class SetupApplyTest(CliCase):
    def answers(self, **extra: object) -> str:
        return json.dumps({"searches": [SEARCH], "notify": {"channels": ["toast"]}, **extra})

    def test_writes_a_loadable_config(self) -> None:
        code, result = self.result("setup-apply", "--answers", "-", "--home", str(self.home), stdin=self.answers(
            profile={"markdown": PROFILE_MD, "skills": ["Python"], "min_hourly": 40, "constraints": 'Remote "only"'},
            scoring={"min_score": 20}))
        self.assertEqual((code, result["ok"]), (0, True), result)
        self.assertEqual(result["written"], ["gigradar.toml", "profile.md"])
        self.assertIsNone(result["backup"])
        self.assertEqual((self.home / "profile.md").read_text(encoding="utf-8"), PROFILE_MD)
        # and the other commands accept it
        code, result = self.result("doctor", "--offline", env={})
        checks = {c["name"]: c for c in result["checks"]}
        self.assertEqual(checks["config"]["status"], "OK")
        self.assertEqual(checks["profile"]["status"], "OK")

    def test_telegram_secrets_go_to_env_only_and_are_never_echoed(self) -> None:
        code, result = self.result("setup-apply", "--answers", "-", "--home", str(self.home), stdin=self.answers(
            notify={"channels": ["telegram"]}, telegram={"bot_token": TOKEN, "chat_id": CHAT}))
        self.assertEqual(code, 0, result)
        env = (self.home / ".env").read_text(encoding="utf-8")
        self.assertEqual(env, f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHAT_ID={CHAT}\n")
        self.assertNotIn(TOKEN, (self.home / "gigradar.toml").read_text(encoding="utf-8"))
        self.assertEqual(result["written"], [".env", "gigradar.toml"])

    def test_refuses_to_overwrite_without_force_then_backs_up_with_force(self) -> None:
        self.write_config(env_file="UPWORK_PROXY=x\n")
        before = (self.home / "gigradar.toml").read_text(encoding="utf-8")
        code, result = self.result("setup-apply", "--answers", "-", "--home", str(self.home), stdin=self.answers())
        self.assertEqual((code, result["error"]["code"]), (1, "exists"))
        self.assertEqual((self.home / "gigradar.toml").read_text(encoding="utf-8"), before)   # untouched
        code, result = self.result("setup-apply", "--answers", "-", "--home", str(self.home), "--force", stdin=self.answers())
        self.assertEqual(code, 0, result)
        backup = Path(result["backup"])
        self.assertEqual((backup / "gigradar.toml").read_text(encoding="utf-8"), before)
        self.assertIn("toast", (self.home / "gigradar.toml").read_text(encoding="utf-8"))
        self.assertEqual((self.home / ".env").read_text(encoding="utf-8"), "UPWORK_PROXY=x\n")   # not in the answers: kept

    def test_invalid_answers_write_nothing(self) -> None:
        for bad in ({"searches": [{"name": "x", "tier": "godlike"}]}, {"searches": []}, {"nope": 1, "searches": [SEARCH]},
                    {"searches": [SEARCH], "telegram": {"bot_token": "short", "chat_id": CHAT}},
                    {"searches": [SEARCH], "profile": {"markdown": "no sections here"}},
                    {"searches": [SEARCH], "notify": {"channels": ["telegram"]}}):
            with self.subTest(bad=bad):
                code, result = self.result("setup-apply", "--answers", "-", "--home", str(self.home), stdin=json.dumps(bad))
                self.assertEqual((code, result["ok"]), (1, False))
                self.assertEqual([p.name for p in self.home.iterdir()], [])   # no file, no leftover staging folder

    def test_a_byte_order_mark_is_accepted(self) -> None:       # Windows PowerShell 5.1 writes one
        path = self.home / "answers.json"
        path.write_text(self.answers(), encoding="utf-8-sig")
        code, result = self.result("setup-apply", "--answers", str(path), "--home", str(self.home / "a"))
        self.assertEqual(code, 0, result)
        code, result = self.result("setup-apply", "--answers", "-", "--home", str(self.home / "b"), stdin="﻿" + self.answers())
        self.assertEqual(code, 0, result)

    def test_answers_must_be_json(self) -> None:
        code, result = self.result("setup-apply", "--answers", "-", "--home", str(self.home), stdin="{oops")
        self.assertEqual((code, result["error"]["code"]), (1, "invalid_answers"))

    def test_answers_from_a_file(self) -> None:
        path = self.home / "answers.json"
        path.write_text(self.answers(), encoding="utf-8")
        code, result = self.result("setup-apply", "--answers", str(path), "--home", str(self.home / "new"))
        self.assertEqual(code, 0, result)
        code, result = self.result("setup-apply", "--answers", str(self.home / "missing.json"), "--home", str(self.home))
        self.assertEqual(result["error"]["code"], "answers_unreadable")


class TelegramConnectTest(CliCase):
    def test_finds_the_chat_and_sends_a_test_message(self) -> None:
        code, result = self.result("telegram-connect", stdin=TOKEN + "\n")
        self.assertEqual((code, result["ok"]), (0, True), result)
        self.assertEqual((result["bot"], result["chat_id"], result["chat_name"]), ("my_gigradar_bot", CHAT, "Ada"))
        self.assertEqual(self.telegram.calls, ["getMe", "getUpdates", "sendMessage"])
        self.assertNotIn("token", {k for k in result})

    def test_explicit_chat_id_skips_discovery(self) -> None:
        code, result = self.result("telegram-connect", "--chat-id", "-100123", stdin=TOKEN)
        self.assertEqual((code, result["chat_id"]), (0, "-100123"))
        self.assertEqual(self.telegram.calls, ["getMe", "sendMessage"])

    def test_no_message_yet(self) -> None:
        self.telegram.updates = [{"update_id": 1, "message": {"chat": {"id": 5, "type": "group"}}}]
        code, result = self.result("telegram-connect", stdin=TOKEN)
        self.assertEqual((code, result["error"]["code"]), (1, "no_message"))
        self.assertEqual(self.telegram.sent, [])

    def test_newest_private_chat_wins(self) -> None:
        self.telegram.updates = [private_update(1, "Old"), private_update(2, "New")]
        self.assertEqual(self.result("telegram-connect", stdin=TOKEN)[1]["chat_id"], "2")

    def test_rejected_token_never_leaks_through_the_error(self) -> None:
        self.telegram.me_status = 401   # the fake echoes the whole URL, token included, in its description
        code, result = self.result("telegram-connect", stdin=TOKEN)
        self.assertEqual((code, result["error"]["code"]), (1, "bad_token"))
        self.assertIn("<redacted>", result["error"]["message"] + "<redacted>")   # message exists; no token (checked by run_cli)

    def test_network_error_text_is_scrubbed(self) -> None:
        def boom(url: str, body: bytes):
            raise OSError(f"connection reset talking to {url}")
        self.telegram = boom   # type: ignore[assignment]
        code, result = self.result("telegram-connect", stdin=TOKEN)
        self.assertEqual((code, result["error"]["code"]), (1, "network"))

    def test_token_with_a_byte_order_mark(self) -> None:
        self.assertEqual(self.result("telegram-connect", stdin="\ufeff" + TOKEN + "\r\n")[0], 0)

    def test_garbage_and_missing_token(self) -> None:
        self.assertEqual(self.result("telegram-connect", stdin="not a token")[1]["error"]["code"], "bad_token")
        self.assertEqual(self.result("telegram-connect", stdin="")[1]["error"]["code"], "no_token")
        self.assertEqual(self.telegram.calls, [])   # nothing was sent anywhere for a bad token

    def test_bad_chat_id(self) -> None:
        self.assertEqual(self.result("telegram-connect", "--chat-id", "abc", stdin=TOKEN)[1]["error"]["code"], "bad_chat_id")


class DoctorTest(CliCase):
    def statuses(self, *argv: str, env: dict[str, str] | None = None) -> dict[str, str]:
        _, result = self.result("doctor", *argv, env=env)
        return {c["name"]: c["status"] for c in result["checks"]}

    def test_missing_config_fails_with_a_fix(self) -> None:
        code, result = self.result("doctor", "--offline")
        self.assertEqual((code, result["status"]), (1, "FAIL"))
        [check] = result["checks"]
        self.assertEqual((check["name"], check["status"]), ("config", "FAIL"))
        self.assertIn("setup", check["fix"])

    def test_fresh_valid_config(self) -> None:
        self.write_config()
        st = self.statuses("--offline")
        self.assertEqual(st["config"], "OK")
        self.assertEqual(st["profile"], "WARN")          # scoring off
        self.assertEqual(st["model"], "OK")              # nothing to download without a profile
        self.assertEqual(st["store"], "WARN")            # not created yet
        self.assertEqual(st["scheduled_task"], "WARN")   # not registered
        self.assertEqual(st["webview_profile"], "WARN")
        self.assertEqual(st["recent_errors"], "OK")

    def test_missing_webview2_runtime_is_a_failure_with_the_download_link(self) -> None:
        self.write_config()
        self.webview2 = None
        code, result = self.result("doctor", "--offline")
        check = next(c for c in result["checks"] if c["name"] == "webview2_runtime")
        self.assertEqual((code, check["status"], result["status"]), (1, "FAIL", "FAIL"))
        self.assertIn(EVERGREEN_URL, check["fix"])
        profile = next(c for c in result["checks"] if c["name"] == "webview_profile")
        self.assertEqual(profile["status"], "WARN")      # a separate check: the profile folder

    def test_installed_webview2_runtime_is_ok_and_not_needed_for_curl(self) -> None:
        self.write_config()
        _, result = self.result("doctor", "--offline")
        check = next(c for c in result["checks"] if c["name"] == "webview2_runtime")
        self.assertEqual((check["status"], check["message"]), ("OK", "WebView2 runtime 154.0.3.2"))
        self.write_config('[search]\nbackend = "curl"\n')
        self.webview2 = None
        self.assertEqual(self.statuses("--offline")["webview2_runtime"], "OK")

    def test_repeated_errors_collapse_into_one_entry_with_a_count(self) -> None:
        self.write_config()
        (self.home / "logs").mkdir()
        lines = [f"2026-10-09 13:03:{i:02d},428 ERROR [pywebview] get_cookies() is not implemented" for i in range(40)]
        lines.insert(10, "2026-10-09 13:00:01,100 ERROR [gigradar] run failed: boom")
        lines.append("2026-10-09 13:04:00,000 INFO [gigradar] done")
        (self.home / "logs" / "gigradar.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        _, result = self.result("doctor", "--offline")
        check = next(c for c in result["checks"] if c["name"] == "recent_errors")
        self.assertEqual(check["status"], "WARN")
        self.assertIn("41 ERROR lines, 2 distinct", check["message"])
        self.assertEqual(check["details"], ["40x ERROR [pywebview] get_cookies() is not implemented",
                                            "ERROR [gigradar] run failed: boom"])

    def test_model_missing_is_a_failure_and_cached_is_ok(self) -> None:
        (self.home / "profile.md").write_text(PROFILE_MD, encoding="utf-8")
        models = self.home / "models"
        self.write_config(f'[profile]\npath = "profile.md"\n[scoring]\nmodel_dir = "models"\n')
        self.assertEqual(self.statuses("--offline")["model"], "FAIL")
        (models / "m").mkdir(parents=True)
        (models / "m" / "model.onnx").write_bytes(b"x")
        self.assertEqual(self.statuses("--offline")["model"], "OK")

    def test_telegram_checks(self) -> None:
        self.write_config(env_file=f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHAT_ID={CHAT}\n")
        toml = (self.home / "gigradar.toml").read_text(encoding="utf-8").replace("channels = []", 'channels = ["telegram"]')
        (self.home / "gigradar.toml").write_text(toml, encoding="utf-8")
        st = self.statuses()
        self.assertEqual((st["telegram_token"], st["telegram_api"], st["telegram_chat"]), ("OK", "OK", "OK"))
        self.telegram.me_status = 401
        _, result = self.result("doctor")
        api = next(c for c in result["checks"] if c["name"] == "telegram_api")
        self.assertEqual(api["status"], "FAIL")
        self.assertTrue(api["fix"])
        self.assertEqual(self.statuses("--offline")["telegram_api"], "WARN")

    def test_missing_telegram_values_fail_in_config_with_a_fix(self) -> None:
        self.write_config()
        toml = (self.home / "gigradar.toml").read_text(encoding="utf-8").replace("channels = []", 'channels = ["telegram"]')
        (self.home / "gigradar.toml").write_text(toml, encoding="utf-8")
        _, result = self.result("doctor", "--offline")
        [check] = result["checks"]
        self.assertEqual((check["name"], check["status"]), ("config", "FAIL"))
        self.assertIn("TELEGRAM_BOT_TOKEN", check["message"])

    def test_store_and_recent_errors(self) -> None:
        self.write_config()
        self.seed_store()
        log = self.home / "logs" / "gigradar.log"
        log.parent.mkdir()
        log.write_text(f"2026-10-09 10:00:00,1 INFO [gigradar] run start\n"
                       f"2026-10-09 10:00:01,1 ERROR [gigradar] run failed talking to bot{TOKEN}\n", encoding="utf-8")
        (self.home / ".env").write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHAT_ID={CHAT}\n", encoding="utf-8")
        _, result = self.result("doctor", "--offline")
        checks = {c["name"]: c for c in result["checks"]}
        self.assertEqual(checks["store"]["status"], "OK")
        self.assertIn("2 jobs", checks["store"]["message"])
        self.assertEqual(checks["recent_errors"]["status"], "WARN")
        self.assertIn("run failed", checks["recent_errors"]["message"])   # and no token: run_cli asserts it

    def test_scheduled_task_results(self) -> None:
        self.write_config()
        header = '"HostName","TaskName","Next Run Time","Status","Last Run Time","Last Result"\n'

        def row(result: int) -> str:
            return header + f'"PC","\\gigradar\\gigradar-watch","n/a","Ready","2026-10-09 09:53:00","{result}"\n'
        for result, expected in ((0, "OK"), (267011, "WARN"), (75, "WARN"), (3221225477, "FAIL"), (1, "FAIL")):
            with self.subTest(result=result):
                self.tasks["gigradar-watch"] = row(result)
                self.assertEqual(self.statuses("--offline")["scheduled_task"], expected)
        self.tasks["other"] = row(0)
        self.assertEqual(self.statuses("--offline", "--task-name", "other")["scheduled_task"], "OK")
        self.tasks["gigradar-watch"] = None
        self.assertEqual(self.statuses("--offline")["scheduled_task"], "WARN")


class JobsAndLabelTest(CliCase):
    def test_jobs_lists_newest_first_with_scores_and_labels(self) -> None:
        self.write_config()
        self.seed_store()
        code, result = self.result("jobs", "--limit", "5")
        self.assertEqual(code, 0, result)
        by_id = {j["id"]: j for j in result["jobs"]}
        self.assertEqual(set(by_id), {"~01", "~02"})
        self.assertEqual((by_id["~02"]["score"], by_id["~02"]["reason"], by_id["~02"]["label"]), (77, "Web · matched: Python", None))
        self.assertEqual((by_id["~01"]["score"], by_id["~01"]["label"]), (None, -1))
        self.assertIn("Hourly", by_id["~01"]["pay"])
        self.assertEqual(len(self.result("jobs", "--limit", "1")[1]["jobs"]), 1)

    def test_jobs_without_a_store_is_a_clean_error(self) -> None:
        self.write_config()
        code, result = self.result("jobs")
        self.assertEqual((code, result["error"]["code"]), (1, "store"))

    def test_label_up_down_clear(self) -> None:
        self.write_config()
        self.seed_store()
        for value, expected in (("up", 1), ("down", -1), ("clear", None)):
            code, result = self.result("label", "--job-id", "~02", "--value", value)
            self.assertEqual((code, result["label"]), (0, value))
            jobs = {j["id"]: j for j in self.result("jobs")[1]["jobs"]}
            self.assertEqual(jobs["~02"]["label"], expected)

    def test_label_unknown_job(self) -> None:
        self.write_config()
        self.seed_store()
        code, result = self.result("label", "--job-id", "~nope", "--value", "up")
        self.assertEqual((code, result["error"]["code"]), (1, "not_found"))


class RunOnceTest(CliCase):
    def test_reports_the_watchers_exit_code(self) -> None:
        self.write_config()
        self.seed_store()
        for watch_code, ok, meaning in ((0, True, "ok"), (75, False, "stopped"), (2, False, "config"), (1, False, "error")):
            with self.subTest(code=watch_code), mock.patch("gigradar.watch.main", return_value=watch_code) as watch_main:
                code, result = self.result("run-once")
                self.assertEqual((code, result["ok"], result["exit_code"]), (watch_code, ok, watch_code))
                self.assertIn(meaning, result["meaning"])
                self.assertEqual(result["jobs_seen_before"], 2)
                argv = watch_main.call_args.args[0]
                self.assertEqual(argv[:2], ["--config", str(self.home / "gigradar.toml")])
                self.assertEqual(Path(argv[3]), self.home / "logs" / "gigradar.log")

    def test_config_error_is_reported_without_running(self) -> None:
        with mock.patch("gigradar.watch.main") as watch_main:
            code, result = self.result("run-once")
        self.assertEqual((code, result["error"]["code"]), (1, "config"))
        watch_main.assert_not_called()


class FakeSearcher:
    instances: list["FakeSearcher"] = []

    def __init__(self, profile: Path, notifier: object, count: int, visible: bool) -> None:
        self.args = (profile, count, visible)
        FakeSearcher.instances.append(self)

    outcome: object = [make_job("~10"), make_job("~11")]
    searches = 0
    run_errors: list[Exception] = []   # raised by run() before the page loads, one per window

    def run(self, work):
        if FakeSearcher.run_errors:
            raise FakeSearcher.run_errors.pop(0)

        def search(spec):
            FakeSearcher.searches += 1
            outcome = FakeSearcher.outcome
            if isinstance(outcome, list) and outcome and isinstance(outcome[0], Exception):   # one outcome per attempt
                step = outcome[min(FakeSearcher.searches, len(outcome)) - 1]
                if isinstance(step, Exception):
                    raise step
                return step
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return work(search)


class UpworkCheckTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        FakeSearcher.instances.clear()
        FakeSearcher.searches = 0
        FakeSearcher.run_errors = []
        FakeSearcher.outcome = [make_job("~10"), make_job("~11")]
        patcher = mock.patch("gigradar.webview_search.WebViewSearcher", FakeSearcher)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_opens_a_visible_window_runs_one_search_and_seeds_silently(self) -> None:
        self.write_config()
        code, lines = self.run_cli("upwork-check")
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[0]["event"], "window")
        self.assertEqual({k: lines[-1][k] for k in ("ok", "search", "jobs_found", "seeded", "jobs_seen_total")},
                         {"ok": True, "search": "py", "jobs_found": 2, "seeded": 2, "jobs_seen_total": 2})
        [searcher] = FakeSearcher.instances
        self.assertEqual(searcher.args[1:], (1, True))          # one search, window visible
        # a second check seeds nothing new and the search now counts as established
        code, result = self.result("upwork-check")
        self.assertEqual((result["jobs_found"], result["seeded"], result["jobs_seen_total"]), (2, 0, 2))

    def test_a_page_that_is_not_ready_yet_is_retried_in_the_same_window(self) -> None:
        self.write_config()
        FakeSearcher.outcome = [SearchBlocked("fetch() gave no result within 30s"), [make_job("~10")]]
        code, lines = self.run_cli("upwork-check")
        self.assertEqual(code, 0, lines)
        self.assertEqual([line["event"] for line in lines if "event" in line], ["window", "retry"])
        self.assertEqual((lines[-1]["jobs_found"], FakeSearcher.searches, len(FakeSearcher.instances)), (1, 2, 1))
        self.assertEqual(self.pauses, [cli.CHECK_PAUSES_S[0]])
        self.assertEqual(lines[-1]["attempts"], {"window": 1, "fetch": 2})

    def test_gives_up_after_the_last_attempt(self) -> None:
        self.write_config()
        FakeSearcher.outcome = [SearchBlocked("HTTP 403 cf-mitigated=challenge")]
        code, result = self.result("upwork-check")
        self.assertEqual((code, result["error"]["code"], FakeSearcher.searches), (1, "blocked", cli.CHECK_ATTEMPTS))
        self.assertFalse((self.home / "data" / "gigradar.db").exists())
        self.assertEqual(self.pauses, list(cli.CHECK_PAUSES_S))      # short backoff, nothing longer

    def test_blocked_or_unsolved_check_is_a_clean_error_and_stores_nothing(self) -> None:
        self.write_config()
        FakeSearcher.outcome = TokenUnavailable("no Upwork token: challenge not solved within 300s")
        code, result = self.result("upwork-check")
        self.assertEqual((code, result["error"]["code"]), (1, "blocked"))
        self.assertFalse((self.home / "data" / "gigradar.db").exists())

    def test_a_slow_browser_component_is_a_retryable_error(self) -> None:
        from webview.errors import WebViewException
        self.write_config()
        FakeSearcher.outcome = WebViewException("Main window failed to start")
        code, result = self.result("upwork-check")
        self.assertEqual((code, result["error"]["code"]), (1, "window_failed"))
        self.assertIn("again", result["error"]["message"])

    def test_a_slow_window_start_opens_a_new_window_and_succeeds(self) -> None:
        from webview.errors import WebViewException
        self.write_config()
        FakeSearcher.run_errors = [WebViewException("Main window failed to start")] * 2
        code, lines = self.run_cli("upwork-check")
        self.assertEqual(code, 0, lines)
        retries = [line for line in lines if line.get("event") == "retry"]
        self.assertEqual([(r["kind"], r["attempt"]) for r in retries], [("window", 2), ("window", 3)])
        self.assertEqual(lines[-1]["attempts"], {"window": 3, "fetch": 1})
        self.assertEqual(self.pauses, list(cli.CHECK_PAUSES_S))

    def test_a_window_that_never_starts_gives_up_after_three_tries(self) -> None:
        from webview.errors import WebViewException
        self.write_config()
        FakeSearcher.run_errors = [WebViewException("Main window failed to start")] * 5
        code, result = self.result("upwork-check")
        self.assertEqual((code, result["error"]["code"]), (1, "window_failed"))
        self.assertIn("3 times", result["error"]["message"])
        self.assertEqual(len(FakeSearcher.run_errors), 2)        # exactly three windows were tried

    def test_missing_webview2_fails_fast_before_any_window(self) -> None:
        self.write_config()
        self.webview2 = None
        code, lines = self.run_cli("upwork-check")
        self.assertEqual((code, lines[-1]["error"]["code"]), (1, "webview2_missing"))
        self.assertIn(EVERGREEN_URL, lines[-1]["error"]["message"])
        self.assertEqual((len(lines), FakeSearcher.instances, self.pauses), (1, [], []))     # no window event, no retry

    def test_the_searcher_reporting_a_missing_runtime_is_not_retried(self) -> None:
        self.write_config()
        FakeSearcher.run_errors = [WebView2Missing("The Microsoft Edge WebView2 runtime is not installed " + EVERGREEN_URL)] * 3
        code, result = self.result("upwork-check")
        self.assertEqual((code, result["error"]["code"]), (1, "webview2_missing"))
        self.assertEqual((len(FakeSearcher.run_errors), self.pauses), (2, []))

    def test_curl_backend_is_not_applicable(self) -> None:
        self.write_config('[search]\nbackend = "curl"\n')
        code, result = self.result("upwork-check")
        self.assertEqual((code, result["error"]["code"]), (1, "not_applicable"))


class NotifyTestTest(CliCase):
    def test_sends_one_sample_job_through_the_configured_channels(self) -> None:
        self.write_config()
        sent: list = []

        class Recorder:
            def notify_jobs(self, alerts):
                sent.extend(alerts)

        with mock.patch("gigradar.notify.build_notifier", return_value=Recorder()):
            code, result = self.result("notify-test")
        self.assertEqual((code, result["channels"]), (0, ["log"]))
        self.assertEqual(len(sent), 1)
        self.assertIn("gigradar test", sent[0].job.title)

    def test_a_failing_channel_is_a_clean_error_without_secrets(self) -> None:
        self.write_config()

        class Broken:
            def notify_jobs(self, alerts):
                raise RuntimeError(f"notification failed: TelegramNotifier: HTTP 401 at /bot{TOKEN}/sendMessage")

        with mock.patch("gigradar.notify.build_notifier", return_value=Broken()):
            code, result = self.result("notify-test", env={"TELEGRAM_BOT_TOKEN": TOKEN})
        self.assertEqual((code, result["error"]["code"]), (1, "notify_failed"))

    def test_config_error_comes_first(self) -> None:
        code, result = self.result("notify-test")
        self.assertEqual((code, result["error"]["code"]), (1, "config"))


class HeadContentLengthTest(unittest.TestCase):
    class Response:
        headers = {"Content-Length": "1234"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_reads_the_content_length(self) -> None:
        with mock.patch("urllib.request.urlopen", return_value=self.Response()):
            self.assertEqual(cli.head_content_length("https://example.test/f"), 1234)

    def test_unreachable_means_unknown(self) -> None:
        import urllib.error
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            self.assertIsNone(cli.head_content_length("https://example.test/f"))


class FakeEmbedder:
    calls: list[bool] = []
    fail = False

    def __init__(self, model: str, cache_dir: Path, offline: bool) -> None:
        from gigradar.embed import EmbedderError
        FakeEmbedder.calls.append(offline)
        if FakeEmbedder.fail:
            raise EmbedderError(f"can't load model {model}")
        if not offline:
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / "model.onnx").write_bytes(b"x" * 5000)
            import time
            time.sleep(0.08)   # long enough for a few progress lines
        self.model = model

    def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class ModelDownloadTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        FakeEmbedder.calls.clear()
        FakeEmbedder.fail = False
        self.url = "https://example.test/m.onnx"
        for patcher in (mock.patch("gigradar.embed.FastEmbedder", FakeEmbedder),
                        mock.patch("gigradar.embed.model_file_url", lambda model: self.url),
                        mock.patch.object(cli, "PROGRESS_INTERVAL_S", 0.01)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_progress_lines_then_one_result(self) -> None:
        self.write_config(f'[scoring]\nmodel_dir = "models"\n')
        code, lines = self.run_cli("model-download")
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[0]["event"], "start")
        progress = [line for line in lines if line.get("event") == "progress"]
        self.assertTrue(progress)
        self.assertEqual(progress[-1]["bytes"], 5000)
        self.assertTrue(all("event" in line for line in lines[:-1]))
        self.assertEqual(lines[-1]["ok"], True)
        self.assertEqual((lines[-1]["dim"], lines[-1]["bytes"]), (3, 5000))
        self.assertEqual(FakeEmbedder.calls, [False, True])     # download, then the offline load check

    def test_total_comes_from_the_servers_content_length(self) -> None:
        self.write_config(f'[scoring]\nmodel_dir = "models"\n')
        self.sizes[self.url] = 4_000
        _, lines = self.run_cli("model-download")
        self.assertEqual(lines[0]["total_bytes"], 4_000 + cli.MODEL_EXTRAS_BYTES)
        percents = [line["percent"] for line in lines if line.get("event") == "progress"]
        self.assertTrue(percents and all(0 <= p <= 99 for p in percents))

    def test_unknown_size_means_no_percentage_instead_of_a_wrong_one(self) -> None:
        self.write_config(f'[scoring]\nmodel_dir = "models"\n')
        _, lines = self.run_cli("model-download")
        self.assertIsNone(lines[0]["total_bytes"])
        self.assertTrue(all(line["percent"] is None for line in lines if line.get("event") == "progress"))

    def test_the_hugging_face_cache_is_not_counted_twice(self) -> None:
        blobs = self.home / "m" / "models--x--y" / "blobs"
        snap = self.home / "m" / "models--x--y" / "snapshots" / "rev"
        blobs.mkdir(parents=True)
        snap.mkdir(parents=True)
        (blobs / "abc").write_bytes(b"x" * 1000)
        (snap / "model.onnx").write_bytes(b"x" * 1000)          # a copy where symlinks are not available
        self.assertEqual(cli._dir_bytes(self.home / "m"), 1000)
        (self.home / "plain").mkdir()
        (self.home / "plain" / "f").write_bytes(b"x" * 7)
        self.assertEqual(cli._dir_bytes(self.home / "plain"), 7)

    def test_failure_is_a_clean_error(self) -> None:
        self.write_config()
        FakeEmbedder.fail = True
        code, result = self.result("model-download")
        self.assertEqual((code, result["error"]["code"]), (1, "model"))


if __name__ == "__main__":
    unittest.main()
