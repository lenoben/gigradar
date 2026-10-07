"""One search with an externally sourced token: exactly one GraphQL request, no retries.

upwork_search.run_search() can't take an outside token and re-fetches on a 401, so this
calls the private `_search_with_token` instead. UPSTREAM_SEARCH is the only reference
to that private API: if upstream renames it, fix it here.
"""

from __future__ import annotations

from collections.abc import Callable

import upwork_search
from gigradar.config import SearchSpec
from gigradar.tokens import StopRun, TokenProvider, TokenUnavailable
from upwork_search import Job, SearchFilters

SearchFn = Callable[[SearchFilters, int, int, str, "str | None"], "tuple[list[Job], int]"]

UPSTREAM_SEARCH: SearchFn = upwork_search._search_with_token


class SearchBlocked(StopRun):
    """Upwork/Cloudflare refused the search itself (e.g. HTTP 403 challenge)."""


def run_one(spec: SearchSpec, provider: TokenProvider, proxy: str | None, search_fn: SearchFn) -> list[Job]:
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
