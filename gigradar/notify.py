"""Notifiers: how gigradar tells you something.

Two kinds of message: `notify_jobs(alerts, run, below)` for new jobs (each channel formats them its own
way) and `notify(title, message, link)` for status messages such as "Upwork needs a quick check". An Alert is a
job plus its score, or None when unscored (scoring off or failed: shown as "Score n/a").
Alerts arrive sorted (best score first); channels keep that order. `below` counts the run's other new jobs that scored
under the user's minimum score: they are not listed, but the message says how many there were.

A toast carries a link (gigradar://job/<id>, gigradar://jobs?run=<run>, gigradar://status): clicking it opens the
desktop app on that screen. `run` is the time stamp the run marked its jobs with (store.first_seen).

LogNotifier is always on. ToastNotifier (Windows toast) needs the optional `windows-toasts`
package (requirements-toast.txt). TelegramNotifier (gigradar.telegram) uses the stdlib only.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from gigradar import branding
from gigradar.config import Config, ConfigError
from gigradar.score import Score
from gigradar.store import job_id
from upwork_search import Job

log = logging.getLogger("gigradar")

MAX_TITLES = 3
SCORE_CHARS = 160  # score line incl. reason; matched-skill lists can be long


@dataclass(frozen=True)
class Alert:
    job: Job
    score: Score | None  # None = unscored


class Notifier(Protocol):
    def notify(self, title: str, message: str, link: str | None) -> None:
        """Deliver one status message. Raise on failure; the caller decides what that means."""
        ...

    def notify_jobs(self, alerts: list[Alert], run: str, below: int) -> None:
        """Announce new jobs (non-empty, sorted) and how many more were left out as below the minimum score. Raise on
        failure: they then stay unseen and are re-sent."""
        ...


def score_text(score: Score | None) -> str:
    """'82 · Full-stack web · matched: Next.js, React' (cut at SCORE_CHARS) or 'Score n/a'."""
    if score is None:
        return "Score n/a"
    text = f"{score.value} · {score.reason}"
    return text if len(text) <= SCORE_CHARS else text[:SCORE_CHARS - 1].rstrip(" ,·") + "…"


def below_text(below: int) -> str:
    """'2 below your minimum score', or '' when nothing was left out."""
    return f"{below} below your minimum score" if below else ""


def summarize_alerts(alerts: list[Alert], below: int) -> tuple[str, str]:
    """Short (title, message) for small surfaces: the number of new jobs (+ best score), how many of them were left
    out as below the minimum score, and the first few titles of the others."""
    scored = [a.score.value for a in alerts if a.score is not None]
    top = f" (top {max(scored)})" if scored else ""
    total = len(alerts) + below
    count = f"{branding.APP_DISPLAY_NAME}: {total} new job{'s' if total != 1 else ''}{top}"
    titles = "\n".join(f"• {a.score.value} {a.job.title}" if a.score else f"• {a.job.title}"
                       for a in alerts[:MAX_TITLES])
    more = f"\n…and {len(alerts) - MAX_TITLES} more" if len(alerts) > MAX_TITLES else ""
    left_out = f"{below_text(below)}\n" if below else ""
    return count, left_out + titles + more


def alerts_link(alerts: list[Alert], run: str, below: int) -> str:
    """One job -> its card; several, or some left out as below the minimum -> the list of that run."""
    jid = job_id(alerts[0].job) if len(alerts) == 1 and not below else None
    return branding.job_link(jid) if jid else branding.run_link(run)


class LogNotifier:
    def notify(self, title: str, message: str, link: str | None) -> None:
        log.info("%s: %s", title, message)

    def notify_jobs(self, alerts: list[Alert], run: str, below: int) -> None:
        self.notify(*summarize_alerts(alerts, below), None)
        for alert in alerts:
            log.info("  %s | %s", score_text(alert.score), alert.job.title)


class ToastNotifier:
    def __init__(self, identity: str, links: bool) -> None:
        """`identity`: the name or AppUserModelID toasts are sent under (branding.toast_identity). `links`: the
        gigradar:// handler exists, so a toast may carry a link."""
        self._links = links
        if sys.platform != "win32":
            raise ConfigError("notify channel 'toast' is Windows-only")
        try:
            from windows_toasts import Toast, WindowsToaster
        except ImportError as exc:
            raise ConfigError("notify channel 'toast' needs: pip install -r requirements-toast.txt") from exc
        self._toast_cls = Toast
        self._toaster = WindowsToaster(identity)

    def notify(self, title: str, message: str, link: str | None) -> None:
        self._toaster.show_toast(self._toast_cls(text_fields=[title, message], launch_action=link if self._links else None))

    def notify_jobs(self, alerts: list[Alert], run: str, below: int) -> None:
        self.notify(*summarize_alerts(alerts, below), alerts_link(alerts, run, below))


class MultiNotifier:
    """Fan out to several notifiers; one failing channel doesn't block the others.
    If any channel fails, the call raises (so the jobs stay unseen and every channel
    gets them again next run: at-least-once delivery, possible duplicates)."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers

    def notify(self, title: str, message: str, link: str | None) -> None:
        self._each(lambda n: n.notify(title, message, link))

    def notify_jobs(self, alerts: list[Alert], run: str, below: int) -> None:
        self._each(lambda n: n.notify_jobs(alerts, run, below))

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
            notifiers.append(build_toast_notifier())
        elif channel == "telegram":
            from gigradar.telegram import TelegramNotifier, urllib_post
            if not (cfg.telegram_bot_token and cfg.telegram_chat_id):  # load_config enforces this too
                raise ConfigError("notify channel 'telegram' needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
            notifiers.append(TelegramNotifier(cfg.telegram_bot_token, cfg.telegram_chat_id, urllib_post))
    return MultiNotifier(notifiers)


def build_toast_notifier() -> ToastNotifier:
    """The toast channel under the installed app's identity (its name and icon), or under the plain display name when
    run from a source checkout."""
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:   # the icon sits in the app's resource folder, one level above the helper's own folder
        branding.register_toast_identity(Path(sys.executable).parent.parent / "app.ico", branding.write_registry_values)
    return ToastNotifier(branding.toast_identity(frozen), branding.link_handler_registered())


def warning_notifier(notifier: Notifier) -> Notifier:
    """Where a crash warning goes: the log plus Telegram. Toast only when Telegram isn't configured
    (the warning matters most when nobody is at the PC, and toasts only reach the screen)."""
    from gigradar.telegram import TelegramNotifier  # telegram imports this module

    parts = notifier.notifiers if isinstance(notifier, MultiNotifier) else [notifier]
    chosen = [n for n in parts if isinstance(n, TelegramNotifier)] or [n for n in parts if isinstance(n, ToastNotifier)]
    return MultiNotifier([n for n in parts if isinstance(n, LogNotifier)] + chosen)
