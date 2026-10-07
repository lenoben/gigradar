"""Run: .venv/Scripts/python -m unittest discover -s tests   (fake HTTP sender: no network)"""

import json
import re
import unittest
import urllib.parse

from gigradar.telegram import (DIGEST_THRESHOLD, MAX_CHARS, SEND_PAUSE_S, TelegramError, TelegramNotifier,
                               format_brief, format_digest, pay_line)
from upwork_search import Job

TOKEN = "123456:SECRET-token_value"


def job(cipher: str, **overrides) -> Job:
    fields = dict(title=f"Job {cipher}", url=f"https://www.upwork.com/jobs/{cipher}", job_type="HOURLY",
                  published=None, hourly_min="50.0", hourly_max="80.0", fixed_budget=None, tier="ExpertLevel",
                  skills="Python, Rust", description="Build things.")
    fields.update(overrides)
    return Job(**fields)


class FakePost:
    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, body: bytes) -> tuple[int, bytes]:
        self.calls.append((url, dict(urllib.parse.parse_qsl(body.decode()))))
        reply = self.replies.pop(0) if self.replies else (200, b'{"ok": true, "result": {}}')
        if isinstance(reply, Exception):
            raise reply
        return reply


def notifier(post: FakePost) -> tuple[TelegramNotifier, list[float]]:
    sleeps: list[float] = []
    n = TelegramNotifier(TOKEN, "42", post)
    n._sleep = sleeps.append
    return n, sleeps


def balanced(text: str) -> bool:
    return all(text.count(f"<{t}") == text.count(f"</{t}>") for t in ("b", "i", "a"))


class FormatTest(unittest.TestCase):
    def test_pay_line_variants(self) -> None:
        self.assertEqual(pay_line(job("~1")), "Hourly $50–80/hr · Expert")
        self.assertEqual(pay_line(job("~1", hourly_min="40.0", hourly_max="40.0")), "Hourly $40/hr · Expert")
        self.assertEqual(pay_line(job("~1", hourly_min=None, hourly_max=None, tier=None)), "Hourly")
        self.assertEqual(pay_line(job("~1", job_type="FIXED", fixed_budget="1500", tier="IntermediateLevel")),
                         "Fixed $1,500 · Intermediate")
        self.assertEqual(pay_line(job("~1", job_type="FIXED", fixed_budget="0.0", tier=None)), "Fixed price")
        self.assertEqual(pay_line(job("~1", job_type=None, tier=None)), "")

    def test_brief_content_and_escaping(self) -> None:
        text = format_brief(job("~1", title="AI <agent> & tools", skills="C++, <b>", description="Use <script> & more"))
        self.assertTrue(text.startswith("<b>AI &lt;agent&gt; &amp; tools</b>\nHourly $50–80/hr · Expert"))
        self.assertIn("<i>C++, &lt;b&gt;</i>", text)
        self.assertIn("Use &lt;script&gt; &amp; more", text)
        self.assertIn('<a href="https://www.upwork.com/jobs/~1">Open on Upwork</a>', text)
        self.assertTrue(balanced(text))

    def test_brief_description_cut_at_word_boundary(self) -> None:
        text = format_brief(job("~1", description="word " * 200))
        body = text.split("\n\n")[1]
        self.assertTrue(body.endswith("word…"))
        self.assertLessEqual(len(body), 301)

    def test_brief_never_exceeds_limit_even_when_escaping_explodes(self) -> None:
        nasty = job("~1", title="&" * 1000, skills="&" * 1000, description="& " * 5000)
        text = format_brief(nasty)
        self.assertLessEqual(len(text), MAX_CHARS)
        self.assertTrue(balanced(text))

    def test_href_is_attribute_escaped(self) -> None:
        text = format_brief(job("~1", url='https://x/"><script>'))
        self.assertIn('href="https://x/&quot;&gt;&lt;script&gt;"', text)

    def test_digest_splits_on_lines_within_limit(self) -> None:
        jobs = [job(f"~{i:03d}", title=f"Job {i} " + "x" * 150) for i in range(60)]
        messages = format_digest(jobs)
        self.assertGreater(len(messages), 1)
        self.assertTrue(messages[0].startswith("<b>gigradar: 60 new jobs</b>"))
        for message in messages:
            self.assertLessEqual(len(message), MAX_CHARS)
            self.assertTrue(balanced(message))
        joined = "\n".join(messages)
        self.assertEqual(sum(joined.count(f"jobs/~{i:03d}\"") for i in range(60)), 60)


