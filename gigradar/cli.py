"""JSON command line for the desktop app (and for scripts): python -m gigradar.cli <command> [options]

stdin and stdout are UTF-8. Output is JSON Lines on stdout: zero or more {"event": ...} progress lines, then ONE result line
{"ok": true|false, "command": ..., ...}; a failure has "error": {"code", "message"}. Nothing else
goes to stdout (logs go to a file or stderr). The bot token is never printed or logged, and any
known secret is replaced by <redacted> in whatever is printed.

Exit codes: 0 ok · 1 the command failed · 2 usage error · run-once returns the watcher's code
(0 ok, 1 error, 2 config, 75 stopped early), so the scheduler's history keeps its meaning.

Commands: setup-apply, telegram-connect, upwork-check, run-once, jobs, label, doctor, model-download, notify-test, selftest.
`--config PATH` picks another gigradar.toml; the app home (GIGRADAR_HOME, else the per-user data
folder) holds config, .env, data/ and logs/. See docs/app-core.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from gigradar import appsetup, doctor, netutil, tgconnect, webview2
from gigradar.config import ConfigError, Config, app_home, load_config, load_dotenv
from gigradar.store import (StoreError, delete_label, filter_new, open_existing, open_store, recent_jobs, record_searches,
                            save_label, seen_count, stored_job)
from gigradar.telegram import PostFn, pay_line, urllib_post
from gigradar.search import SearchBlocked
from gigradar.tokens import StopRun

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
LOG_RELATIVE = Path("logs") / "gigradar.log"
BUSY_TIMEOUT = 10.0
HEAD_TIMEOUT_S = 10.0
PROGRESS_INTERVAL_S = 0.5
MODEL_EXTRAS_BYTES = 1_000_000
DEFAULT_TASK = "gigradar-watch"
CHECK_ATTEMPTS = 3          # upwork-check: a fresh browser profile can still be settling its first Cloudflare check
CHECK_PAUSES_S = (2.0, 5.0)  # short backoff before attempt 2 and 3
REDACTED = "<redacted>"


class CommandError(Exception):
    """A command failed in an expected way. `code` is machine-readable; the message holds no secret."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        raise UsageError(message)


@dataclass
class Context:
    """Everything a command touches from outside, so tests can fake it."""

    environ: dict[str, str]
    home: Path
    stdin: TextIO | None
    stdout: TextIO | None             # None under a windowless (pythonw-style) exe: output is dropped
    now: Callable[[], datetime]
    post: PostFn                      # Telegram transport
    sleep: Callable[[float], None]
    task_query: doctor.TaskQueryFn
    webview2_version: Callable[[], str | None]   # installed WebView2 runtime version, None when missing
    content_length: Callable[[str], int | None]  # size of a download (HEAD request), None when unknown
    secrets: list[str] = field(default_factory=list)   # values replaced by <redacted> in all output
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, payload: dict) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        for secret in self.secrets:
            text = text.replace(secret, REDACTED)
        if self.stdout is None:
            return
        with self._lock:
            self.stdout.write(text + "\n")
            self.stdout.flush()


def real_context() -> Context:
    for stream in (sys.stdin, sys.stdout):   # a pipe would otherwise use the ANSI code page: job text has 👍 · é ...
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    frozen = bool(getattr(sys, "frozen", False))
    environ = dict(os.environ)
    home = app_home(environ, frozen, Path(__file__).resolve().parent.parent)
    return Context(environ=environ, home=home, stdin=sys.stdin, stdout=sys.stdout,
                   now=lambda: datetime.now(timezone.utc), post=urllib_post, sleep=time.sleep,
                   task_query=doctor.schtasks_query,
                   webview2_version=lambda: webview2.installed_version(webview2.read_registry),
                   content_length=head_content_length)


def head_content_length(url: str) -> int | None:
    """Content-Length of a URL via HEAD (redirects followed), or None when the server does not say."""
    request = urllib.request.Request(url, method="HEAD")
    try:
        with netutil.urlopen(request, HEAD_TIMEOUT_S) as response:
            size = response.headers.get("Content-Length")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return int(size) if size and size.isdigit() else None


# ---- helpers -------------------------------------------------------------------------------------

