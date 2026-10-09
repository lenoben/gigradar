"""Register / remove the Windows scheduled task that runs the packaged app's `run-once` every N minutes.

Same settings as scripts/windows/gigradar-task.ps1 (which serves a source checkout): interactive logon (WebView2 needs
the desktop session), normal priority (Task Scheduler's default priority made WebView2 start too slowly), never
overlapping itself, a 15 minute limit, and a missed run starts once when the PC is available again. No admin rights.
The task passes `--config`, so a test install with its own home and task name cannot touch another install.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

TASK_FOLDER = "\\gigradar\\"
TASK_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
MIN_INTERVAL, MAX_INTERVAL = 15, 1440
FIRST_RUN_DELAY = timedelta(minutes=1)

# argv of schtasks (without the program name) -> (exit code, combined output)
SchtasksFn = Callable[[list[str]], tuple[int, str]]


class TaskError(Exception):
    """Registering or removing the task failed. `code` is machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def run_schtasks(argv: list[str]) -> tuple[int, str]:
    done = subprocess.run(["schtasks", *argv], capture_output=True, text=True, timeout=60, check=False)
    return done.returncode, (done.stdout + done.stderr).strip()


def check_name(name: str) -> str:
    if not TASK_NAME.match(name):
        raise TaskError("invalid_task_name", "the task name may only contain letters, digits, . _ -")
    return name


def build_xml(exe: Path, config: Path, user: str, interval_minutes: int, start: datetime) -> str:
    if not MIN_INTERVAL <= interval_minutes <= MAX_INTERVAL:
        raise TaskError("invalid_interval", f"the interval must be {MIN_INTERVAL} to {MAX_INTERVAL} minutes")
    arguments = f'run-once --config "{config}"'
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>gigradar: Upwork job watcher, every {interval_minutes} min.</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition><Interval>PT{interval_minutes}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>{start:%Y-%m-%dT%H:%M:%S}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowHardTerminate>true</AllowHardTerminate>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(str(exe))}</Command><Arguments>{escape(arguments)}</Arguments><WorkingDirectory>{escape(str(exe.parent))}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def register(task_name: str, exe: Path, config: Path, interval_minutes: int, now: datetime, user: str,
             schtasks: SchtasksFn) -> dict:
    check_name(task_name)
    if not exe.is_file():
        raise TaskError("exe_missing", f"{exe} does not exist")
    xml = build_xml(exe, config, user, interval_minutes, now + FIRST_RUN_DELAY)
    handle, name = tempfile.mkstemp(suffix=".xml", prefix="gigradar-task-")
    try:
        with os.fdopen(handle, "w", encoding="utf-16") as out:
            out.write(xml)
        code, output = schtasks(["/create", "/tn", TASK_FOLDER + task_name, "/xml", name, "/f"])
    finally:
        Path(name).unlink(missing_ok=True)
    if code != 0:
        raise TaskError("schtasks_failed", f"could not register the task: {output}")
    return {"task_name": task_name, "interval_minutes": interval_minutes, "runs": str(exe), "config": str(config)}


def unregister(task_name: str, schtasks: SchtasksFn) -> dict:
    check_name(task_name)
    code, _ = schtasks(["/query", "/tn", TASK_FOLDER + task_name])
    if code != 0:
        return {"task_name": task_name, "removed": False}
    code, output = schtasks(["/delete", "/tn", TASK_FOLDER + task_name, "/f"])
    if code != 0:
        raise TaskError("schtasks_failed", f"could not remove the task: {output}")
    return {"task_name": task_name, "removed": True}


def current_user(environ: dict[str, str]) -> str:
    name = environ.get("USERNAME", "")
    domain = environ.get("USERDOMAIN", "")
    return f"{domain}\\{name}" if domain else name
