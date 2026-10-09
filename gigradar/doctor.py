"""doctor: one OK / WARN / FAIL line per thing that has to work, each with a one-line fix.

Used by `python -m gigradar.cli doctor`. Read-only: it never writes, never opens a window and never
creates the store. Secrets are never part of a result: error text is scrubbed of the bot token.
"""

from __future__ import annotations

import csv
import io
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from gigradar.config import Config, ConfigError, load_config, load_dotenv
from gigradar.store import StoreError, open_existing, seen_count
from gigradar.telegram import PostFn
from gigradar.tgconnect import ConnectError, get_me

OK, WARN, FAIL = "OK", "WARN", "FAIL"
TASK_FOLDER = "\\gigradar\\"
LOG_TAIL_LINES = 300
ERROR_CHARS = 200
RESULT_MEANING = {75: "stopped early (Upwork check not passed); retried at the next run"}
NOT_YET_RUN, RUNNING = 267011, 267009
ERROR_LINE = re.compile(r"\sERROR\s")

# (task name) -> csv text of `schtasks /query /v /fo csv`, or None when the task does not exist
TaskQueryFn = Callable[[str], str | None]


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    message: str
    fix: str | None


def ok(name: str, message: str) -> Check:
    return Check(name, OK, message, None)


def schtasks_query(task_name: str) -> str | None:
    if sys.platform != "win32":
        raise OSError("scheduled tasks are Windows-only")
    done = subprocess.run(["schtasks", "/query", "/tn", TASK_FOLDER + task_name, "/v", "/fo", "csv"],
                          capture_output=True, text=True, timeout=30, check=False)
    return done.stdout if done.returncode == 0 else None


def run_checks(config_path: Path, environ: Mapping[str, str], post: PostFn, task_query: TaskQueryFn,
               task_name: str, network: bool) -> list[Check]:
    env = dict(environ)
    try:
        load_dotenv(config_path.parent / ".env", env)
    except ConfigError as exc:
        return [Check("config", FAIL, str(exc), "fix the line in .env (KEY=VALUE)")]
    try:
        cfg = load_config(config_path, env)
    except ConfigError as exc:
        fix = ("run setup-apply (see docs/app-core.md)" if not config_path.is_file()
               else "fix the config as the message says")
        return [Check("config", FAIL, _scrub(str(exc), env), fix)]
    checks = [ok("config", f"{config_path} ({len(cfg.searches)} searches)")]
    checks.append(_profile(cfg))
    checks.extend(_telegram(cfg, env, post, network))
    checks.append(_webview(cfg))
    checks.append(_model(cfg))
    checks.append(_store(cfg))
    checks.append(_task(task_query, task_name))
    checks.append(_log_errors(config_path.parent / "logs" / "gigradar.log", env))
    return checks


def _scrub(text: str, env: Mapping[str, str]) -> str:
    secret = env.get("TELEGRAM_BOT_TOKEN")
    return text.replace(secret, "<bot-token>") if secret else text


def _profile(cfg: Config) -> Check:
    if cfg.profile is None:
        return Check("profile", WARN, "no [profile]: jobs are alerted without a score",
                     "add your skills in the setup to get scores")
    return ok("profile", f"{len(cfg.profile.sections)} skill areas, {len(cfg.profile.skills)} skills")


def _telegram(cfg: Config, env: Mapping[str, str], post: PostFn, network: bool) -> list[Check]:
    if "telegram" not in cfg.notify_channels:
        return [ok("telegram_token", "Telegram is not used"), ok("telegram_api", "Telegram is not used"),
                ok("telegram_chat", "Telegram is not used")]
    token, chat_id = cfg.telegram_bot_token, cfg.telegram_chat_id
    if not token:
        missing = Check("telegram_token", FAIL, "TELEGRAM_BOT_TOKEN is not set",
                        "run telegram-connect and the setup again")
        return [missing, Check("telegram_api", WARN, "skipped (no token)", None),
                Check("telegram_chat", WARN, "skipped (no token)", None)]
    checks = [ok("telegram_token", "bot token present")]
    if not network:
        checks.append(Check("telegram_api", WARN, "skipped (--offline)", None))
    else:
        try:
            checks.append(ok("telegram_api", f"bot @{get_me(token, post)} answers"))
        except ConnectError as exc:
            checks.append(Check("telegram_api", FAIL, _scrub(str(exc), env),
                                "check the bot token (BotFather) and the internet connection"))
    checks.append(ok("telegram_chat", "chat id present") if chat_id else
                  Check("telegram_chat", FAIL, "TELEGRAM_CHAT_ID is not set",
                        "run telegram-connect and the setup again"))
    return checks


