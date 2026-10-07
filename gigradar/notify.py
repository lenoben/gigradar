"""Notifiers: how gigradar tells you something.

Two kinds of message: `notify_jobs(jobs)` for new jobs (each channel formats them its own
way) and `notify(title, message)` for status messages such as "click needed".

LogNotifier is always on. ToastNotifier (Windows toast) needs the optional `windows-toasts`
package (requirements-toast.txt). TelegramNotifier (gigradar.telegram) uses the stdlib only.
"""

from __future__ import annotations

import logging
import sys
from typing import Protocol

from gigradar.config import Config, ConfigError
from upwork_search import Job

log = logging.getLogger("gigradar")

APP_NAME = "gigradar"
MAX_TITLES = 3


class Notifier(Protocol):
    def notify(self, title: str, message: str) -> None:
        """Deliver one status message. Raise on failure; the caller decides what that means."""
        ...

    def notify_jobs(self, jobs: list[Job]) -> None:
        """Announce new jobs (non-empty). Raise on failure: the jobs then stay unseen and are re-sent."""
        ...


def summarize_jobs(jobs: list[Job]) -> tuple[str, str]:
    """Short (title, message) for small surfaces: count + first few titles."""
    titles = "\n".join(f"• {job.title}" for job in jobs[:MAX_TITLES])
    more = f"\n…and {len(jobs) - MAX_TITLES} more" if len(jobs) > MAX_TITLES else ""
    return f"gigradar: {len(jobs)} new job{'s' if len(jobs) != 1 else ''}", titles + more


class LogNotifier:
    def notify(self, title: str, message: str) -> None:
        log.info("%s: %s", title, message)

    def notify_jobs(self, jobs: list[Job]) -> None:
        self.notify(*summarize_jobs(jobs))


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

    def notify_jobs(self, jobs: list[Job]) -> None:
        self.notify(*summarize_jobs(jobs))


class MultiNotifier:
    """Fan out to several notifiers; one failing channel doesn't block the others.
    If any channel fails, the call raises (so the jobs stay unseen and every channel
    gets them again next run: at-least-once delivery, possible duplicates)."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers

    def notify(self, title: str, message: str) -> None:
        self._each(lambda n: n.notify(title, message))

    def notify_jobs(self, jobs: list[Job]) -> None:
        self._each(lambda n: n.notify_jobs(jobs))

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
