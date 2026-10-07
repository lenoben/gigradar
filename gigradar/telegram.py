"""TelegramNotifier: new-job briefs via the plain Telegram Bot API (stdlib urllib only).

Up to DIGEST_THRESHOLD new jobs -> one brief per job; more -> one digest (titles + links),
split into several messages if needed. Every message stays within Telegram's 4096-char
limit and uses HTML parse mode with all job text escaped.

The bot token is part of the API URL: it is never logged, and it is scrubbed from any
error message before that message can reach a log.

Live test (sends ONE sample message to your chat, using .env):
    python -m gigradar.telegram --test
"""

from __future__ import annotations

import html
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from upwork_search import Job

API = "https://api.telegram.org"
MAX_CHARS = 4096
DIGEST_THRESHOLD = 5          # more new jobs than this in one run -> digest instead of briefs
DESCRIPTION_CHARS = 300
TITLE_CHARS = 200
SKILLS_CHARS = 300
STATUS_CHARS = 600            # status text: even escaped x5 ("&" -> "&amp;") + title stays < 4096
SEND_PAUSE_S = 1.1            # Telegram allows ~1 message/second per chat
TIMEOUT_S = 15
TIERS = {"EntryLevel": "Entry", "IntermediateLevel": "Intermediate", "ExpertLevel": "Expert"}

# (url, form-encoded body) -> (HTTP status, response body)
PostFn = Callable[[str, bytes], tuple[int, bytes]]


class TelegramError(Exception):
    """Telegram rejected or failed a send. The message never contains the bot token."""