def config_path(args: argparse.Namespace, ctx: Context) -> Path:
    return args.config if args.config else ctx.home / "gigradar.toml"


def load(args: argparse.Namespace, ctx: Context) -> tuple[Config, Path]:
    """The validated config plus its path; .env next to the config feeds the environment. Registers
    the bot token as a secret to redact."""
    path = config_path(args, ctx)
    env = dict(ctx.environ)
    try:
        load_dotenv(path.parent / ".env", env)
        cfg = load_config(path, env)
    except ConfigError as exc:
        raise CommandError("config", _redact(str(exc), ctx)) from None
    if cfg.telegram_bot_token:
        ctx.secrets.append(cfg.telegram_bot_token)
    return cfg, path


def _redact(text: str, ctx: Context) -> str:
    for secret in ctx.secrets:
        text = text.replace(secret, REDACTED)
    return text


def read_token(ctx: Context) -> str:
    line = ctx.stdin.readline() if ctx.stdin is not None else ""
    token = line.strip().removeprefix("﻿")
    if not token:
        raise CommandError("no_token", "send the bot token on stdin (one line)")
    ctx.secrets.append(token)
    return token


# ---- commands ------------------------------------------------------------------------------------

def cmd_setup_apply(args: argparse.Namespace, ctx: Context) -> dict:
    source = args.answers
    try:
        raw = (ctx.stdin.read() if ctx.stdin is not None else "") if source == "-" else Path(source).read_text(encoding="utf-8")
        raw = raw.removeprefix("﻿")   # Windows PowerShell 5.1 writes a BOM with -Encoding utf8
    except OSError as exc:
        raise CommandError("answers_unreadable", f"cannot read the answers: {exc}") from None
    try:
        answers = json.loads(raw)
    except ValueError as exc:
        raise CommandError("invalid_answers", f"the answers are not valid JSON: {exc}") from None
    if isinstance(answers, dict) and isinstance(answers.get("telegram"), dict):
        for key in ("bot_token", "chat_id"):
            value = answers["telegram"].get(key)
            if isinstance(value, str) and value:
                ctx.secrets.append(value)
    home = args.home if args.home else ctx.home
    try:
        return appsetup.apply(home, answers, args.force, ctx.now(), ctx.environ)
    except appsetup.SetupError as exc:
        raise CommandError(exc.code, _redact(str(exc), ctx)) from None


def cmd_telegram_connect(args: argparse.Namespace, ctx: Context) -> dict:
    token = read_token(ctx)
    try:
        found = tgconnect.connect(token, args.chat_id, ctx.post)
    except tgconnect.ConnectError as exc:
        raise CommandError(exc.code, _redact(str(exc), ctx)) from None
    return {"bot": found.bot_username, "chat_id": found.chat_id, "chat_name": found.chat_name, "test_message": True}


