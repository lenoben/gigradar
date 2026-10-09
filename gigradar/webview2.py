"""WebView2 runtime detection: gigradar's browser window needs Microsoft's WebView2 runtime.

pywebview silently falls back to the old Internet Explorer engine (MSHTML) when the runtime is missing, and
that engine cannot do the job (no cookie access, no modern fetch). So the check happens here, before any
window is created, and a missing runtime stops the run at once with one clear message.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from gigradar.tokens import StopRun

EVERGREEN_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"   # Microsoft's Evergreen Bootstrapper (~2 MB)
INFO_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

# EdgeUpdate client ids: the stable runtime, then the preview channels pywebview also accepts.
CLIENT_KEYS = ("{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",
               "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}", "{65C35B14-6C1D-4122-AC46-7148CC9D6497}")
MIN_VERSION = (86, 0, 622, 0)     # the oldest runtime pywebview supports

# (hive, subkey) -> the `pv` (product version) value, or None when the key or value does not exist
ReadFn = Callable[[str, str], str | None]


class WebView2Missing(StopRun):
    """The WebView2 runtime is not installed: no window can be opened."""


def read_registry(hive: str, subkey: str) -> str | None:
    import winreg

    root = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive]
    try:
        with winreg.OpenKey(root, subkey) as key:
            return str(winreg.QueryValueEx(key, "pv")[0])
    except OSError:      # key or value not there: not installed
        return None


def parse_version(text: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in text.strip().split("."))
    except ValueError:
        return None


def installed_version(read: ReadFn) -> str | None:
    """The newest installed WebView2 runtime version (per-user or machine-wide), or None."""
    if sys.platform != "win32":
        return None
    best: tuple[tuple[int, ...], str] | None = None
    for key in CLIENT_KEYS:
        for hive, subkey in (("HKCU", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}"),
                             ("HKLM", rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{key}"),
                             ("HKLM", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}")):
            text = read(hive, subkey)
            version = parse_version(text) if text else None
            if version is not None and version >= MIN_VERSION and (best is None or version > best[0]):
                best = (version, text.strip())
    return best[1] if best else None


def missing_message() -> str:
    return ("The Microsoft Edge WebView2 runtime is not installed on this PC, so the browser window cannot open. "
            f"Install it (free, about 2 minutes), then try again: {EVERGREEN_URL}")


def require(read: ReadFn) -> str:
    """The installed version; raises WebView2Missing (one line, no retries) when there is none."""
    version = installed_version(read)
    if version is None:
        raise WebView2Missing(missing_message())
    return version
