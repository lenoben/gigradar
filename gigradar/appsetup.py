"""setup-apply: turn a JSON answers file into gigradar.toml, .env and profile.md in the app home.

Answers (all keys optional except `searches`):
    {"searches": [{"name": "...", "query": "...", "job_type": "hourly", ...}],
     "notify": {"channels": ["toast", "telegram"]},
     "profile": {"markdown": "## Area\ntext", "skills": [...], "min_hourly": 50, "min_fixed": 1000,
                 "tiers": [...], "exclude_keywords": [...], "constraints": "..."},
     "scoring": {"min_score": 20},
     "schedule": {"interval_minutes": 30, "quiet_start": "22:00", "quiet_end": "07:00"},
     "retention": {"keep_days": 30, "keep_rated": true},
     "telegram": {"bot_token": "...", "chat_id": "...", "bot_username": "my_bot"}}

Everything is written to a staging folder and validated with the real config loader first; only a
valid set replaces files. Existing files are never overwritten without `force`, and are then copied
to backups/<UTC time>/ first. The bot token goes to .env only and is never echoed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from gigradar.config import ConfigError, load_config

TOP_KEYS = {"searches", "notify", "profile", "scoring", "telegram", "schedule", "retention"}
PROFILE_KEYS = {"markdown", "skills", "min_hourly", "min_fixed", "tiers", "exclude_keywords", "constraints"}
SCORING_KEYS = {"min_score"}
SCHEDULE_KEYS = {"interval_minutes", "quiet_start", "quiet_end"}
RETENTION_KEYS = {"keep_days", "keep_rated"}
TELEGRAM_KEYS = {"bot_token", "chat_id", "bot_username"}
MAX_SEARCHES = 5   # every search is one request to Upwork per check
SEARCH_KEY = re.compile(r"^[a-z_]+$")
TOKEN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
CHAT_ID = re.compile(r"^-?\d{1,20}$")
BOT_USERNAME = re.compile(r"^[A-Za-z0-9_]{3,64}$")
FILES = ("gigradar.toml", ".env", "profile.md")


class SetupError(Exception):
    """The answers are invalid, or writing would overwrite files. `code` is machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\u007f")
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    raise SetupError("invalid_answers", f"cannot write {type(value).__name__} to the config")


