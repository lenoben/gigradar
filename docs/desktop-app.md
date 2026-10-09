# Desktop app (Tauri)

A small desktop shell around the packaged helper program (see `docs/app-core.md` and `docs/packaging.md`), for people
who should never see a terminal: an onboarding wizard and a status page.

```
app/
  src/            Svelte 5 + Vite (no SvelteKit): screens in src/steps, logic in src/lib, all texts in src/lib/strings.ts
  src-tauri/      Rust: runs the helper, streams its JSON Lines to the window
  e2e/drive.mjs   drives the real app over WebView2's DevTools port and saves a screenshot of every screen
  build.ps1       helper build + Tauri NSIS installer
```

## How the pieces talk

The window never touches files or the network. Every action is a Rust command (`run_command`, `telegram_connect`,
`cancel_command`, `app_info`) that runs `gigradar.exe` from the bundled helper folder and sends each JSON line of its
output to the window through a Tauri `Channel`. The Rust side builds the helper's command line itself from a fixed
list of commands (`invocation_for` in `lib.rs`); the window cannot pass free-form arguments.

**The helper ships as a resource folder, not as `externalBin`.** `externalBin` wants one executable per target;
PyInstaller's one-folder build is an `.exe` plus an `_internal` folder next to it. A one-file build would unpack
158 MB on every start (and every 30 minutes from the scheduler), so the folder is bundled as resources
(`bundle.resources` maps `src-tauri/resources/` to the install folder, `build.ps1` copies the helper into
`resources/sidecar/`) and started by path. Overrides for testing: `GIGRADAR_SIDECAR_DIR` (folder with `gigradar.exe`).

**Telegram bot token:** typed into a password field, emptied from the field the moment Connect is pressed, passed to
Rust as one argument, written to the helper's stdin, and kept in Rust memory only (needed again to write `.env`
with the rest of the settings). The window state, the saved draft, the console and the Rust side's output never hold
it: the Rust runner replaces any secret it was given with `<redacted>` in whatever the helper prints, neither side
writes logs, and tests assert all of that (`src/lib/secrets.test.ts`, `src-tauri/src/*.rs`).

## Screens

Welcome, Profile, Preferences, Searches (hourly / fixed / both, up to 5), Notifications, Upwork check, Model
download, Finish (with a Close button); then Home, which is the Status page for now: a summary line ("All set - next
check at HH:MM" from the scheduled task, or "N things need attention"), one compact row per check with an equal-width
pill (icon + OK / Check / Problem, so colour is not the only signal), a plain-language hint and a Fix button where the
app can fix it itself. On start the app runs `doctor --offline`: only a MISSING config opens the wizard; once a config
file exists the app always opens on Home (a damaged one shows up there, with "Set up again", which backs the old files
up).

### What a scheduled run does when Upwork's security check has expired (today)

The hidden window waits 20 s for the visitor token. Without it (the Cloudflare clearance is gone) the run shows the
window, sends "gigradar: click needed" to every channel (log, toast, Telegram) and waits 5 minutes for a click. If
nobody clicks, the run stops without searching: log line "run stopped before searching: no Upwork token...", exit code
75, nothing lost (jobs stay unseen). Every later run (30 minutes) repeats this, window and notification included:
there is no throttling yet. Home shows the task result 75 as a warning. M3 replaces this with one notification and a
Fix button.

The settings are written when leaving the Notifications step (`setup-apply`), because the Upwork check needs them.
Going back and forward rewrites them without making another backup. Finish registers the scheduled task
(`task-register`: `gigradarw.exe run-once --config <home>\gigradar.toml`, every 30 minutes), runs one check and lists
the latest jobs.

## Settings through the environment (testing)

| Variable | Effect |
|---|---|
| `GIGRADAR_HOME` | the app home (config, data, logs, models, browser profile) |
| `GIGRADAR_TASK_NAME` | scheduled task name (default `gigradar-watch`); a test must not use the real one |
| `GIGRADAR_SIDECAR_DIR` | use the helper from this folder |

## Build and test

```powershell
cd app; npm ci
npm test                 # vitest (screens, token handling) ; npm run check = svelte-check
cd src-tauri; cargo test # argument building, secret scrubbing, a fake helper
powershell -ExecutionPolicy Bypass -File app\build.ps1   # helper + installer (CPU heavy: not near :23 / :53)
npm run dev              # in a browser: a mock helper, every screen; ?demo=1&step=4&fail=window_failed:2&tick=100
```

`bundle.windows.webviewInstallMode` is `embedBootstrapper` (silent): the installer carries Microsoft's 1.8 MB
bootstrapper and installs the WebView2 runtime when the PC lacks it. The installer is per-user (no administrator).
The product name is "Gigradar Desktop" so the install folder cannot collide with the data folder
`%LOCALAPPDATA%\gigradar`.
