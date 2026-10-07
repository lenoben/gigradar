"""Where the Upwork visitor token comes from. The watcher only sees TokenProvider.

Policy: no retries anywhere. A provider that can't deliver raises TokenUnavailable,
which (like every StopRun) means "stop this run, try again at the next scheduled run".
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import upwork_search

# The cache file upwork_search.py (CLI + web UI) already uses, so a pasted or fetched
# token is shared by all three.
TOKEN_CACHE = Path(upwork_search.TOKEN_CACHE)
TOKEN_TTL = upwork_search.TOKEN_TTL


class StopRun(Exception):
    """Abort the current run without retrying; the next scheduled run tries again."""


class TokenUnavailable(StopRun):
    """No usable visitor token right now."""


class TokenProvider(Protocol):
    name: str

    def get_token(self) -> str:
        """Return a token or raise TokenUnavailable. Never retries."""
        ...

    def invalidate(self) -> None:
        """Forget the current token (Upwork rejected it)."""
        ...


EXPIRY_MARGIN_S = 300  # treat a token as expired this long before its real expiry


def write_token(path: Path, token: str, now: float, expires: float | None) -> None:
    """Store a token in upwork_search's cache format {"token", "ts"}, plus an optional
    gigradar-only "expires" (unix seconds, e.g. the cookie's real expiry). upwork_search
    ignores the extra key and keeps applying its own TTL to `ts`."""
    data: dict[str, object] = {"token": token, "ts": now}
    if expires is not None:
        data["expires"] = expires
    path.write_text(json.dumps(data), encoding="utf-8")


class ManualTokenProvider:
    """A token pasted from a logged-out browser (or cached by FetchTokenProvider).
    Valid until its real expiry (minus EXPIRY_MARGIN_S) when known, else ts + ttl_s."""

    name = "manual"

    def __init__(self, cache_path: Path, ttl_s: float, clock: Callable[[], float]) -> None:
        self.cache_path = cache_path
        self.ttl_s = ttl_s
        self.clock = clock

    def get_token(self) -> str:
        try:
            cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
            token, ts = str(cached["token"]), float(cached["ts"])
            expires = float(cached["expires"]) if cached.get("expires") is not None else None
        except FileNotFoundError:
            raise TokenUnavailable(f"no cached token ({self.cache_path.name} missing)") from None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise TokenUnavailable(f"unreadable {self.cache_path.name}: {exc}") from exc
        if not token:
            raise TokenUnavailable(f"empty token in {self.cache_path.name}")
        valid_until = expires - EXPIRY_MARGIN_S if expires is not None else ts + self.ttl_s
        now = self.clock()
        if now >= valid_until:
            raise TokenUnavailable(f"cached token expired {(now - valid_until) / 60:.0f} min ago; get a fresh one")
        return token

    def invalidate(self) -> None:
        self.cache_path.unlink(missing_ok=True)


class FetchTokenProvider:
    """Fetch from the Upwork homepage: exactly ONE request, then cache the token."""

    name = "fetch"

    def __init__(self, fetch: Callable[[str | None], str], proxy: str | None, cache_path: Path,
                 clock: Callable[[], float]) -> None:
        self.fetch = fetch
        self.proxy = proxy
        self.cache_path = cache_path
        self.clock = clock

    def get_token(self) -> str:
        upwork_search.MAX_RETRIES = 1  # read per call by fetch_visitor_token; one request, no retries
        try:
            token = self.fetch(self.proxy)
        except upwork_search.TokenError as exc:  # 403 / challenge / no cookie
            raise TokenUnavailable(f"homepage token fetch failed: {exc}") from exc
        write_token(self.cache_path, token, self.clock(), None)  # expiry unknown: TTL applies
        return token

    def invalidate(self) -> None:
        self.cache_path.unlink(missing_ok=True)


class ChainProvider:
    """Try providers in order; the first token wins."""

    def __init__(self, providers: list[TokenProvider]) -> None:
        if not providers:
            raise ValueError("ChainProvider needs at least one provider")
        self.providers = providers
        self.name = "+".join(p.name for p in providers)

    def get_token(self) -> str:
        reasons = []
        for provider in self.providers:
            try:
                return provider.get_token()
            except TokenUnavailable as exc:
                reasons.append(f"{provider.name}: {exc}")
        raise TokenUnavailable("; ".join(reasons))

    def invalidate(self) -> None:
        for provider in self.providers:
            provider.invalidate()


def build_provider(sources: list[str], proxy: str | None) -> TokenProvider:
    """Providers for the configured `[token].sources`, in order."""
    factories: dict[str, Callable[[], TokenProvider]] = {
        "manual": lambda: ManualTokenProvider(TOKEN_CACHE, TOKEN_TTL, time.time),
        "fetch": lambda: FetchTokenProvider(upwork_search.fetch_visitor_token, proxy, TOKEN_CACHE, time.time),
    }
    return ChainProvider([factories[s]() for s in sources])