def urllib_post(url: str, body: bytes) -> tuple[int, bytes]:
    """POST a form body; returns (status, body) for HTTP errors too (Telegram explains them in JSON)."""
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _cut(text: str, limit: int) -> str:
    """Shorten to `limit` chars at a word boundary, with an ellipsis. Operates on plain text."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip(" ,.;:-") + "…"


def _money(value: str | None) -> str | None:
    try:
        amount = float(value) if value not in (None, "") else None
    except ValueError:
        return None
    if amount is None or amount <= 0:
        return None
    return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"


def pay_line(job: Job) -> str:
    """'Hourly $50–80/hr · Expert', 'Fixed $1,000 · Intermediate', … (plain text)."""
    parts = []
    kind = (job.job_type or "").upper()
    if kind == "HOURLY":
        low, high = _money(job.hourly_min), _money(job.hourly_max)
        if low and high and low != high:
            parts.append(f"Hourly {low}–{high.lstrip('$')}/hr")
        elif low or high:
            parts.append(f"Hourly {low or high}/hr")
        else:
            parts.append("Hourly")
    elif kind == "FIXED":
        budget = _money(job.fixed_budget)
        parts.append(f"Fixed {budget}" if budget else "Fixed price")
    elif job.job_type:
        parts.append(job.job_type.replace("_", " ").title())
    if job.tier:
        parts.append(TIERS.get(job.tier, job.tier))
    return " · ".join(parts)


def _link(job: Job, text: str) -> str:
    return f'<a href="{html.escape(job.url, quote=True)}">{html.escape(text, quote=False)}</a>'


def format_brief(job: Job) -> str:
    """One job as an HTML message, always <= MAX_CHARS. Escaping can grow text up to 5x
    ("&" -> "&amp;"), so if the capped parts still overflow, description and skills shrink."""
    desc_chars, skills_chars = DESCRIPTION_CHARS, SKILLS_CHARS
    while True:
        text = _brief(job, desc_chars, skills_chars)
        if len(text) <= MAX_CHARS or (desc_chars == 0 and skills_chars == 0):
            return text
        desc_chars, skills_chars = desc_chars // 2, skills_chars // 2


def _brief(job: Job, desc_chars: int, skills_chars: int) -> str:
    lines = [f"<b>{html.escape(_cut(job.title, TITLE_CHARS), quote=False)}</b>"]
    pay = pay_line(job)
    if pay:
        lines.append(html.escape(pay, quote=False))
    if job.skills and skills_chars:
        lines.append(f"<i>{html.escape(_cut(job.skills, skills_chars), quote=False)}</i>")
    if job.description and desc_chars:
        lines.append("")
        lines.append(html.escape(_cut(job.description, desc_chars), quote=False))
    if job.url:
        lines.append("")
        lines.append(_link(job, "Open on Upwork"))
    return "\n".join(lines)


def format_digest(jobs: list[Job]) -> list[str]:
    """Titles + links, split into as many messages as needed, each <= MAX_CHARS.
    Splits only between whole lines, so no HTML tag is ever cut."""
    header = f"<b>gigradar: {len(jobs)} new jobs</b>"
    lines = []
    for job in jobs:
        title = _cut(job.title, TITLE_CHARS)
        pay = pay_line(job)
        entry = f"• {_link(job, title) if job.url else html.escape(title, quote=False)}"
        lines.append(entry + (f" — {html.escape(pay, quote=False)}" if pay else ""))
    messages, current = [], header
    for line in lines:
        if len(current) + 1 + len(line) > MAX_CHARS:
            messages.append(current)
            current = "<b>gigradar (cont.)</b>"
        current += "\n" + line
    messages.append(current)
    return messages


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, post: PostFn) -> None:
        self._token = bot_token
        self.chat_id = chat_id
        self._post = post
        self._sleep: Callable[[float], None] = time.sleep

    def notify(self, title: str, message: str) -> None:
        # Cap the plain text first so escaping (which only lengthens) can't break the limit.
        title, message = title[:TITLE_CHARS], message[:STATUS_CHARS]
        self._send(f"<b>{html.escape(title, quote=False)}</b>\n{html.escape(message, quote=False)}")

    def notify_jobs(self, jobs: list[Job]) -> None:
        messages = [format_brief(job) for job in jobs] if len(jobs) <= DIGEST_THRESHOLD else format_digest(jobs)
        for i, text in enumerate(messages):
            if i:
                self._sleep(SEND_PAUSE_S)
            self._send(text)

    def _send(self, text: str) -> None:
        if len(text) > MAX_CHARS:  # formatting caps should make this impossible
            raise TelegramError(f"message too long ({len(text)} > {MAX_CHARS} chars)")
        body = urllib.parse.urlencode({
            "chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        try:
            status, raw = self._post(f"{API}/bot{self._token}/sendMessage", body)
        except Exception as exc:  # noqa: BLE001  network errors: re-raise without the token
            raise TelegramError(self._scrub(f"send failed: {type(exc).__name__}: {exc}")) from None
        try:
            reply = json.loads(raw)
        except ValueError:
            reply = {}
        if status != 200 or not reply.get("ok"):
            description = reply.get("description") or raw[:200].decode("utf-8", "replace")
            raise TelegramError(self._scrub(f"HTTP {status}: {description}"))

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "<bot-token>") if self._token else text


def _sample_job() -> Job:
    return Job(
        title="gigradar test: Senior Python & <Next.js> engineer for AI tooling",
        url="https://www.upwork.com/jobs/~0123456789abcdef",
        job_type="HOURLY", published=None, hourly_min="50.0", hourly_max="80.0", fixed_budget=None,
        tier="ExpertLevel", skills="Python, Next.js, PostgreSQL, LLM",
        description="This is a sample brief from `python -m gigradar.telegram --test`. "
                    "It checks bold titles, escaping of <tags> & ampersands, the pay line, "
                    "skills, a description cut at ~300 characters and the link. " * 3,
    )


def main(argv: list[str]) -> int:
    if argv != ["--test"]:
        print("usage: python -m gigradar.telegram --test   (sends one sample message)", file=sys.stderr)
        return 2
    import os

    from gigradar.config import default_paths, load_dotenv

    _, env_path = default_paths()
    load_dotenv(env_path, os.environ)
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env", file=sys.stderr)
        return 2
    try:
        TelegramNotifier(token, chat_id, urllib_post).notify_jobs([_sample_job()])
    except TelegramError as exc:
        print(f"Telegram test FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"Telegram test message sent to chat {chat_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
