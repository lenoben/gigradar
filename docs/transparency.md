# Transparency: what Gigradar does, contacts and stores

Gigradar is a program that runs on your PC and reads a website for you. This page says exactly what it does, so you
can decide whether you are comfortable with it. The code is in this repository; where a statement is checked by a
test or by the code, the file is named.

## What it does

- Every N minutes (30 by default, never less) a scheduled task starts the helper program. It opens a **hidden
  browser page** (Microsoft WebView2) on `https://www.upwork.com/` and, from inside that page, calls Upwork's
  **public visitor search** once per search (at most 5 searches, 30 jobs each by default).
- New jobs are stored on your PC, scored against your profile **on your PC**, and announced by a Windows toast and,
  if you set it up, a Telegram message.
- It only reads. It never logs in to Upwork, never applies to a job, never sends a proposal, never posts anything.

## Everything it connects to

| Where | Why | What is sent |
|---|---|---|
| `www.upwork.com` (from your PC, through the hidden WebView2 page) | the job searches | your search keywords and filters, like a visitor typing them. Upwork sees your home IP address and a normal browser. |
| `huggingface.co` | the scoring model, **once** (67 MB, the "Model" step) | a plain file download; no profile text, no account |
| `api.telegram.org` | **only if you turn Telegram on** | the job messages (title, score, pay, skills, the first ~300 characters of the description, the Upwork link) to **your** chat, through **your** bot |
| `go.microsoft.com` | **only if** WebView2 is missing, during the install | Microsoft's own installer download |
| your web browser | when you click "Open on Upwork" | opens an `https://www.upwork.com/...` link. The app may open only that, `t.me/BotFather` and Microsoft's WebView2 page (`app/src-tauri/capabilities/default.json`). |

That is the whole list. In particular there is **no telemetry, no analytics, no crash reporting, no automatic
update, and no call to any AI service.** The app window itself cannot make network requests: its content security
policy allows only the local bridge to the Rust side (`app/src-tauri/tauri.conf.json`).

## What it stores, and where

In `%LOCALAPPDATA%\Gigradar Data` (or the folder in `GIGRADAR_HOME`), plain files you can open:

| File or folder | Content |
|---|---|
| `gigradar.toml` | your searches, notification channels, schedule, minimum score, retention. **No secrets.** |
| `profile.md` | the profile text you wrote |
| `.env` | the Telegram bot token and chat id (if used). Only here. |
| `data/gigradar.db` | SQLite: jobs you were shown (text, skills, budget), their scores, your 👍/👎, and "seen" markers |
| `logs/` | what each check did; secrets are not logged |
| `models/` | the scoring model |
| browser profile | the cookies of the hidden browser page, including Cloudflare's clearance for Upwork |
| `backups/` | copies of config files the app replaced; a copy of the database is made next to it before a schema upgrade |

**Retention.** The setup keeps jobs for 30 days (Settings → Storage: 1 week, 2 weeks, 1 month or forever; jobs you
rated can be kept whole). Older jobs lose their text, scores and embeddings; only an id and a date remain for 60 days,
so a removed job that is still listed on Upwork never alerts again.

**Your profile never leaves the PC for scoring.** The embedding model runs locally (ONNX) and the profile text is not
sent anywhere. Telegram receives only the job messages above, not your profile.

## How secrets are handled

- The Telegram bot token is typed into a password field, passed to the Rust side and the helper over standard input,
  and written to `.env` and nowhere else. It is not kept in the window state, the saved draft, the console or the
  logs, and the runner replaces it with `<redacted>` in any output. Tests: `app/src/lib/secrets.test.ts`,
  `tests/test_app_m3.py`.
- `gigradar.toml` is refused if it contains a token.
- Notification links (`gigradar://job/...`, `gigradar://jobs?run=...`, `gigradar://status`) are accepted only in those
  three shapes with strict characters (`app/src-tauri/src/links.rs`). The window can start only a fixed list of helper
  commands (`invocation_for` in `app/src-tauri/src/lib.rs`).

## Limits and honest caveats

- **Terms of Service.** Upwork's terms may forbid automated access. Gigradar uses only the public visitor endpoint,
  without an account, at personal volume (a few requests every 30 minutes), but that does not make it permitted. You
  use it at your own risk. Gigradar is not affiliated with Upwork.
- **It can stop working.** Upwork or Cloudflare can change anything at any time. When Upwork's security check
  expires, Gigradar says so once and waits for you; it does not open windows by itself or nag.
- **Unsigned installer.** The installer has no code-signing certificate, so Windows warns about an unknown publisher.
  Build it from source if you want to be sure what you run ([install-and-build.md](install-and-build.md)).
- **Scores are an estimate.** A small model compares texts. On the author's own ratings it ranked a liked job above a
  disliked one about three times in four (AUC about 0.76). It can be
  wrong, which is why the minimum score is a setting and jobs below it stay visible behind a switch.
- **You see what a visitor sees.** No applicant counts, connects or client history.
- **No guarantee of delivery.** Checks run only while the PC is on and you are logged in. A crashed check is reported
  once at the next run ([windows-scheduler.md](windows-scheduler.md)).

## Verify it yourself

- Read `gigradar/` (the Python helper), `app/src-tauri/src/` (the Rust shell) and `app/src/` (the screens). Network
  calls are in `gigradar/webview_search.py`, `gigradar/telegram.py`, `gigradar/embed.py` and `gigradar/webview2.py`.
- Run the tests: `python -m unittest discover -s tests`, `npm test` and `cargo test` (in `app/src-tauri`).
- Watch it with a firewall or Process Monitor: apart from the table above there should be nothing.
- Open the files in the data folder; they are plain text and SQLite.
