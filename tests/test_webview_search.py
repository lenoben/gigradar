"""Run: .venv/Scripts/python -m unittest discover -s tests   (fake page: no window, no network)"""

import json
import unittest

import upwork_search
from gigradar.config import SearchSpec
from gigradar.search import SearchBlocked
from gigradar.tokens import TokenUnavailable
from gigradar.webview_search import (CLICK_WAIT_S, TOKEN_WAIT_S, build_fetch_script, parse_fetch_result,
                                     run_session, wait_for_token)
from upwork_search import SearchFilters

SPEC = SearchSpec("py", SearchFilters("python", "recency", None, "expert", None, None, None, None,
                                      "50-150", None, None, False), 7)
RAW_JOB = {"title": "Build an AI agent", "ciphertext": "~021abc", "description": "d", "skills": []}


def graphql_body(results: list, total: int) -> str:
    node = {"paging": {"total": total}, "results": [{"jobTile": {"job": r}, **r} for r in results]}
    return json.dumps({"data": {"search": {"universalSearchNuxt": {"visitorJobSearchV1": node}}}})


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakePage:
    def __init__(self, token_at: float | None, clock: FakeClock, results: list) -> None:
        self.token_at, self.clock = token_at, clock
        self.results = list(results)
        self.scripts: list[str] = []

    def token_cookie(self) -> str | None:
        if self.token_at is None:
            return None
        return "tok" if self.clock() >= self.token_at else None

    def fetch(self, script: str, timeout_s: float) -> object:
        self.scripts.append(script)
        outcome = self.results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FetchScriptTest(unittest.TestCase):
    def test_script_carries_filters_limit_and_token(self) -> None:
        script = build_fetch_script(SPEC, "tok-123")
        self.assertIn(json.dumps(upwork_search.GRAPHQL_URL), script)
        self.assertIn('"Bearer " + "tok-123"', script)
        # the request body is a JSON string literal inside the script; decode it twice
        body_literal = script.split("body: ", 1)[1].split("\n", 1)[0]
        body = json.loads(json.loads(body_literal))
        req = body["variables"]["requestVariables"]
        self.assertEqual(req["paging"], {"offset": 0, "count": 7})
        self.assertEqual((req["userQuery"], req["contractorTier"], req["jobType"]), ("python", "ExpertLevel", "hourly"))


class ParseTest(unittest.TestCase):
    def test_ok(self) -> None:
        jobs = parse_fetch_result({"status": 200, "cf": None, "text": graphql_body([RAW_JOB], 1907)})
        self.assertEqual([j.url for j in jobs], ["https://www.upwork.com/jobs/~021abc"])

    def test_error_mapping(self) -> None:
        cases = [
            ({"status": None, "cf": None, "text": "TypeError: Failed to fetch"}, SearchBlocked),
            ({"status": 401, "cf": None, "text": ""}, TokenUnavailable),
            ({"status": 403, "cf": "challenge", "text": "<html>"}, SearchBlocked),
            ({"status": 200, "cf": "challenge", "text": "<html>"}, SearchBlocked),
            ({"status": 429, "cf": None, "text": ""}, SearchBlocked),
            ({"status": 500, "cf": None, "text": "oops"}, upwork_search.UpworkAPIError),
            ({"status": 200, "cf": None, "text": "<html>"}, upwork_search.UpworkAPIError),
            ({"status": 200, "cf": None, "text": "[1]"}, upwork_search.UpworkAPIError),
            ({"status": 200, "cf": None, "text": '{"errors": [{"message": "x"}]}'}, upwork_search.UpworkAPIError),
            ("not a dict", upwork_search.UpworkAPIError),
            (None, upwork_search.UpworkAPIError),
        ]
        for result, exc_type in cases:
            with self.subTest(result=str(result)[:40]):
                with self.assertRaises(exc_type):
                    parse_fetch_result(result)


class SessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()

    def page(self, token_at: float | None, results: list) -> FakePage:
        return FakePage(token_at, self.clock, results)

    def test_token_while_hidden(self) -> None:
        self.assertEqual(wait_for_token(self.page(3, []), self.clock, self.clock.sleep, False), "tok")

    def test_a_hidden_run_gives_up_after_the_short_wait_and_never_waits_for_a_click(self) -> None:
        # a scheduled run has no window to click in: Cloudflare's check means "stop, the app will ask the user once"
        with self.assertRaises(TokenUnavailable) as raised:
            wait_for_token(self.page(TOKEN_WAIT_S + 30, []), self.clock, self.clock.sleep, False)
        self.assertLessEqual(self.clock.now, TOKEN_WAIT_S + 2)
        self.assertIn("quick check", str(raised.exception))

    def test_visible_window_waits_for_the_click_at_once(self) -> None:
        self.assertEqual(wait_for_token(self.page(TOKEN_WAIT_S + 30, []), self.clock, self.clock.sleep, True), "tok")
        with self.assertRaises(TokenUnavailable):
            wait_for_token(self.page(None, []), self.clock, self.clock.sleep, True)
        self.assertLessEqual(self.clock.now, TOKEN_WAIT_S + 30 + CLICK_WAIT_S + 2)

    def test_one_fetch_per_search(self) -> None:
        ok = {"status": 200, "cf": None, "text": graphql_body([RAW_JOB], 1)}
        page = self.page(0, [ok, ok])
        result = run_session(page, lambda search: [search(SPEC), search(SPEC)], self.clock, self.clock.sleep, False)
        self.assertEqual([len(r) for r in result], [1, 1])
        self.assertEqual(len(page.scripts), 2)

    def test_fetch_timeout_is_blocked(self) -> None:
        page = self.page(0, [TimeoutError()])
        with self.assertRaises(SearchBlocked):
            run_session(page, lambda search: search(SPEC), self.clock, self.clock.sleep, False)

    def test_no_token_means_no_fetch(self) -> None:
        page = self.page(None, [])
        with self.assertRaises(TokenUnavailable):
            run_session(page, lambda search: search(SPEC), self.clock, self.clock.sleep, False)
        self.assertEqual(page.scripts, [])


if __name__ == "__main__":
    unittest.main()
