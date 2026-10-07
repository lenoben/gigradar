"""Notifiers: how gigradar tells you something (new jobs, "click needed").

LogNotifier is always on. ToastNotifier (Windows toast) needs the optional `windows-toasts`
package (requirements-toast.txt). Telegram will be another Notifier later.
"""

from __future__ import annotations

import logging
import sys
from typing import Protocol

from gigradar.config import ConfigError

log = logging.getLogger("gigradar")

APP_NAME = "gigradar"


class Notifier(Protocol):
    def notify(self, title: str, message: str) -> None:
        """Deliver one notification. Raise on failure; the caller decides what that means."""
        ...


class LogNotifier:
    def notify(self, title: str, message: str) -> None:
        log.info("%s: %s", title, message)


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


class MultiNotifier:
    """Fan out to several notifiers; one failing channel doesn't block the others."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers

    def notify(self, title: str, message: str) -> None:
        failures = []
        for notifier in self.notifiers:
            try:
                notifier.notify(title, message)
            except Exception as exc:  # noqa: BLE001  report all channels, then raise
                failures.append(f"{type(notifier).__name__}: {exc}")
        if failures:
            raise RuntimeError("notification failed: " + "; ".join(failures))


def build_notifier(channels: list[str]) -> Notifier:
    """Log always, plus the configured channels. Missing optional deps fail at startup."""
    factories = {"toast": ToastNotifier}
    return MultiNotifier([LogNotifier()] + [factories[c]() for c in channels])
