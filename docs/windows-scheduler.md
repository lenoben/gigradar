# Scheduling gigradar on Windows

Windows replacement for the README's cron example: a Task Scheduler task that runs
`python -m gigradar.watch` every 30 minutes while you're logged on.

## Prerequisites

```bash
py -3.12 -m venv .venv
.venv/Scripts/pip install curl_cffi -r requirements-webview.txt -r requirements-toast.txt
cp gigradar.example.toml gigradar.toml      # then edit your searches
.venv/Scripts/python -m gigradar.watch      # one manual run first: seeds the store silently
```

## Register / check / remove

From the repo root (no admin rights needed):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Register            # every 30 min
powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Register -IntervalMinutes 60
powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Run       # run the task now
powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Status
powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Unregister
```

The task is `\gigradar\gigradar-watch` in Task Scheduler. Re-running `-Register` replaces it.

**Testing the task command by hand:** use PowerShell, not Git Bash. Starting the venv's
`pythonw.exe` from Git Bash fails with "Python Launcher is sorry to say… stderr duplication
failed" (a Git Bash / venv launcher quirk), which says nothing about the task. Same command as the task:

```powershell
Start-Process -Wait -FilePath .venv\Scripts\pythonw.exe -WorkingDirectory . `
  -ArgumentList '-m gigradar.watch --config gigradar.toml --log-file logs\gigradar.log'
Get-Content logs\gigradar.log -Tail 20
```

Or trigger the registered task itself: `Start-ScheduledTask -TaskPath '\gigradar\' -TaskName gigradar-watch`.

## How it runs

- **Only when you're logged on.** The WebView2 search needs your desktop session, and so does
  the window gigradar shows if Cloudflare ever asks for a click.
- **No console window:** it runs `.venv\Scripts\pythonw.exe`.
- **Normal priority.** Task Scheduler's default (below normal, with low I/O priority) delayed
  startup by ~40 s and made WebView2 miss pywebview's 20 s window-start wait
  ("Main window failed to start"). The script registers the task at priority 4 (normal).
- **Log:** `logs\gigradar.log` (rotating, 1 MB × 3, gitignored).
- **No overlap, no bursts:** a still-running run makes the next start skip; a run missed while the
  PC was asleep starts once when it wakes. Minimum interval is 15 minutes.

## Exit codes (Task Scheduler "Last Run Result")

| Code | Meaning |
|---|---|
| 0 | ok |
| 75 (0x4B) | stopped early: no token / blocked. Not retried; the next scheduled run tries again |
| 2 | config error (`gigradar.toml` / `.env`) |
| 1 | any other error, see the log |
