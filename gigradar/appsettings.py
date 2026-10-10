"""settings-get / settings-save: read the app's configuration for the Settings screen and change ONE section of it.

The sections are the same questions the first-start setup asks: profile, preferences, searches, notifications,
schedule. `read_settings` turns gigradar.toml, profile.md and .env into plain values (never the bot token).
`save_section` replaces one section of the current answers and writes everything back through `appsetup.apply`, so a
change is validated like the setup validates it, an invalid one changes nothing, and the replaced files are backed up.

Only a configuration the app wrote can be edited here. A hand-edited file with other settings is refused
(`unsupported_config`) instead of being rewritten without them.
"""

from __future__ import annotations

import re
import tomllib
from datetime import datetime
from pathlib import Path

from gigradar import appsetup
from gigradar.config import ConfigError, load_dotenv

SECTIONS = ("profile", "preferences", "searches", "notifications", "schedule", "storage")
INTERVALS = (30, 60, 120, 240)          # minutes between checks; never below 30
JOB_TYPES = ("hourly", "fixed")         # no job type = both
KNOWN_TABLES = {"search", "notify", "profile", "scoring", "schedule", "searches", "retention"}
KEEP_DAYS = (7, 14, 30, 0)              # a week, two weeks, a month, forever (0)
PREFERENCE_KEYS = ("skills", "constraints", "min_hourly", "min_fixed")
MIN_SCORE_DEFAULT = 20   # what the screen shows for a config without the key (the checks then use no minimum)
MIN_SCORE_MAX = 80
CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SettingsError(Exception):
    """The settings cannot be read or the change is invalid. `code` is machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def current_answers(home: Path) -> tuple[dict, dict[str, str]]:
    """The configuration in `setup-apply` answers form, plus the values of .env (the bot token among them)."""
    toml_path = home / "gigradar.toml"
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SettingsError("config", f"{toml_path} not found: run the setup first") from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SettingsError("config", f"{toml_path.name}: {exc}") from None
    env: dict[str, str] = {}
    try:
        load_dotenv(home / ".env", env)
    except ConfigError as exc:
        raise SettingsError("config", str(exc)) from None
    _refuse_foreign_settings(data)

    answers: dict = {"searches": [dict(entry) for entry in data.get("searches", [])],
                     "notify": {"channels": list(data.get("notify", {}).get("channels", []))}}
    if "profile" in data:
        profile = {k: v for k, v in data["profile"].items() if k != "path"}
        try:
            profile["markdown"] = (home / data["profile"].get("path", "profile.md")).read_text(encoding="utf-8")
        except OSError as exc:
            raise SettingsError("config", f"profile.md: {exc}") from None
        answers["profile"] = profile
    if data.get("scoring", {}).get("min_score") is not None:
        answers["scoring"] = {"min_score": data["scoring"]["min_score"]}
    if "schedule" in data:
        answers["schedule"] = dict(data["schedule"])
    if "retention" in data:
        answers["retention"] = dict(data["retention"])
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        answers["telegram"] = {"bot_token": env["TELEGRAM_BOT_TOKEN"], "chat_id": env["TELEGRAM_CHAT_ID"]}
        if env.get("TELEGRAM_BOT_USERNAME"):
            answers["telegram"]["bot_username"] = env["TELEGRAM_BOT_USERNAME"]
    return answers, env


def _refuse_foreign_settings(data: dict) -> None:
    """The setup writes a fixed set of keys; anything else was added by hand and would be lost on a rewrite."""
    allowed = {
        "search": {"webview_profile"}, "notify": {"channels"}, "scoring": {"model_dir", "min_score"},
        "profile": appsetup.PROFILE_KEYS - {"markdown"} | {"path"}, "schedule": appsetup.SCHEDULE_KEYS,
        "retention": appsetup.RETENTION_KEYS,
    }
    foreign = sorted(set(data) - KNOWN_TABLES)
    for table, keys in allowed.items():
        if isinstance(data.get(table), dict):
            foreign += [f"{table}.{key}" for key in sorted(set(data[table]) - keys)]
    if data.get("search", {}).get("webview_profile", "webview2") != "webview2":
        foreign.append("search.webview_profile")
    if data.get("profile", {}).get("path", "profile.md") != "profile.md":
        foreign.append("profile.path")
    if data.get("scoring", {}).get("model_dir", "models") != "models":
        foreign.append("scoring.model_dir")
    if foreign:
        raise SettingsError("unsupported_config", "gigradar.toml has settings this app cannot edit ("
                            + ", ".join(foreign) + "). Edit the file by hand, or run the setup again.")


def read_settings(home: Path) -> dict:
    """What the Settings screen shows. Never contains the bot token."""
    answers, env = current_answers(home)
    profile = answers.get("profile", {})
    channels = answers["notify"]["channels"]
    schedule = answers.get("schedule", {})
    return {
        "profile": {"markdown": profile.get("markdown", "")},
        "preferences": {"skills": profile.get("skills", []), "constraints": profile.get("constraints", ""),
                        "min_hourly": profile.get("min_hourly"), "min_fixed": profile.get("min_fixed"),
                        "min_score": answers.get("scoring", {}).get("min_score", MIN_SCORE_DEFAULT)},
        "searches": [{"name": s["name"], "query": s.get("query", ""), "job_type": s.get("job_type")}
                     for s in answers["searches"]],
        "notifications": {"toast": "toast" in channels,
                          "telegram": {"enabled": "telegram" in channels, "connected": "telegram" in answers,
                                       "bot": env.get("TELEGRAM_BOT_USERNAME") or None}},
        "schedule": {"interval_minutes": schedule.get("interval_minutes", 30),
                     "quiet_start": schedule.get("quiet_start"), "quiet_end": schedule.get("quiet_end")},
        # keep_days None = the file has no [retention] (nothing is ever deleted); the screen shows that as "forever"
        "storage": {"keep_days": answers["retention"].get("keep_days") if "retention" in answers else None,
                    "keep_rated": answers.get("retention", {}).get("keep_rated", True)},
    }


def save_section(home: Path, section: str, payload: object, now: datetime, environ: dict[str, str]) -> dict:
    """Replace one section and write the configuration. Returns what `appsetup.apply` returns."""
    if section not in SECTIONS:
        raise SettingsError("invalid_section", f"section must be one of {', '.join(SECTIONS)}")
    if not isinstance(payload, dict):
        raise SettingsError("invalid_settings", "the settings must be a JSON object")
    answers, env = current_answers(home)
    {"profile": _profile, "preferences": _preferences, "searches": _searches,
     "notifications": _notifications, "schedule": _schedule, "storage": _storage}[section](answers, payload, env)
    try:
        return appsetup.apply(home, answers, True, True, now, environ)
    except appsetup.SetupError as exc:
        raise SettingsError(exc.code, str(exc)) from None


def _profile(answers: dict, payload: dict, env: dict[str, str]) -> None:
    markdown = payload.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        raise SettingsError("invalid_settings", "the profile must be text")
    answers.setdefault("profile", {})["markdown"] = markdown


def _preferences(answers: dict, payload: dict, env: dict[str, str]) -> None:
    unknown = set(payload) - set(PREFERENCE_KEYS) - {"min_score"}
    if unknown:
        raise SettingsError("invalid_settings", f"unknown preference keys {sorted(unknown)}")
    skills = payload.get("skills", [])
    if not isinstance(skills, list) or any(not isinstance(s, str) or not s.strip() for s in skills):
        raise SettingsError("invalid_settings", "skills must be a list of words")
    constraints = payload.get("constraints") or ""
    if not isinstance(constraints, str):
        raise SettingsError("invalid_settings", "constraints must be text")
    profile = answers.setdefault("profile", {})
    for key in PREFERENCE_KEYS:
        profile.pop(key, None)
    if skills:
        profile["skills"] = [s.strip() for s in skills]
    if constraints.strip():
        profile["constraints"] = constraints.strip()
    for key in ("min_hourly", "min_fixed"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise SettingsError("invalid_settings", f"{key} must be a number of at least 0, or empty")
        profile[key] = value
    if "min_score" in payload:
        score = payload["min_score"]
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= MIN_SCORE_MAX:
            raise SettingsError("invalid_settings", f"min_score must be a whole number from 0 to {MIN_SCORE_MAX}")
        answers["scoring"] = {"min_score": score}


def _searches(answers: dict, payload: dict, env: dict[str, str]) -> None:
    entries = payload.get("searches")
    if not isinstance(entries, list) or not 1 <= len(entries) <= appsetup.MAX_SEARCHES:
        raise SettingsError("invalid_settings", f"give 1 to {appsetup.MAX_SEARCHES} searches")
    before = {s["name"]: s for s in answers["searches"]}
    used: set[str] = set()
    result = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("query"), str) or not entry["query"].strip():
            raise SettingsError("invalid_settings", "every search needs keywords")
        job_type = entry.get("job_type")
        if job_type is not None and job_type not in JOB_TYPES:
            raise SettingsError("invalid_settings", f"job_type must be one of {JOB_TYPES}, or empty for both")
        name = entry.get("name") if isinstance(entry.get("name"), str) and entry["name"] else _name(entry["query"], job_type, used)
        if name in used:
            raise SettingsError("invalid_settings", f"two searches are called {name}")
        used.add(name)
        # keep the settings of an existing search that the screen does not show (they were put there by hand)
        kept = {k: v for k, v in before.get(name, {}).items() if k not in ("query", "job_type")}
        merged = {**kept, "name": name, "query": entry["query"].strip()}
        if job_type is not None:
            merged["job_type"] = job_type
        result.append(merged)
    answers["searches"] = result


def _name(query: str, job_type: str | None, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:30].strip("-") or "search"
    base = f"{base}-{job_type or 'both'}"
    name, n = base, 2
    while name in used:
        name, n = f"{base}-{n}", n + 1
    return name


def _notifications(answers: dict, payload: dict, env: dict[str, str]) -> None:
    telegram = payload.get("telegram") or {}
    if not isinstance(telegram, dict) or set(telegram) - {"enabled", "bot_token", "chat_id", "bot_username"}:
        raise SettingsError("invalid_settings", "unexpected telegram settings")
    channels = (["toast"] if payload.get("toast") else []) + (["telegram"] if telegram.get("enabled") else [])
    if not channels:
        raise SettingsError("no_channel", "turn on at least one way to be notified")
    if telegram.get("enabled"):
        known = answers.get("telegram", {})
        token = telegram.get("bot_token") or known.get("bot_token")
        chat_id = telegram.get("chat_id") or known.get("chat_id")
        if not token or not chat_id:
            raise SettingsError("telegram_not_connected", "connect a Telegram bot first")
        username = telegram.get("bot_username") if telegram.get("bot_token") else known.get("bot_username")
        answers["telegram"] = {"bot_token": token, "chat_id": str(chat_id)}
        if username:
            answers["telegram"]["bot_username"] = username
    answers["notify"] = {"channels": channels}


def _storage(answers: dict, payload: dict, env: dict[str, str]) -> None:
    unknown = set(payload) - appsetup.RETENTION_KEYS
    if unknown:
        raise SettingsError("invalid_settings", f"unknown storage keys {sorted(unknown)}")
    keep_days, keep_rated = payload.get("keep_days"), payload.get("keep_rated")
    if isinstance(keep_days, bool) or keep_days not in KEEP_DAYS:
        raise SettingsError("invalid_settings", f"keep_days must be one of {KEEP_DAYS} (0 = forever)")
    if not isinstance(keep_rated, bool):
        raise SettingsError("invalid_settings", "keep_rated must be true or false")
    answers["retention"] = {"keep_days": keep_days, "keep_rated": keep_rated}


def _schedule(answers: dict, payload: dict, env: dict[str, str]) -> None:
    unknown = set(payload) - appsetup.SCHEDULE_KEYS
    if unknown:
        raise SettingsError("invalid_settings", f"unknown schedule keys {sorted(unknown)}")
    interval = payload.get("interval_minutes")
    if isinstance(interval, bool) or interval not in INTERVALS:
        raise SettingsError("invalid_settings", f"interval_minutes must be one of {INTERVALS}")
    start, end = payload.get("quiet_start"), payload.get("quiet_end")
    if (start is None) != (end is None):
        raise SettingsError("invalid_settings", "give both the start and the end of the quiet hours, or neither")
    if start is not None:
        if not (isinstance(start, str) and isinstance(end, str) and CLOCK.match(start) and CLOCK.match(end)):
            raise SettingsError("invalid_settings", "quiet hours must look like 22:00")
        if start == end:
            raise SettingsError("invalid_settings", "the quiet hours must start and end at different times")
    schedule = {"interval_minutes": interval}
    if start is not None:
        schedule.update(quiet_start=start, quiet_end=end)
    answers["schedule"] = schedule