def _table(answers: dict, key: str, allowed: set[str]) -> dict:
    value = answers.get(key, {})
    if not isinstance(value, dict):
        raise SetupError("invalid_answers", f"{key} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise SetupError("invalid_answers", f"{key}: unknown keys {sorted(unknown)}")
    return value


def render_toml(answers: dict) -> str:
    """gigradar.toml text for the answers. Values are validated afterwards by load_config."""
    # The browser profile and the model live next to this file, so a scheduled run (which has no GIGRADAR_HOME in its
    # environment) finds the same ones as the app that wrote the config, wherever the app home is.
    lines = ["# Written by gigradar setup. Secrets are in .env, never here.", "",
             "[search]", 'webview_profile = "webview2"', "",
             "[notify]", f"channels = {toml_value(_channels(answers))}", ""]
    profile = _table(answers, "profile", PROFILE_KEYS)
    if profile:
        lines.append("[profile]")
        lines.append('path = "profile.md"')
        for key in sorted(PROFILE_KEYS - {"markdown"}):
            if profile.get(key) is not None:
                lines.append(f"{key} = {toml_value(profile[key])}")
        lines.append("")
    scoring = _table(answers, "scoring", SCORING_KEYS)
    if scoring or profile:
        lines.append("[scoring]")
        lines.append('model_dir = "models"')
        lines.extend(f"{key} = {toml_value(scoring[key])}" for key in sorted(scoring))
        lines.append("")
    schedule = _table(answers, "schedule", SCHEDULE_KEYS)
    if schedule:
        lines.append("[schedule]")
        lines.extend(f"{key} = {toml_value(schedule[key])}" for key in sorted(schedule) if schedule[key] is not None)
        lines.append("")
    retention = _table(answers, "retention", RETENTION_KEYS)
    if retention:
        lines.append("[retention]")
        lines.extend(f"{key} = {toml_value(retention[key])}" for key in sorted(retention))
        lines.append("")
    searches = answers.get("searches")
    if not isinstance(searches, list) or not searches:
        raise SetupError("invalid_answers", "searches must be a non-empty list")
    if len(searches) > MAX_SEARCHES:
        raise SetupError("invalid_answers", f"at most {MAX_SEARCHES} searches (each one is a request to Upwork per check)")
    for entry in searches:
        if not isinstance(entry, dict):
            raise SetupError("invalid_answers", "each search must be an object")
        lines.append("[[searches]]")
        for key, value in entry.items():
            if not SEARCH_KEY.match(key):
                raise SetupError("invalid_answers", f"bad search key {key!r}")
            lines.append(f"{key} = {toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def _channels(answers: dict) -> list:
    notify = _table(answers, "notify", {"channels"})
    channels = notify.get("channels", ["toast"])
    if not isinstance(channels, list):
        raise SetupError("invalid_answers", "notify.channels must be a list")
    return channels


def render_env(answers: dict) -> str | None:
    telegram = _table(answers, "telegram", TELEGRAM_KEYS)
    if not telegram:
        return None
    token, chat_id = str(telegram.get("bot_token", "")), str(telegram.get("chat_id", ""))
    if not TOKEN.match(token):
        raise SetupError("invalid_answers", "telegram.bot_token does not look like a bot token")
    if not CHAT_ID.match(chat_id):
        raise SetupError("invalid_answers", "telegram.chat_id must be a number")
    username = str(telegram.get("bot_username") or "")
    if username and not BOT_USERNAME.match(username):
        raise SetupError("invalid_answers", "telegram.bot_username does not look like a bot name")
    text = f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID={chat_id}\n"
    return text + (f"TELEGRAM_BOT_USERNAME={username}\n" if username else "")


def apply(home: Path, answers: object, force: bool, backup: bool, now: datetime, environ: dict[str, str]) -> dict:
    """Write config files into `home`. Returns {"written": [...], "backup": dir or None}.
    `backup=False` skips copying replaced files (for a caller that wrote them itself a moment ago)."""
    if not isinstance(answers, dict):
        raise SetupError("invalid_answers", "the answers must be a JSON object")
    unknown = set(answers) - TOP_KEYS
    if unknown:
        raise SetupError("invalid_answers", f"unknown keys {sorted(unknown)}")
    texts = {"gigradar.toml": render_toml(answers), ".env": render_env(answers)}
    markdown = _table(answers, "profile", PROFILE_KEYS).get("markdown")
    if markdown is not None:
        if not isinstance(markdown, str):
            raise SetupError("invalid_answers", "profile.markdown must be text")
        texts["profile.md"] = markdown
    texts = {name: text for name, text in texts.items() if text is not None}

    home.mkdir(parents=True, exist_ok=True)
    existing = [name for name in texts if (home / name).exists()]
    if existing and not force:
        raise SetupError("exists", f"{', '.join(existing)} already exist in {home}; use --force to replace them "
                                   "(they are backed up first)")
    stage = Path(tempfile.mkdtemp(prefix=".setup-", dir=home))
    try:
        for name, text in texts.items():
            (stage / name).write_text(text, encoding="utf-8", newline="\n")
        staged_env = dict(environ)
        if ".env" in texts:
            from gigradar.config import load_dotenv
            staged_env.pop("TELEGRAM_BOT_TOKEN", None)
            staged_env.pop("TELEGRAM_CHAT_ID", None)
            load_dotenv(stage / ".env", staged_env)
        try:
            load_config(stage / "gigradar.toml", staged_env)
        except ConfigError as exc:
            raise SetupError("invalid_config", str(exc)) from None
        backup_dir = None
        if existing and backup:
            backup_dir = home / "backups" / f"{now:%Y%m%dT%H%M%SZ}"
            for n in range(2, 100):      # two saves in the same second: never overwrite an earlier backup
                if not backup_dir.exists():
                    break
                backup_dir = home / "backups" / f"{now:%Y%m%dT%H%M%SZ}-{n}"
            else:
                raise SetupError("exists", "too many backups in the same second; try again")
            backup_dir.mkdir(parents=True)
            for name in existing:
                shutil.copy2(home / name, backup_dir / name)
        for name in texts:
            os.replace(stage / name, home / name)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return {"written": sorted(texts), "backup": str(backup_dir) if backup_dir else None}
