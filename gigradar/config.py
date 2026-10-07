"""Load gigradar.toml (non-secret settings) and .env (secrets) into a validated Config.

Secrets — Telegram bot token/chat id, proxy URL — come ONLY from the environment
(optionally loaded from a .env file); a TOML that contains them is rejected.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

from upwork_search import CLIENT_HIRES, DURATIONS, JOB_TYPES, PAGE_MAX, TIERS, WORKLOADS, SearchFilters

TOKEN_SOURCES = ("manual", "fetch")
BACKENDS = ("webview", "curl")
NOTIFY_CHANNELS = ("toast", "telegram")
MAX_LIMIT = PAGE_MAX  # one page = exactly one request per search per run
SECRET_KEYS = {"bot_token", "telegram_bot_token", "chat_id", "telegram_chat_id", "proxy", "proxy_url", "token"}

_FILTER_KEYS = {
    "query", "job_type", "tier", "workload", "duration", "contract_to_hire",
    "client_hires", "hourly_rate", "fixed_budget", "location",
}
_CHOICES = {
    "job_type": JOB_TYPES, "tier": tuple(TIERS), "workload": WORKLOADS,
    "duration": DURATIONS, "client_hires": CLIENT_HIRES,
}


class ConfigError(Exception):
    """gigradar.toml or .env is missing, malformed or invalid."""


@dataclass(frozen=True)
class SearchSpec:
    name: str
    filters: SearchFilters
    limit: int


@dataclass(frozen=True)
class Config:
    db_path: Path
    searches: list[SearchSpec]
    backend: str                    # "webview" (default) | "curl"
    webview_profile: Path
    notify_channels: list[str]
    token_sources: list[str]
    use_proxy: bool
    proxy: str | None               # env UPWORK_PROXY
    telegram_bot_token: str | None  # env TELEGRAM_BOT_TOKEN
    telegram_chat_id: str | None    # env TELEGRAM_CHAT_ID


def load_dotenv(path: Path, environ: MutableMapping[str, str]) -> None:
    """Minimal KEY=VALUE loader. Missing file is fine; real env vars win over the file."""
    if not path.is_file():
        return
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            raise ConfigError(f"{path.name}:{lineno}: expected KEY=VALUE")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        environ.setdefault(key, value)


def load_config(toml_path: Path, environ: Mapping[str, str]) -> Config:
    """Parse and validate `toml_path`; relative paths resolve against its directory."""
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{toml_path} not found (copy gigradar.example.toml)") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{toml_path.name}: {exc}") from exc
    _reject_secrets(data, "")

    store = _table(data, "store")
    db_path = Path(_get(store, "path", str, "data/gigradar.db", "store"))
    if not db_path.is_absolute():
        db_path = toml_path.parent / db_path

    defaults = _table(data, "defaults")
    default_limit = _get(defaults, "limit", int, 30, "defaults")

    raw_searches = data.get("searches")
    if not isinstance(raw_searches, list) or not raw_searches:
        raise ConfigError("define at least one [[searches]] entry")
    searches = [_search(entry, default_limit) for entry in raw_searches]
    names = [s.name for s in searches]
    if len(set(names)) != len(names):
        raise ConfigError(f"duplicate search names: {names}")

    search = _table(data, "search")
    backend = _get(search, "backend", str, "webview", "search")
    if backend not in BACKENDS:
        raise ConfigError(f"search.backend must be one of {BACKENDS}, got {backend!r}")
    profile = _get(search, "webview_profile", str, None, "search")
    webview_profile = Path(profile) if profile else default_webview_profile(environ)
    if not webview_profile.is_absolute():
        webview_profile = toml_path.parent / webview_profile

    notify = _table(data, "notify")
    channels = _get(notify, "channels", list, [], "notify")
    if any(c not in NOTIFY_CHANNELS for c in channels):
        raise ConfigError(f"notify.channels must be a subset of {NOTIFY_CHANNELS}, got {channels}")
    if "telegram" in channels and not (environ.get("TELEGRAM_BOT_TOKEN") and environ.get("TELEGRAM_CHAT_ID")):
        raise ConfigError("notify.channels has 'telegram' but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
                          "are not set (put them in .env)")

    token = _table(data, "token")
    sources = _get(token, "sources", list, ["manual", "fetch"], "token")
    if not sources or any(s not in TOKEN_SOURCES for s in sources):
        raise ConfigError(f"token.sources must be a non-empty subset of {TOKEN_SOURCES}, got {sources}")
    use_proxy = _get(token, "use_proxy", bool, False, "token")
    proxy = environ.get("UPWORK_PROXY") or None
    if use_proxy and not proxy:
        raise ConfigError("token.use_proxy = true but UPWORK_PROXY is not set (put it in .env)")

    return Config(
        db_path=db_path,
        searches=searches,
        backend=backend,
        webview_profile=webview_profile,
        notify_channels=list(channels),
        token_sources=list(sources),
        use_proxy=use_proxy,
        proxy=proxy if use_proxy else None,
        telegram_bot_token=environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=environ.get("TELEGRAM_CHAT_ID") or None,
    )


def default_webview_profile(environ: Mapping[str, str]) -> Path:
    """%LOCALAPPDATA%\\gigradar\\webview2 on Windows, else ~/.local/share/gigradar/webview2."""
    base = environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "gigradar" / "webview2"


def default_paths() -> tuple[Path, Path]:
    """(gigradar.toml, .env) at the repo root, independent of the current directory."""
    root = Path(__file__).resolve().parent.parent
    return root / "gigradar.toml", root / ".env"


def load_default() -> Config:
    toml_path, env_path = default_paths()
    load_dotenv(env_path, os.environ)
    return load_config(toml_path, os.environ)


def _search(entry: object, default_limit: int) -> SearchSpec:
    if not isinstance(entry, dict):
        raise ConfigError("each [[searches]] entry must be a table")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError("every [[searches]] entry needs a non-empty name")
    where = f"searches[{name}]"
    unknown = set(entry) - _FILTER_KEYS - {"name", "limit"}
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")

    values: dict[str, object] = {}
    for key in _FILTER_KEYS - {"contract_to_hire"}:
        value = _get(entry, key, str, None, where)
        if value is not None and key in _CHOICES and value not in _CHOICES[key]:
            raise ConfigError(f"{where}: {key} must be one of {_CHOICES[key]}, got {value!r}")
        values[key] = value
    contract_to_hire = _get(entry, "contract_to_hire", bool, None, where)

    if values["hourly_rate"] and values["fixed_budget"]:
        raise ConfigError(f"{where}: hourly_rate and fixed_budget are mutually exclusive")
    if values["hourly_rate"] and values["job_type"] == "fixed":
        raise ConfigError(f"{where}: hourly_rate is hourly-only; drop job_type = 'fixed'")
    if values["fixed_budget"] and values["job_type"] == "hourly":
        raise ConfigError(f"{where}: fixed_budget is fixed-only; drop job_type = 'hourly'")

    limit = _get(entry, "limit", int, default_limit, where)
    if not 1 <= limit <= MAX_LIMIT:
        raise ConfigError(f"{where}: limit must be 1..{MAX_LIMIT}, got {limit}")

    filters = SearchFilters(
        query=values["query"], sort="recency", job_type=values["job_type"], tier=values["tier"],
        workload=values["workload"], duration=values["duration"],
        contract_to_hire=True if contract_to_hire else None, client_hires=values["client_hires"],
        hourly_rate=values["hourly_rate"], fixed_budget=values["fixed_budget"],
        location=values["location"], highlight=False,
    )
    return SearchSpec(name=name, filters=filters, limit=limit)


def _reject_secrets(node: object, where: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key.lower() in SECRET_KEYS and not isinstance(value, dict):  # [token] table is fine
                raise ConfigError(f"secret-looking key '{where}{key}' in TOML; put secrets in .env instead")
            _reject_secrets(value, f"{where}{key}.")
    elif isinstance(node, list):
        for item in node:
            _reject_secrets(item, where)


def _table(data: dict, key: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{key}] must be a table")
    return value


def _get(table: dict, key: str, kind: type, fallback: object, where: str):
    value = table.get(key, fallback)
    # bool is a subclass of int: don't accept `limit = true`
    if value is not None and (not isinstance(value, kind) or (kind is int and isinstance(value, bool))):
        raise ConfigError(f"{where}.{key} must be {kind.__name__}, got {type(value).__name__}")
    return value