def cmd_upwork_check(args: argparse.Namespace, ctx: Context) -> dict:
    """One visible WebView2 window, ONE search (the first configured), seeded silently.

    Retries: a slow browser start (window_failed) opens a new window, a page that is not ready yet
    ("fetch() gave no result") retries in the same window; each at most CHECK_ATTEMPTS times."""
    cfg, _ = load(args, ctx)
    if cfg.backend != "webview":
        raise CommandError("not_applicable", f"upwork-check opens the browser window of the webview backend; backend is {cfg.backend}")
    if ctx.webview2_version() is None:       # before any window: pywebview would fall back to the IE engine
        raise CommandError("webview2_missing", webview2.missing_message())
    from gigradar.notify import LogNotifier
    from webview.errors import WebViewException

    from gigradar.watch import collect
    from gigradar.webview_search import WebViewSearcher

    spec = cfg.searches[0]
    ctx.emit({"event": "window", "message": "A window opens. If it shows a security check, solve it; it closes by itself."})
    searcher = WebViewSearcher(cfg.webview_profile, LogNotifier(), 1, visible=True)
    attempts = {"window": 0, "fetch": 0}

    def work(search):
        # Same page, same window: the first fetch can land while Cloudflare's check is still navigating.
        for attempt in range(1, CHECK_ATTEMPTS + 1):
            attempts["fetch"] += 1
            found, stopped = collect(search, [spec])
            if stopped is None or not isinstance(stopped, SearchBlocked) or attempt == CHECK_ATTEMPTS:
                return found, stopped
            ctx.emit({"event": "retry", "kind": "fetch", "attempt": attempt + 1,
                      "message": "the page is not ready yet; trying again"})
            ctx.sleep(CHECK_PAUSES_S[attempt - 1])

    for window_try in range(1, CHECK_ATTEMPTS + 1):
        attempts["window"] = window_try
        try:
            results, stopped = searcher.run(work)
            break
        except webview2.WebView2Missing as exc:
            raise CommandError("webview2_missing", str(exc)) from None
        except StopRun as exc:
            raise CommandError("blocked", _redact(str(exc), ctx)) from None
        except WebViewException as exc:   # WebView2 did not come up within pywebview's 20 s (a cold, busy PC)
            if window_try == CHECK_ATTEMPTS:
                raise CommandError("window_failed", f"the browser component was slow to start ({exc}); "
                                   f"tried {window_try} times, run the check again") from None
            ctx.emit({"event": "retry", "kind": "window", "attempt": window_try + 1,
                      "message": "the browser component was slow to start; opening the window again"})
            ctx.sleep(CHECK_PAUSES_S[window_try - 1])
    if stopped is not None:
        raise CommandError("blocked", _redact(str(stopped), ctx))
    [(_, jobs)] = results
    now = ctx.now()
    conn = open_store(cfg.db_path, [], now)
    try:
        new = filter_new(conn, jobs)
        record_searches(conn, [(spec.name, jobs)], new, now)   # seeded silently: no alert, no score
        total = seen_count(conn)
    finally:
        conn.close()
    return {"search": spec.name, "jobs_found": len(jobs), "seeded": len(new), "jobs_seen_total": total,
            "attempts": attempts}


def cmd_run_once(args: argparse.Namespace, ctx: Context) -> dict:
    cfg, path = load(args, ctx)
    from gigradar import watch

    log_file = args.log_file if args.log_file else path.parent / LOG_RELATIVE
    before = _seen_total(cfg.db_path)
    code = watch.main(["--config", str(path), "--log-file", str(log_file)])
    after = _seen_total(cfg.db_path)
    meaning = {watch.EXIT_OK: "ok", watch.EXIT_ERROR: "error", watch.EXIT_CONFIG: "config error",
               watch.EXIT_STOPPED: "stopped early; retried at the next run"}.get(code, "unknown")
    result = {"exit_code": code, "meaning": meaning, "jobs_seen_before": before, "jobs_seen_after": after,
              "log_file": str(log_file)}
    if code != watch.EXIT_OK:
        result["_exit"] = code
        result["_fail"] = True
    return result


def _seen_total(db_path: Path) -> int | None:
    try:
        conn = open_existing(db_path, BUSY_TIMEOUT)
    except StoreError:
        return None
    try:
        return seen_count(conn)
    finally:
        conn.close()


def cmd_jobs(args: argparse.Namespace, ctx: Context) -> dict:
    cfg, _ = load(args, ctx)
    try:
        conn = open_existing(cfg.db_path, BUSY_TIMEOUT)
    except StoreError as exc:
        raise CommandError("store", str(exc)) from None
    try:
        rows = recent_jobs(conn, args.limit)
    finally:
        conn.close()
    return {"jobs": [{"id": r.job_id, "title": r.job.title, "url": r.job.url, "pay": pay_line(r.job),
                      "skills": r.job.skills, "published": r.job.published, "first_seen": r.first_seen,
                      "score": r.score, "reason": r.reason, "label": r.label} for r in rows]}


def cmd_label(args: argparse.Namespace, ctx: Context) -> dict:
    cfg, _ = load(args, ctx)
    try:
        conn = open_existing(cfg.db_path, BUSY_TIMEOUT)
    except StoreError as exc:
        raise CommandError("store", str(exc)) from None
    try:
        if stored_job(conn, args.job_id) is None:
            raise CommandError("not_found", f"no stored job {args.job_id}")
        if args.value == "clear":
            delete_label(conn, args.job_id)
        else:
            save_label(conn, args.job_id, 1 if args.value == "up" else -1, ctx.now())
    finally:
        conn.close()
    return {"job_id": args.job_id, "label": args.value}


