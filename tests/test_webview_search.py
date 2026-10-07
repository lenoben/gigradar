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
    def __init__(self, token_at: float | None, after_show: bool, clock: FakeClock, results: list) -> None:
        self.token_at, self.after_show, self.clock = token_at, after_show, clock
        self.results = list(results)
        self.shown = False
        self.scripts: list[str] = []

    def token_cookie(self) -> str | None:
        if self.token_at is None or (self.after_show and not self.shown):
            return None
        return "tok" if self.clock() >= self.token_at else None

    def fetch(self, script: str, timeout_s: float) -> object:
        self.scripts.append(script)
        outcome = self.results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def show(self) -> None:
        self.shown = True


class FakeNotifier:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str]] = []

    def notify(self, title: str, message: str) -> None:
        self.sent.append((title, message))
        if self.fail:
            raise RuntimeError("toast broke")


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

    def page(self, token_at: float | None, after_show: bool, results: list) -> FakePage:
        return FakePage(token_at, after_show, self.clock, results)

    def test_token_while_hidden_no_show_no_notify(self) -> None:
        page, notifier = self.page(3, False, []), FakeNotifier(False)
        self.assertEqual(wait_for_token(page, notifier, self.clock, self.clock.sleep), "tok")
        self.assertFalse(page.shown)
        self.assertEqual(notifier.sent, [])

    def test_click_needed_shows_window_and_notifies(self) -> None:
        page, notifier = self.page(TOKEN_WAIT_S + 30, True, []), FakeNotifier(False)
        self.assertEqual(wait_for_token(page, notifier, self.clock, self.clock.sleep), "tok")
        self.assertTrue(page.shown)
        self.assertEqual(len(notifier.sent), 1)
        self.assertIn("click", notifier.sent[0][0])

    def test_no_click_in_time_is_token_unavailable(self) -> None:
        page, notifier = self.page(None, True, []), FakeNotifier(True)  # notifier failure is tolerated
        with self.assertRaises(TokenUnavailable):
            wait_for_token(page, notifier, self.clock, self.clock.sleep)
        self.assertTrue(page.shown)
        self.assertLessEqual(self.clock.now, TOKEN_WAIT_S + CLICK_WAIT_S + 2)

    def test_one_fetch_per_search(self) -> None:
        ok = {"status": 200, "cf": None, "text": graphql_body([RAW_JOB], 1)}
        page = self.page(0, False, [ok, ok])
        result = run_session(page, FakeNotifier(False), lambda search: [search(SPEC), search(SPEC)],
                             self.clock, self.clock.sleep)
        self.assertEqual([len(r) for r in result], [1, 1])
        self.assertEqual(len(page.scripts), 2)

    def test_fetch_timeout_is_blocked(self) -> None:
        page = self.page(0, False, [TimeoutError()])
        with self.assertRaises(SearchBlocked):
            run_session(page, FakeNotifier(False), lambda search: search(SPEC), self.clock, self.clock.sleep)

    def test_no_token_means_no_fetch(self) -> None:
        page = self.page(None, True, [])
        with self.assertRaises(TokenUnavailable):
            run_session(page, FakeNotifier(False), lambda search: search(SPEC), self.clock, self.clock.sleep)
        self.assertEqual(page.scripts, [])


if __name__ == "__main__":
    unittest.main()
