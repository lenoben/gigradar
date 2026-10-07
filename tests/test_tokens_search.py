"""Run: .venv/Scripts/python -m unittest discover -s tests   (stdlib only, no network)"""

import json
import tempfile
import unittest
from pathlib import Path

import upwork_search
from gigradar.config import SearchSpec
from gigradar.search import SearchBlocked, run_one
from gigradar.tokens import (ChainProvider, FetchTokenProvider, ManualTokenProvider, StopRun,
                             TokenUnavailable, write_token)
from upwork_search import Job, SearchFilters

NOW = 1_800_000_000.0
FILTERS = SearchFilters("python", "recency", None, None, None, None, None, None, None, None, None, False)
SPEC = SearchSpec(name="py", filters=FILTERS, limit=5)


def clock() -> float:
    return NOW


class FakeProvider:
    name = "fake"

    def __init__(self, token: str | None) -> None:
        self.token = token
        self.invalidated = 0

    def get_token(self) -> str:
        if self.token is None:
            raise TokenUnavailable("none")
        return self.token

    def invalidate(self) -> None:
        self.invalidated += 1


class TokensTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name) / ".token_cache.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def manual(self) -> ManualTokenProvider:
        return ManualTokenProvider(self.cache, 1200, clock)

    def test_manual_fresh_token(self) -> None:
        write_token(self.cache, "tok", NOW - 60, None)
        self.assertEqual(self.manual().get_token(), "tok")
        self.assertEqual(json.loads(self.cache.read_text()), {"token": "tok", "ts": NOW - 60})

    def test_cache_stays_upstream_compatible(self) -> None:
        write_token(self.cache, "tok", NOW, NOW + 86400)
        self.assertEqual(json.loads(self.cache.read_text()), {"token": "tok", "ts": NOW, "expires": NOW + 86400})
        upstream_cache, upwork_search.TOKEN_CACHE = upwork_search.TOKEN_CACHE, str(self.cache)
        real_time, upwork_search.time.time = upwork_search.time.time, clock
        try:  # upstream ignores "expires" and applies its own TTL to ts
            self.assertEqual(upwork_search.get_cached_token(None), "tok")
        finally:
            upwork_search.TOKEN_CACHE, upwork_search.time.time = upstream_cache, real_time

    def test_manual_real_expiry_beats_fixed_ttl(self) -> None:
        write_token(self.cache, "tok", NOW - 3 * 3600, NOW + 20 * 3600)  # 3 h old, lives 20 h more
        self.assertEqual(self.manual().get_token(), "tok")

    def test_manual_real_expiry_margin(self) -> None:
        write_token(self.cache, "tok", NOW - 60, NOW + 299)  # inside the 5-min margin
        with self.assertRaises(TokenUnavailable):
            self.manual().get_token()
        write_token(self.cache, "tok", NOW - 60, NOW + 301)
        self.assertEqual(self.manual().get_token(), "tok")

    def test_manual_unavailable_cases(self) -> None:
        cases = {"missing": None, "expired": ("tok", NOW - 1200, None), "empty": ("", NOW, None),
                 "expired-real": ("tok", NOW, NOW - 1), "corrupt": "not json", "not-object": "[1]",
                 "bad-expires": '{"token": "t", "ts": 1, "expires": "soon"}'}
        for label, content in cases.items():
            with self.subTest(label):
                self.cache.unlink(missing_ok=True)
                if isinstance(content, tuple):
                    write_token(self.cache, *content)
                elif content is not None:
                    self.cache.write_text(content)
                with self.assertRaises(TokenUnavailable):
                    self.manual().get_token()

    def test_manual_invalidate_deletes_cache(self) -> None:
        write_token(self.cache, "tok", NOW, None)
        self.manual().invalidate()
        self.assertFalse(self.cache.exists())
        self.manual().invalidate()  # missing file is fine

    def test_fetch_success_caches_and_forces_single_attempt(self) -> None:
        calls = []
        upwork_search.MAX_RETRIES = 5

        def fetch(proxy):
            calls.append((proxy, upwork_search.MAX_RETRIES))
            return "fetched"

        provider = FetchTokenProvider(fetch, "http://proxy:1", self.cache, clock)
        self.assertEqual(provider.get_token(), "fetched")
        self.assertEqual(calls, [("http://proxy:1", 1)])
        self.assertEqual(self.manual().get_token(), "fetched")  # shared cache

    def test_fetch_failure_is_token_unavailable_once(self) -> None:
        calls = []

        def fetch(proxy):
            calls.append(proxy)
            raise upwork_search.TokenError("HTTP 403")

        with self.assertRaises(TokenUnavailable) as ctx:
            FetchTokenProvider(fetch, None, self.cache, clock).get_token()
        self.assertIn("HTTP 403", str(ctx.exception))
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.cache.exists())

    def test_chain_order_and_fallthrough(self) -> None:
        first, second = FakeProvider(None), FakeProvider("b")
        self.assertEqual(ChainProvider([first, second]).get_token(), "b")
        self.assertEqual(ChainProvider([FakeProvider("a"), second]).get_token(), "a")

    def test_chain_all_fail_and_invalidate_all(self) -> None:
        a, b = FakeProvider(None), FakeProvider(None)
        chain = ChainProvider([a, b])
        with self.assertRaises(TokenUnavailable) as ctx:
            chain.get_token()
        self.assertEqual(str(ctx.exception), "fake: none; fake: none")
        chain.invalidate()
        self.assertEqual((a.invalidated, b.invalidated), (1, 1))


class SearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calls = []

    def search_fn(self, outcome):
        def fn(filters, limit, offset, token, proxy):
            self.calls.append((filters, limit, offset, token, proxy))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome, len(outcome)
        return fn

    def test_success_single_request(self) -> None:
        job = Job("t", "https://www.upwork.com/jobs/~01", None, None, None, None, None, None, "", "")
        jobs = run_one(SPEC, FakeProvider("tok"), None, self.search_fn([job]))
        self.assertEqual(jobs, [job])
        self.assertEqual(self.calls, [(FILTERS, 5, 0, "tok", None)])

    def test_no_token_means_no_request(self) -> None:
        with self.assertRaises(TokenUnavailable):
            run_one(SPEC, FakeProvider(None), None, self.search_fn([]))
        self.assertEqual(self.calls, [])

    def test_401_invalidates_and_stops_without_retry(self) -> None:
        provider = FakeProvider("tok")
        with self.assertRaises(TokenUnavailable):
            run_one(SPEC, provider, None, self.search_fn(upwork_search.TokenError("HTTP 401")))
        self.assertEqual((provider.invalidated, len(self.calls)), (1, 1))

    def test_403_is_blocked_stoprun(self) -> None:
        err = upwork_search.UpworkAPIError("HTTP 403: <title>Just a moment...</title>")
        with self.assertRaises(SearchBlocked) as ctx:
            run_one(SPEC, FakeProvider("tok"), None, self.search_fn(err))
        self.assertIsInstance(ctx.exception, StopRun)
        self.assertEqual(len(self.calls), 1)

    def test_other_api_errors_propagate(self) -> None:
        err = upwork_search.UpworkAPIError("GraphQL errors: [...]")
        with self.assertRaises(upwork_search.UpworkAPIError):
            run_one(SPEC, FakeProvider("tok"), None, self.search_fn(err))


if __name__ == "__main__":
    unittest.main()