def cmd_doctor(args: argparse.Namespace, ctx: Context) -> dict:
    path = config_path(args, ctx)
    env = dict(ctx.environ)
    try:
        load_dotenv(path.parent / ".env", env)    # only to learn the token for redaction
    except ConfigError:
        pass
    if env.get("TELEGRAM_BOT_TOKEN"):
        ctx.secrets.append(env["TELEGRAM_BOT_TOKEN"])
    checks = doctor.run_checks(path, ctx.environ, ctx.post, ctx.task_query, args.task_name, not args.offline,
                                 ctx.webview2_version)
    status = doctor.worst(checks)
    result = {"status": status, "checks": [asdict(c) for c in checks]}
    if status == doctor.FAIL:
        result["_fail"] = True
    return result


def cmd_model_download(args: argparse.Namespace, ctx: Context) -> dict:
    cfg, _ = load(args, ctx)
    from gigradar.embed import EmbedderError, FastEmbedder, model_file_url

    s = cfg.scoring
    url = model_file_url(s.model)
    size = ctx.content_length(url) if url else None
    total = size + MODEL_EXTRAS_BYTES if size else None     # the model file plus the small tokenizer/config files
    ctx.emit({"event": "start", "model": s.model, "dir": str(s.model_dir), "total_bytes": total})
    stop = threading.Event()

    def report() -> None:
        while not stop.wait(PROGRESS_INTERVAL_S):
            done = _dir_bytes(s.model_dir)
            ctx.emit({"event": "progress", "bytes": done, "total_bytes": total,
                      "percent": min(99, round(100 * done / total)) if total else None})

    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()
    started = time.monotonic()
    try:
        FastEmbedder(s.model, s.model_dir, offline=False).embed(["warm-up"])
        ctx.emit({"event": "verify", "message": "downloaded; checking it loads offline"})
        dim = len(FastEmbedder(s.model, s.model_dir, offline=True).embed(["offline check"])[0])
    except EmbedderError as exc:
        raise CommandError("model", _redact(str(exc), ctx)) from None
    finally:
        stop.set()
        reporter.join()
    return {"model": s.model, "dir": str(s.model_dir), "bytes": _dir_bytes(s.model_dir), "dim": dim,
            "seconds": round(time.monotonic() - started, 1)}


def cmd_notify_test(args: argparse.Namespace, ctx: Context) -> dict:
    """One sample job through every configured channel (toast, Telegram): the app's "send a test" button."""
    cfg, _ = load(args, ctx)
    from gigradar.embed import preload_runtime
    from gigradar.notify import build_notifier
    from gigradar.telegram import sample_alert

    if cfg.profile is not None:
        preload_runtime()   # before the toast notifier loads winrt: see its docstring
    try:
        build_notifier(cfg).notify_jobs([sample_alert()])
    except (ConfigError, RuntimeError) as exc:
        raise CommandError("notify_failed", _redact(str(exc), ctx)) from None
    return {"channels": ["log", *cfg.notify_channels]}


def cmd_selftest(args: argparse.Namespace, ctx: Context) -> dict:
    """Checks the native-library load order that scoring depends on (see embed.preload_runtime). Meant for
    a packaged build: run it once per order; a crash (exit code != 0) is the answer, there is no JSON then.
    `preload` is the order gigradar really uses and must always pass."""
    import importlib

    order = args.order
    if order == "preload":
        from gigradar.embed import preload_runtime
        preload_runtime()
        importlib.import_module("windows_toasts")
    elif order == "toasts-first":
        importlib.import_module("windows_toasts")
        importlib.import_module("onnxruntime")
    else:
        importlib.import_module("onnxruntime")
        importlib.import_module("windows_toasts")
    onnxruntime = importlib.import_module("onnxruntime")
    result = {"order": order, "frozen": bool(getattr(sys, "frozen", False)), "onnxruntime": onnxruntime.__version__}
    if args.model_dir:
        from gigradar.config import DEFAULT_MODEL
        from gigradar.embed import FastEmbedder
        result["embedding_dim"] = len(FastEmbedder(DEFAULT_MODEL, args.model_dir, offline=True).embed(["selftest"])[0])
    return result


