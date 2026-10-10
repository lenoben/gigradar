"""Searchers: how a configured search reaches Upwork. The watcher only sees `Searcher`.

Every search is exactly one request with no retries; failures that mean "try again at
the next scheduled run" raise StopRun subclasses.

CurlSearcher: curl_cffi from Python (works only when Upwork doesn't challenge the IP,
e.g. through a proxy). upwork_search.run_search() can't take an outside token and
re-fetches on a 401, so this calls the private `_search_with_token` instead.
UPSTREAM_SEARCH is the only reference to that private API: if upstream renames it, fix it here.
WebViewSearcher lives in gigradar.webview_search (optional pywebview dependency).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

import upwork_search
from gigradar.config import SearchSpec
from gigradar.tokens import StopRun, TokenProvider, TokenUnavailable
from upwork_search import Job, SearchFilters

UpstreamSearchFn = Callable[[SearchFilters, int, int, str, "str | None"], "tuple[list[Job], int]"]
SearchFn = Callable[[SearchSpec], list[Job]]
T = TypeVar("T")

UPSTREAM_SEARCH: UpstreamSearchFn = upwork_search._search_with_token


class SearchBlocked(StopRun):
    """Upwork/Cloudflare refused the search itself (e.g. HTTP 403 challenge)."""


class Searcher(Protocol):
    name: str

    def run(self, work: Callable[[SearchFn], T]) -> T:
        """Set up a session, call `work(search)` and return its result. `search(spec)` makes
        exactly one request. Inverted control because pywebview must own the main thread:
        `work` may run on another thread, so it must not touch thread-bound state (SQLite)."""
        ...


class CurlSearcher:
    name = "curl"

    def __init__(self, provider: TokenProvider, proxy: str | None, search_fn: UpstreamSearchFn) -> None:
        self.provider = provider
        self.proxy = proxy
        self.search_fn = search_fn

    def run(self, work: Callable[[SearchFn], T]) -> T:
        return work(lambda spec: run_one(spec, self.provider, self.proxy, self.search_fn))


def run_one(spec: SearchSpec, provider: TokenProvider, proxy: str | None,
            search_fn: UpstreamSearchFn) -> list[Job]:
    """Run `spec` once. Raises StopRun (TokenUnavailable / SearchBlocked) to end the run;
    other UpworkAPIErrors (GraphQL/shape errors) propagate unchanged."""
    token = provider.get_token()
    try:
        jobs, _total = search_fn(spec.filters, spec.limit, 0, token, proxy)
    except upwork_search.TokenError as exc:  # HTTP 401
        provider.invalidate()
        raise TokenUnavailable(f"token rejected by search: {exc}") from exc
    except upwork_search.UpworkAPIError as exc:
        if str(exc).startswith(("HTTP 403", "HTTP 429", "HTTP 503")):
            raise SearchBlocked(str(exc)[:200]) from exc
        raise
    return jobs