class SendTest(unittest.TestCase):
    def test_few_jobs_one_brief_each_with_pause(self) -> None:
        post = FakePost([])
        n, sleeps = notifier(post)
        n.notify_jobs([job("~1"), job("~2")])
        self.assertEqual(len(post.calls), 2)
        self.assertEqual(sleeps, [SEND_PAUSE_S])
        url, form = post.calls[0]
        self.assertEqual(url, f"https://api.telegram.org/bot{TOKEN}/sendMessage")
        self.assertEqual((form["chat_id"], form["parse_mode"], form["disable_web_page_preview"]), ("42", "HTML", "true"))
        self.assertTrue(form["text"].startswith("<b>Job ~1</b>"))

    def test_threshold_switches_to_digest(self) -> None:
        post = FakePost([])
        notifier(post)[0].notify_jobs([job(f"~{i}") for i in range(DIGEST_THRESHOLD)])
        self.assertEqual(len(post.calls), DIGEST_THRESHOLD)
        post = FakePost([])
        notifier(post)[0].notify_jobs([job(f"~{i}") for i in range(DIGEST_THRESHOLD + 1)])
        self.assertEqual(len(post.calls), 1)
        self.assertIn(f"gigradar: {DIGEST_THRESHOLD + 1} new jobs", post.calls[0][1]["text"])

    def test_status_message_escaped_and_capped(self) -> None:
        post = FakePost([])
        notifier(post)[0].notify("gigradar: click needed", "<win> & " * 1000)
        text = post.calls[0][1]["text"]
        self.assertTrue(text.startswith("<b>gigradar: click needed</b>\n&lt;win&gt; &amp;"))
        self.assertLessEqual(len(text), MAX_CHARS)

    def test_api_error_raises_without_token(self) -> None:
        reply = (401, json.dumps({"ok": False, "description": "Unauthorized"}).encode())
        with self.assertRaises(TelegramError) as ctx:
            notifier(FakePost([reply]))[0].notify_jobs([job("~1")])
        self.assertEqual(str(ctx.exception), "HTTP 401: Unauthorized")

    def test_ok_false_and_non_json_raise(self) -> None:
        for reply in [(200, b'{"ok": false, "description": "Bad Request: chat not found"}'), (502, b"<html>bad gateway")]:
            with self.subTest(reply=reply[0]), self.assertRaises(TelegramError):
                notifier(FakePost([reply]))[0].notify("t", "m")

    def test_network_error_is_scrubbed_of_token(self) -> None:
        leak = OSError(f"connect failed for https://api.telegram.org/bot{TOKEN}/sendMessage")
        with self.assertRaises(TelegramError) as ctx:
            notifier(FakePost([leak]))[0].notify("t", "m")
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertIn("<bot-token>", str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)  # `from None`: no chained traceback with the URL

    def test_failure_mid_batch_stops_and_raises(self) -> None:
        post = FakePost([(200, b'{"ok": true}'), (429, b'{"ok": false, "description": "Too Many Requests"}')])
        with self.assertRaises(TelegramError):
            notifier(post)[0].notify_jobs([job("~1"), job("~2"), job("~3")])
        self.assertEqual(len(post.calls), 2)  # no retry, nothing after the failure


class NoLoggingTest(unittest.TestCase):
    def test_module_does_not_log(self) -> None:
        """The token lives in this module; it has no logger, so it can't log the token or API URL."""
        import gigradar.telegram as module
        source = open(module.__file__, encoding="utf-8").read()
        self.assertIsNone(re.search(r"^\s*(import logging|from logging)", source, re.M))
        self.assertNotIn("getLogger", source)


if __name__ == "__main__":
    unittest.main()