def _dir_bytes(path: Path) -> int:
    """Bytes on disk. The Hugging Face cache keeps every file in blobs/ and links or copies it into
    snapshots/ (a full second copy without symlinks), so count blobs/ only when it exists."""
    if not path.is_dir():
        return 0
    blobs = [b for b in path.rglob("blobs") if b.is_dir()]
    roots = blobs if blobs else [path]
    return sum(f.stat().st_size for root in roots for f in root.rglob("*") if f.is_file())


# ---- plumbing ------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m gigradar.cli", description=__doc__.split("\n")[0])
    common = _Parser(add_help=False)
    common.add_argument("--json", action="store_true", help="accepted for clarity: output is always JSON lines")
    common.add_argument("--config", type=Path, default=None, help="gigradar.toml path (default: <app home>/gigradar.toml)")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    p = sub.add_parser("setup-apply", parents=[common], help="write config files from a JSON answers file")
    p.add_argument("--answers", required=True, help="answers JSON file, or - for stdin")
    p.add_argument("--home", type=Path, default=None, help="write here instead of the app home")
    p.add_argument("--force", action="store_true", help="replace existing files (backed up first)")
    p.set_defaults(run=cmd_setup_apply)

    p = sub.add_parser("telegram-connect", parents=[common], help="verify a bot, find the chat id, send a test message")
    p.add_argument("--chat-id", default=None, help="skip chat discovery and use this chat id")
    p.set_defaults(run=cmd_telegram_connect)   # the bot token is read from stdin

    sub.add_parser("upwork-check", parents=[common], help="open the Upwork window once, run one search, seed silently"
                   ).set_defaults(run=cmd_upwork_check)

    p = sub.add_parser("run-once", parents=[common], help="one watch run (what the scheduled task does)")
    p.add_argument("--log-file", type=Path, default=None, help="log file (default: <app home>/logs/gigradar.log)")
    p.set_defaults(run=cmd_run_once)

    p = sub.add_parser("jobs", parents=[common], help="latest stored jobs with their scores")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(run=cmd_jobs)

    p = sub.add_parser("label", parents=[common], help="mark a job 👍 (up), 👎 (down) or clear the label")
    p.add_argument("--job-id", required=True)
    p.add_argument("--value", required=True, choices=("up", "down", "clear"))
    p.set_defaults(run=cmd_label)

    p = sub.add_parser("doctor", parents=[common], help="check that everything works")
    p.add_argument("--offline", action="store_true", help="skip checks that need the internet")
    p.add_argument("--task-name", default=DEFAULT_TASK, help="scheduled task to inspect")
    p.set_defaults(run=cmd_doctor)

    sub.add_parser("notify-test", parents=[common], help="send one sample job to every configured channel"
                   ).set_defaults(run=cmd_notify_test)

    p = sub.add_parser("selftest", parents=[common], help="check the native-library load order (for packaged builds)")
    p.add_argument("--order", required=True, choices=("preload", "toasts-first", "onnx-first"))
    p.add_argument("--model-dir", type=Path, default=None, help="also load the cached model from here and embed one text")
    p.set_defaults(run=cmd_selftest)

    sub.add_parser("model-download", parents=[common], help="download the scoring model (progress as JSON lines)"
                   ).set_defaults(run=cmd_model_download)
    return parser


def run(argv: Sequence[str], ctx: Context) -> int:
    try:
        args = build_parser().parse_args(argv)
    except UsageError as exc:
        ctx.emit({"ok": False, "command": None, "error": {"code": "usage", "message": str(exc)}})
        return EXIT_USAGE
    name = args.command
    try:
        result = args.run(args, ctx)
    except CommandError as exc:
        ctx.emit({"ok": False, "command": name, "error": {"code": exc.code, "message": _redact(str(exc), ctx)}})
        return EXIT_FAIL
    except Exception as exc:  # noqa: BLE001  the app needs a JSON answer even for a bug; type + message, no traceback
        ctx.emit({"ok": False, "command": name,
                  "error": {"code": "unexpected", "message": _redact(f"{type(exc).__name__}: {exc}", ctx)}})
        return EXIT_FAIL
    failed, code = result.pop("_fail", False), result.pop("_exit", None)
    ctx.emit({"ok": not failed, "command": name, **result})
    if code is not None:
        return code
    return EXIT_FAIL if failed else EXIT_OK


def main(argv: Sequence[str]) -> int:
    return run(argv, real_context())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
