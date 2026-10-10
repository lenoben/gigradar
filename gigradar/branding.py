"""What the app is called and how Windows knows it: the one place for names a user can see.

APP_DISPLAY_NAME is used wherever a person reads it (toasts, Telegram headers, the window title, the tray, the
installer). The same text is written in three other places that cannot import this module: `productName` and the window
title in app/src-tauri/tauri.conf.json, APP_DISPLAY_NAME in app/src/lib/strings.ts and in app/src-tauri/src/lib.rs.
tests/test_branding.py fails when they drift apart.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Callable
from pathlib import Path

APP_DISPLAY_NAME = "Gigradar"
# The Windows application identity (AppUserModelID) of the installed app. The installer puts it on the Start-menu
# shortcut, so a toast sent under this id shows the shortcut's name and icon. It is Tauri's `identifier`.
APP_ID = "app.gigradar.desktop"
LINK_SCHEME = "gigradar"          # gigradar://job/<id>, gigradar://jobs?run=<time>, gigradar://status

STATUS_LINK = f"{LINK_SCHEME}://status"


def job_link(job_id: str) -> str:
    """The link a toast carries for one job: clicking it opens (or focuses) the app on that job's card."""
    return f"{LINK_SCHEME}://job/{urllib.parse.quote(job_id, safe='~')}"


def run_link(run: str) -> str:
    """The link a digest toast carries: the jobs list, filtered to the jobs announced in that run (its time stamp)."""
    return f"{LINK_SCHEME}://jobs?run={urllib.parse.quote(run, safe='')}"


# The scheduled task of the desktop app. It has its own folder and name, so it can never replace the task of a
# source checkout (\gigradar\gigradar-watch, scripts/windows/gigradar-task.ps1).
TASK_FOLDER = "\\Gigradar Desktop\\"
TASK_NAME = "checks"

DATA_DIR_NAME = "Gigradar Data"   # %LOCALAPPDATA%\<this>: the packaged app's data (config, history, browser profile)

AUMID_KEY = r"Software\Classes\AppUserModelId"

log = logging.getLogger("gigradar")


def toast_identity(frozen: bool) -> str:
    """The id toasts are sent under: the installed app's AppUserModelID, or just the display name from a checkout."""
    return APP_ID if frozen else APP_DISPLAY_NAME


def link_handler_registered() -> bool:
    """Is gigradar:// registered for this user (the installer does it)? Without a handler a clicked toast would show
    Windows' "how do you want to open this" / Store dialog, so such a toast carries no link."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{LINK_SCHEME}\shell\open\command"):
            return True
    except (ImportError, OSError):
        return False


def write_registry_values(path: str, values: dict[str, str]) -> None:
    """Create the per-user registry key `path` and set string values (Windows only)."""
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def register_toast_identity(icon: Path | None, write: Callable[[str, dict[str, str]], None]) -> None:
    """Make Windows show 'Gigradar' (and the app icon) for toasts sent under APP_ID even when the installer's
    Start-menu shortcut is missing (a copied folder). Per-user registry only; a failure is logged, never raised."""
    values = {"DisplayName": APP_DISPLAY_NAME}
    if icon is not None and icon.is_file():
        values["IconUri"] = str(icon)
    try:
        write(AUMID_KEY + "\\" + APP_ID, values)
    except OSError as exc:
        log.warning("could not register the toast identity (toasts may show a technical name): %s", exc)
