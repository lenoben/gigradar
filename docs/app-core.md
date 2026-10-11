# App core: per-user data folder and JSON command line

The groundwork for a desktop app (a UI that shells out to gigradar). Nothing here changes how a git
checkout with its own `gigradar.toml` runs.

## Where things live (the app home)

Config, `.env`, `profile.md`, `data/` (the store), `logs/`, `models/` and `webview2/` live in one folder,
the **app home**. It is, in this order:

1. `GIGRADAR_HOME`, if set;
2. the repository root, when running from source and a `gigradar.toml` exists there (an existing checkout
   keeps working unchanged; a packaged app never does this);
3. the packaged app: `%LOCALAPPDATA%\Gigradar Data` (its own folder: it is neither the install folder `%LOCALAPPDATA%\Gigradar` nor the
   source checkout's `%LOCALAPPDATA%\gigradar`); a source run without a repo config: `%LOCALAPPDATA%\gigradar`.

`--config PATH` (every command, and `gigradar.watch`) uses another `gigradar.toml`; its `.env` is read from the
same folder. Relative paths inside a config resolve against that folder.

## The command line

    python -m gigradar.cli <command> [--config PATH] [--json] [options]

Output is JSON Lines on stdout: any number of `{"event": ...}` progress lines, then exactly one result line
`{"ok": true|false, "command": ..., ...}`. A failure has `"error": {"code", "message"}`. Logs never go to
stdout. Exit codes: 0 ok, 1 failed, 2 usage error; `run-once` returns the watcher's code (0 ok, 1 error,
2 config, 75 stopped early), so a scheduler keeps its meaning.

The Telegram bot token is never printed or logged; whatever is printed has every known secret replaced by
`<redacted>`.

| Command | What it does |
|---|---|
| `setup-apply --answers FILE\|- [--home DIR] [--force]` | Writes `gigradar.toml`, `.env`, `profile.md` from a JSON answers file (below). Validates with the real config loader first; refuses to overwrite without `--force`, which copies the old files to `backups/<UTC time>/` first. |
| `telegram-connect [--chat-id ID]` | Reads the bot token from **stdin** (one line). `getMe`, finds the chat id from `getUpdates` (write the bot a message first), sends a test message. Returns `bot`, `chat_id`, `chat_name`; never the token. |
| `upwork-check` | Opens the WebView2 window **visibly** once, runs the first search once, and marks what it found as seen (no alerts, no scoring). Solve Cloudflare's check if it shows; the window closes by itself. Needs the WebView2 runtime: without it the command stops at once with `webview2_missing` (message includes Microsoft's download link). A slow browser start (`window_failed`) opens a new window and a page that is not ready (`fetch() gave no result`) retries in the same window, each at most 3 times with a 2 s / 5 s pause; `retry` lines report it and the result has `attempts`. |
| `run-once` | One watch run, exactly what the scheduled task does. Logs to `<home>/logs/gigradar.log`. |
| `jobs [--limit N] [--run STAMP]` | The latest stored jobs with their embedding score, `reason`, the profile `section` and `matched` skills parsed from it, and your label. `--run` lists only the jobs one run announced (the time stamp a digest notification link carries; jobs below `min_score` are left out). |
| `job --job-id ID` | One stored job as in `jobs`, plus the start of its description: the card a notification opens. |
| `label --job-id ID --value up\|down\|clear` | 👍 / 👎 / remove. |
| `doctor [--offline] [--task-name NAME]` (default `checks`, in the folder `Gigradar Desktop`) | `OK` / `WARN` / `FAIL` per check with a one-line `fix`: config, profile, Telegram token, Telegram API (`getMe`), chat id, WebView2 runtime (installed version, FAIL with the download link when missing), WebView2 profile folder, **Upwork access** (WARN while a scheduled run was turned away and waits for `upwork-check`), model cached, store, scheduled task and its last result, recent `ERROR` log lines (identical repeats collapse into one entry with a count; see `details`). `--offline` skips the network check. Exit 1 only on `FAIL`. |
| `notify-test [--channel toast]` | Sends one sample job through every configured channel (toast, Telegram): a "send a test" button. |
| `settings-get` | The current settings, section by section (profile, preferences, searches, notifications, schedule), without any secret; the Telegram bot only as `connected` and its name. Refuses (`unsupported_config`) a config with entries the app did not write. |
| `settings-save --section S [--task-name N] [--exe PATH]` | Replaces ONE section with the JSON on stdin and writes everything through the same checks as `setup-apply` (backup first). `notifications` may carry a new `bot_token`; `schedule` takes `interval_minutes` 30 / 60 / 120 / 240 and optional `quiet_start` / `quiet_end` and re-registers the scheduled task. |
| `task-register [--interval N]` / `task-unregister` | The app's scheduled task `\Gigradar Desktop\checks` (without `--interval`: the saved `[schedule]`). |
| `selftest --order preload\|onnx-first\|toasts-first [--model-dir DIR]` | Checks the native-library load order scoring depends on; for packaged builds (see packaging.md). A crash is the answer, there is no JSON then. |
| `model-download` | Downloads the scoring model (about 67 MB; the total comes from the server's Content-Length, no percentage if unknown), then checks it loads offline. Progress lines: `start`, `progress` (`bytes`, `total_bytes`, `percent`), `verify`. |

### Answers file (`setup-apply`)

```json
{
  "searches": [{"name": "py", "query": "python", "job_type": "hourly", "tier": "expert"}],
  "notify": {"channels": ["toast", "telegram"]},
  "profile": {"markdown": "## Web\nWhat I build ...", "skills": ["Python"], "min_hourly": 40,
              "min_fixed": 1000, "tiers": ["expert"], "exclude_keywords": ["wordpress"], "constraints": "Remote only"},
  "scoring": {"min_score": 20},
  "schedule": {"interval_minutes": 30, "quiet_start": "22:00", "quiet_end": "07:00"},
  "telegram": {"bot_token": "...", "chat_id": "...", "bot_username": "my_bot"}
}
```

Only `searches` is required (at most 5); the keys inside a search and `profile` are those of `gigradar.example.toml`.
Send the file on stdin (`--answers -`) when it holds the bot token, so no secret touches the disk except `.env`.

## Several installs side by side

`scripts/windows/gigradar-task.ps1` takes `-TaskName` (default `gigradar-watch`) and `-ConfigPath` (default the
checkout's `gigradar.toml`), for every action. A test install needs its own of both; the task then runs that
config, and its data, `.env` and logs sit next to it.
