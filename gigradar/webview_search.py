"""WebViewSearcher: run searches via fetch() inside a hidden WebView2 page.

Why: from a Cloudflare-challenged home IP, curl_cffi gets 403 even with a valid token,
while the same request from inside a real (non-automated) WebView2 page succeeds. A
persisted profile keeps cf_clearance (~1 year), so later runs pass without a click.

Per run: ONE page load of upwork.com, then ONE fetch() per configured search. No reloads,
no retries. If no token shows up (challenge wants a click), the window is shown and you
are notified; if you don't click within CLICK_WAIT_S, the run stops (StopRun -> exit 75).

Needs the optional `pywebview` package (requirements-webview.txt); imported lazily.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

import upwork_search
from gigradar import webview2
from gigradar.config import ConfigError, SearchSpec
from gigradar.notify import Notifier
from gigradar.search import SearchBlocked, SearchFn
from gigradar.tokens import StopRun, TokenUnavailable
from upwork_search import Job

log = logging.getLogger("gigradar")
T = TypeVar("T")

TOKEN_WAIT_S = 20      # hidden: wait this long for the token before asking for a click
CLICK_WAIT_S = 300     # visible: wait this long for you to click the challenge
FETCH_TIMEOUT_S = 30   # per search
POLL_S = 1.0
BLOCKED_STATUSES = (403, 429, 503)


class Page(Protocol):
    """The few things WebViewSearcher needs from a loaded page (faked in tests)."""

    def token_cookie(self) -> str | None: ...

    def fetch(self, script: str, timeout_s: float) -> object:
        """Evaluate a promise-returning script; return its resolved value. TimeoutError if none."""
        ...

    def show(self) -> None: ...


def build_fetch_script(spec: SearchSpec, token: str) -> str:
    """JS that POSTs one GraphQL search from inside the page and resolves to {status, cf, text}."""
    body = json.dumps({
        "query": upwork_search.GRAPHQL_QUERY,
        "variables": {"requestVariables": upwork_search.build_request_variables(spec.filters, 0, spec.limit)},
    })
    return f"""
      fetch({json.dumps(upwork_search.GRAPHQL_URL)}, {{
        method: "POST", credentials: "include",
        headers: {{"Accept": "*/*", "Content-Type": "application/json",
                   "X-Upwork-Accept-Language": "en-US", "Authorization": "Bearer " + {json.dumps(token)}}},
        body: {json.dumps(body)}
      }}).then(async r => ({{status: r.status, cf: r.headers.get("cf-mitigated"), text: await r.text()}}))
        .catch(e => ({{status: null, cf: null, text: String(e)}}))
    """


def parse_fetch_result(result: object) -> list[Job]:
    """Map the fetch() result to Jobs, or raise: TokenUnavailable (401), SearchBlocked
    (challenge / 403 / 429 / 503 / network), UpworkAPIError (anything else unexpected)."""
    if not isinstance(result, dict):
        raise upwork_search.UpworkAPIError(f"unexpected fetch result: {str(result)[:200]}")
    status, cf, text = result.get("status"), result.get("cf"), result.get("text")
    if status is None:
        raise SearchBlocked(f"fetch failed in page: {str(text)[:200]}")
    if status == 401:
        raise TokenUnavailable("token rejected by search (HTTP 401)")
    if cf == "challenge" or status in BLOCKED_STATUSES:
        raise SearchBlocked(f"HTTP {status} cf-mitigated={cf}")
    if status != 200:
        raise upwork_search.UpworkAPIError(f"HTTP {status}: {str(text)[:300]}")
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise upwork_search.UpworkAPIError(f"non-JSON response: {str(text)[:200]}") from exc
    if not isinstance(payload, dict):
        raise upwork_search.UpworkAPIError(f"unexpected JSON: {str(payload)[:200]}")
    results, _total = upwork_search.extract_page(payload)
    return [upwork_search.to_job(r) for r in results]


def wait_for_token(page: Page, notifier: Notifier, clock: Callable[[], float],
                   sleep: Callable[[float], None], visible: bool) -> str:
    """Token from the (hidden) page; if none after TOKEN_WAIT_S, show the window, notify,
    and wait CLICK_WAIT_S more. Raises TokenUnavailable if it never appears.
    `visible`: the window is already open (the app's upwork-check), so wait CLICK_WAIT_S at once."""
    if visible:
        token = _poll(page, CLICK_WAIT_S, clock, sleep)
        if token:
            return token
        raise TokenUnavailable(f"no Upwork token: challenge not solved within {CLICK_WAIT_S}s")
    token = _poll(page, TOKEN_WAIT_S, clock, sleep)
    if token:
        return token
    log.warning("no Upwork token after %ss; Cloudflare probably wants a click, showing window", TOKEN_WAIT_S)
    page.show()
    try:
        notifier.notify("gigradar: click needed",
                        f"Upwork's Cloudflare check wants a click. Window is open for {CLICK_WAIT_S // 60} min.")
    except Exception as exc:  # noqa: BLE001  a failed notification must not abort the wait
        log.warning("click-needed notification failed: %s", exc)
    token = _poll(page, CLICK_WAIT_S, clock, sleep)
    if token:
        return token
    raise TokenUnavailable(f"no Upwork token: challenge not solved within {CLICK_WAIT_S}s")


def run_session(page: Page, notifier: Notifier, work: Callable[[SearchFn], T],
                clock: Callable[[], float], sleep: Callable[[float], None], visible: bool) -> T:
    """Everything that happens once the page is loading: token, then work(search)."""
    token = wait_for_token(page, notifier, clock, sleep, visible)

    def search(spec: SearchSpec) -> list[Job]:
        try:
            result = page.fetch(build_fetch_script(spec, token), FETCH_TIMEOUT_S)
        except TimeoutError as exc:
            raise SearchBlocked(f"fetch() gave no result within {FETCH_TIMEOUT_S}s") from exc
        return parse_fetch_result(result)

    return work(search)


def _poll(page: Page, seconds: float, clock: Callable[[], float], sleep: Callable[[float], None]) -> str | None:
    deadline = clock() + seconds
    while True:
        token = page.token_cookie()
        if token or clock() >= deadline:
            return token
        sleep(POLL_S)


class PywebviewPage:
    """Page backed by a pywebview window (WebView2 on Windows). Not unit-tested: thin glue."""

    def __init__(self, window) -> None:
        self.window = window

    def token_cookie(self) -> str | None:
        for cookie in self.window.get_cookies():
            if upwork_search.TOKEN_COOKIE in cookie:
                return cookie[upwork_search.TOKEN_COOKIE].value or None
        return None

    def fetch(self, script: str, timeout_s: float) -> object:
        done, box = threading.Event(), {}
        self.window.evaluate_js(script, lambda value: (box.update(value=value), done.set()))
        if not done.wait(timeout_s):
            raise TimeoutError
        value = box.get("value")
        if isinstance(value, str):  # pywebview usually hands over parsed JSON; be lenient
            try:
                value = json.loads(value)
            except ValueError:
                pass
        return value

    def show(self) -> None:
        self.window.show()


class WebViewSearcher:
    name = "webview"

    def __init__(self, profile_dir: Path, notifier: Notifier, search_count: int, visible: bool) -> None:
        try:
            import webview  # noqa: F401  fail at startup, not mid-run
        except ImportError as exc:
            raise ConfigError("search.backend = 'webview' needs: pip install -r requirements-webview.txt") from exc
        self.profile_dir = profile_dir
        self.notifier = notifier
        self.visible = visible  # True: show the window from the start (the app's one-time Upwork check)
        # Backstop for a hung page/GUI: every wait inside is bounded, this bounds the sum.
        self.hard_limit_s = TOKEN_WAIT_S + CLICK_WAIT_S + FETCH_TIMEOUT_S * search_count + 60

    def run(self, work: Callable[[SearchFn], T]) -> T:
        import webview

        webview2.require(webview2.read_registry)   # fail fast: without the runtime pywebview would fall back to IE
        webview.windows.clear()                    # a window left over from a failed start would be picked first
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        box: dict[str, object] = {}
        window = webview.create_window("gigradar", upwork_search.UPWORK_HOME, width=1100, height=800,
                                      hidden=not self.visible)

        def session() -> None:  # runs on pywebview's worker thread; GUI owns the main thread
            try:
                box["result"] = run_session(PywebviewPage(window), self.notifier, work, time.monotonic, time.sleep,
                                           self.visible)
            except BaseException as exc:  # noqa: BLE001  re-raised on the caller's thread below
                box["error"] = exc
            finally:
                window.destroy()

        def watchdog() -> None:
            box.setdefault("error", StopRun(f"webview session exceeded {self.hard_limit_s}s"))
            window.destroy()

        timer = threading.Timer(self.hard_limit_s, watchdog)
        timer.daemon = True
        timer.start()
        try:
            webview.start(session, gui="edgechromium", private_mode=False, storage_path=str(self.profile_dir))
        finally:
            timer.cancel()
        if "error" in box:
            raise box["error"]  # type: ignore[misc]
        if "result" not in box:
            raise StopRun("webview closed before the session finished")
        return box["result"]  # type: ignore[return-value]