def _webview(cfg: Config) -> Check:
    if cfg.backend != "webview":
        return ok("webview_profile", f"backend is {cfg.backend}")
    if cfg.webview_profile.is_dir() and any(cfg.webview_profile.iterdir()):
        return ok("webview_profile", str(cfg.webview_profile))
    return Check("webview_profile", WARN, f"no browser profile yet at {cfg.webview_profile}",
                 "run upwork-check once (it opens a window; solve the check if shown)")


def _model(cfg: Config) -> Check:
    if cfg.profile is None:
        return ok("model", "scoring is off")
    if cfg.scoring.model_dir.is_dir() and any(cfg.scoring.model_dir.rglob("*.onnx")):
        return ok("model", f"{cfg.scoring.model} cached in {cfg.scoring.model_dir}")
    return Check("model", FAIL, f"model {cfg.scoring.model} is not downloaded",
                 "run model-download (about 70 MB, once)")


def _store(cfg: Config) -> Check:
    if not cfg.db_path.is_file():
        return Check("store", WARN, f"no store yet at {cfg.db_path}",
                     "it is created by the first upwork-check or run-once")
    try:
        conn = open_existing(cfg.db_path, 5.0)
    except (StoreError, OSError) as exc:
        return Check("store", FAIL, str(exc), "run run-once to migrate it, or restore a backup")
    try:
        return ok("store", f"{seen_count(conn)} jobs seen")
    finally:
        conn.close()


def _task(task_query: TaskQueryFn, task_name: str) -> Check:
    fix = f"register it: gigradar-task.ps1 -Register -TaskName {task_name}"
    try:
        text = task_query(task_name)
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("scheduled_task", WARN, f"cannot query the task scheduler: {exc}", None)
    if text is None:
        return Check("scheduled_task", WARN, f"task {TASK_FOLDER}{task_name} is not registered", fix)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return Check("scheduled_task", WARN, "task list is empty", fix)
    row = rows[0]
    try:
        result = int(row.get("Last Result", ""))
    except ValueError:
        return Check("scheduled_task", WARN, f"registered; unreadable last result {row.get('Last Result')!r}", None)
    last = row.get("Last Run Time", "?")
    if result in (0, RUNNING):
        return ok("scheduled_task", f"registered, last run {last}, result {result}")
    if result == NOT_YET_RUN:
        return Check("scheduled_task", WARN, "registered, has not run yet", None)
    meaning = RESULT_MEANING.get(result, "failed")
    status = WARN if result in RESULT_MEANING else FAIL
    return Check("scheduled_task", status, f"last run {last}: result {result} ({meaning})", "see the log for the reason")


def _log_errors(log_file: Path, env: Mapping[str, str]) -> Check:
    if not log_file.is_file():
        return ok("recent_errors", "no log yet")
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL_LINES:]
    errors = [line for line in lines if ERROR_LINE.search(line)]
    if not errors:
        return ok("recent_errors", f"no ERROR in the last {len(lines)} log lines")
    return Check("recent_errors", WARN, f"{len(errors)} ERROR lines; latest: {_scrub(errors[-1], env)[:ERROR_CHARS]}",
                 f"read {log_file}")


def worst(checks: list[Check]) -> str:
    return FAIL if any(c.status == FAIL for c in checks) else WARN if any(c.status == WARN for c in checks) else OK
