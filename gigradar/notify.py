"""Notifiers: how gigradar tells you something.

Two kinds of message: `notify_jobs(alerts)` for new jobs (each channel formats them its own
way) and `notify(title, message)` for status messages such as "click needed". An Alert is a
job plus its score, or None when unscored (scoring off or failed: shown as "Score n/a").
Alerts arrive sorted (best score first); channels keep that order.

LogNotifier is always on. ToastNotifier (Windows toast) needs the optional `windows-toasts`
package (requirements-toast.txt). TelegramNotifier (gigradar.telegram) uses the stdlib only.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Protocol

from gigradar.config import Config, ConfigError
from gigradar.score import Score
from upwork_search import Job

log = logging.getLogger("gigradar")

APP_NAME = "gigradar"
MAX_TITLES = 3
SCORE_CHARS = 160  # score line incl. reason; matched-skill lists can be long


@dataclass(frozen=True)
class Alert:
    job: Job
    score: Score | None  # None = unscored


class Notifier(Protocol):
    def notify(self, title: str, message: str) -> None:
        """Deliver one status message. Raise on failure; the caller decides what that means."""
        ...

    def notify_jobs(self, alerts: list[Alert]) -> None:
        """Announce new jobs (non-empty, sorted). Raise on failure: they then stay unseen and are re-sent."""
        ...


def score_text(score: Score | None) -> str:
    """'82 · Full-stack web · matched: Next.js, React' (cut at SCORE_CHARS) or 'Score n/a'."""
    if score is None:
        return "Score n/a"
    text = f"{score.value} · {score.reason}"
    return text if len(text) <= SCORE_CHARS else text[:SCORE_CHARS - 1].rstrip(" ,·") + "…"


def summarize_alerts(alerts: list[Alert]) -> tuple[str, str]:
    """Short (title, message) for small surfaces: count (+ best score) and the first few titles."""
    scored = [a.score.value for a in alerts if a.score is not None]
    top = f" (top {max(scored)})" if scored else ""
    count = f"gigradar: {len(alerts)} new job{'s' if len(alerts) != 1 else ''}{top}"
    titles = "\n".join(f"• {a.score.value} {a.job.title}" if a.score else f"• {a.job.title}"
                       for a in alerts[:MAX_TITLES])
    more = f"\n…and {len(alerts) - MAX_TITLES} more" if len(alerts) > MAX_TITLES else ""
    return count, titles + more


class LogNotifier:
    def notify(self, title: str, message: str) -> None:
        log.info("%s: %s", title, message)

    def notify_jobs(self, alerts: list[Alert]) -> None:
        self.notify(*summarize_alerts(alerts))
        for alert in alerts:
            log.info("  %s | %s", score_text(alert.score), alert.job.title)


class ToastNotifier:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ConfigError("notify channel 'toast' is Windows-only")
        try:
            from windows_toasts import Toast, WindowsToaster
        except ImportError as exc:
            raise ConfigError("notify channel 'toast' needs: pip install -r requirements-toast.txt") from exc
        self._toast_cls = Toast
        self._toaster = WindowsToaster(APP_NAME)

    def notify(self, title: str, message: str) -> None:
        self._toaster.show_toast(self._toast_cls(text_fields=[title, message]))

    def notify_jobs(self, alerts: list[Alert]) -> None:
        self.notify(*summarize_alerts(alerts))


class MultiNotifier:
    """Fan out to several notifiers; one failing channel doesn't block the others.
    If any channel fails, the call raises (so the jobs stay unseen and every channel
    gets them again next run: at-least-once delivery, possible duplicates)."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers

    def notify(self, title: str, message: str) -> None:
        self._each(lambda n: n.notify(title, message))

    def notify_jobs(self, alerts: list[Alert]) -> None:
        self._each(lambda n: n.notify_jobs(alerts))

    def _each(self, call) -> None:
        failures = []
        for notifier in self.notifiers:
            try:
                call(notifier)
            except Exception as exc:  # noqa: BLE001  report all channels, then raise
                failures.append(f"{type(notifier).__name__}: {exc}")
        if failures:
            raise RuntimeError("notification failed: " + "; ".join(failures))


def build_notifier(cfg: Config) -> Notifier:
    """Log always, plus the configured channels. Missing optional deps fail at startup."""
    notifiers: list[Notifier] = [LogNotifier()]
    for channel in cfg.notify_channels:
        if channel == "toast":
            notifiers.append(ToastNotifier())
        elif channel == "telegram":
            from gigradar.telegram import TelegramNotifier, urllib_post
            if not (cfg.telegram_bot_token and cfg.telegram_chat_id):  # load_config enforces this too
                raise ConfigError("notify channel 'telegram' needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
            notifiers.append(TelegramNotifier(cfg.telegram_bot_token, cfg.telegram_chat_id, urllib_post))
    return MultiNotifier(notifiers)
