"""Does Upwork need a quick check by hand? (Cloudflare's clearance in the app's browser profile has expired.)

A scheduled run that cannot get through says so ONCE (toast + Telegram: "open Gigradar and click Check Upwork") and
leaves a small marker file next to the job history. While the marker exists, later blocked runs only log one line
and stop; no window opens by itself and nobody is nagged. The marker is removed when a run gets through again, or
when the app's own Upwork check succeeds.

The marker holds only the time it was set. It is a convenience: a missing or damaged file means "no check needed".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger("gigradar")

MARKER_NAME = "upwork_check_needed.json"


@dataclass(frozen=True)
class RecheckState:
    needed: bool
    since: str | None


def marker_path(db_path: Path) -> Path:
    return db_path.parent / MARKER_NAME


def read(path: Path) -> RecheckState:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return RecheckState(False, None)
    except OSError as exc:
        log.warning("cannot read %s: %s", path, exc)
        return RecheckState(False, None)
    try:
        since = json.loads(text).get("since")
    except (ValueError, AttributeError):
        since = None   # damaged marker: the check is still needed, just without a time
    return RecheckState(True, since if isinstance(since, str) else None)


def mark_needed(path: Path, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"since": now.isoformat()}), encoding="utf-8")


def clear(path: Path) -> bool:
    """Remove the marker; True when there was one."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
