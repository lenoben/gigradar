"""Crash detection: did the previous scheduled run finish?

Every run writes `run_state.json` (next to the store) when it starts and marks it completed when it
ends, however it ends (exit 0, 75, 2 or 1). A hard death (access violation, kill, shutdown) leaves it
"started, not completed". The next run sees that and sends ONE warning; no more warnings until a run
completes again. A crash can't report itself, and a crash before the notifier is built (imports, config)
can't be reported by the next run either; this covers crashes from the search on.

State file: {"started": ISO time, "pid": int, "completed": bool, "warned": bool}.
A marker whose pid is still alive and that started less than STILL_RUNNING_FOR ago is a run in progress
(overlap), not a crash.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from gigradar.notify import Notifier

log = logging.getLogger("gigradar")

STATE_FILE = "run_state.json"
STILL_RUNNING_FOR = timedelta(minutes=15)  # the scheduled task's own time limit


@dataclass(frozen=True)
class RunState:
    started: str      # ISO time the run began
    pid: int
    completed: bool
    warned: bool      # a warning about an unfinished run was sent and no run has completed since


def state_path(db_path: Path) -> Path:
    return db_path.parent / STATE_FILE


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        # os.kill(pid, 0) would TERMINATE the process on Windows, so ask the kernel instead.
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours
    return True


def read_state(path: Path) -> RunState | None:
    """None when there is no usable marker (first run, or a corrupt file: logged, then replaced)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return RunState(started=str(raw["started"]), pid=int(raw["pid"]),
                        completed=bool(raw["completed"]), warned=bool(raw["warned"]))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("run state %s is unreadable (%s: %s); treating it as no previous run",
                    path, type(exc).__name__, exc)
        return None


def write_state(path: Path, state: RunState) -> None:
    """Atomic: a crash while writing leaves the old marker, never half of a new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(asdict(state)), encoding="utf-8")
    os.replace(temp, path)


def begin_run(path: Path, warn: Notifier, now: datetime, pid: int,
              alive: Callable[[int], bool]) -> RunState | None:
    """Check the previous run, warn once if it didn't finish, then mark this run as started.
    Returns the marker to hand to end_run, or None if it couldn't be written (logged; the run goes on,
    it just can't be detected if it dies)."""
    previous = read_state(path)
    warned = previous.warned if previous is not None else False
    if previous is not None and not previous.completed:
        if _still_running(previous, now, alive):
            log.warning("previous run (pid %d, started %s) is still running; not treating it as a crash",
                        previous.pid, previous.started)
        elif not warned:
            warned = _warn(warn, previous)
        else:
            log.warning("previous run (started %s) did not finish; already warned", previous.started)
    state = RunState(started=now.isoformat(), pid=pid, completed=False, warned=warned)
    try:
        write_state(path, state)
    except OSError:
        log.exception("could not write run state %s; this run can't be crash-checked", path)
        return None
    return state


def end_run(path: Path, state: RunState | None) -> None:
    """Mark this run completed (and re-arm the warning). Call it however the run ends."""
    if state is None:
        return
    try:
        write_state(path, RunState(started=state.started, pid=state.pid, completed=True, warned=False))
    except OSError:
        log.exception("could not mark run state %s completed; the next run will report a crash", path)


def _still_running(state: RunState, now: datetime, alive: Callable[[int], bool]) -> bool:
    try:
        started = datetime.fromisoformat(state.started)
    except ValueError:
        return False
    return now - started < STILL_RUNNING_FOR and alive(state.pid)


def _warn(warn: Notifier, previous: RunState) -> bool:
    """True once the warning went out; False (retry next run) if no channel could deliver it."""
    try:
        started = datetime.fromisoformat(previous.started).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        started = previous.started
    title = "gigradar: the previous run did not finish"
    message = (f"The run that started at {started} never completed (crash, kill or shutdown). Its jobs "
               "are not marked seen and are alerted next time they are listed. Check logs/gigradar.log and "
               "the Windows Application log. No further warning until a run completes.")
    try:
        warn.notify(title, message)
    except Exception:  # noqa: BLE001  a failing channel must not stop the run; retried next run
        log.exception("could not send the crash warning; will retry next run")
        return False
    return True
