# Desktop app (Tauri)

A small desktop shell around the packaged helper program (see `docs/app-core.md` and `docs/packaging.md`), for people
who should never see a terminal: an onboarding wizard, then a Home screen with Status, Settings and Jobs, a tray icon,
and notifications that open the job they are about.

```
app/
  src/            Svelte 5 + Vite (no SvelteKit): screens in src/steps and src/home, logic in src/lib, all texts in src/lib/strings.ts
  src-tauri/      Rust: runs the helper, streams its JSON Lines to the window, tray, gigradar:// links
    windows/      installer hooks (link registration, uninstall) and the installer's texts
  e2e/drive.mjs   drives the real app over WebView2's DevTools port and saves a screenshot of every screen
  build.ps1       helper build + Tauri NSIS installer
```

## The name

The product is called **Gigradar** (capital G) wherever a person reads it. One constant per language, and a test
(`tests/test_branding.py`) that fails when they differ: `APP_DISPLAY_NAME` in `gigradar/branding.py`, in
`app/src/lib/strings.ts` and in `app/src-tauri/src/lib.rs`, plus `productName`, `mainBinaryName` and the window title in
`tauri.conf.json`. It shows in the window title, the tray, toasts, the Telegram messages, the Status screen, the
installer, the Start-menu entry, Task Manager and "Apps".

**Toasts under the app's identity.** The installer puts the application id `app.gigradar.desktop` (Tauri's
`identifier`) on the Start-menu shortcut. The helper sends its toasts under that id when it runs from the installed
app, so Windows shows the shortcut's name ("Gigradar") and icon instead of "Python" or a file name. If the shortcut is
missing (a copied folder), the helper registers the same id with a name and icon for the current user. From a source
checkout the toast is sent under the plain name "Gigradar".

## How the pieces talk

The window never touches files or the network. Every action is a Rust command (`run_command`, `telegram_connect`,
`cancel_command`, `app_info`, and a few for the window itself) that runs `gigradar.exe` from the bundled helper folder
and sends each JSON line of its output to the window through a Tauri `Channel`. The Rust side builds the helper's
command line itself from a fixed list of commands (`invocation_for` in `lib.rs`); the window cannot pass free-form
arguments, and job ids, run time stamps, settings sections and label values are checked there.

**The helper ships as a resource folder, not as `externalBin`.** `externalBin` wants one executable per target;
PyInstaller's one-folder build is an `.exe` plus an `_internal` folder next to it. A one-file build would unpack
158 MB on every start (and every 30 minutes from the scheduler), so the folder is bundled as resources
(`bundle.resources` maps `src-tauri/resources/` to the install folder, `build.ps1` copies the helper into
`resources/sidecar/`) and started by path. Overrides for testing: `GIGRADAR_SIDECAR_DIR` (folder with `gigradar.exe`).

**Telegram bot token:** typed into a password field, emptied from the field the moment Connect is pressed, passed to
Rust as one argument, written to the helper's stdin, and kept in Rust memory only until it is saved with the rest of
the settings (the setup, or Settings > Notifications > Change bot, which asks Rust to add the token it holds to the
helper's input; the window only says "use the stored token"). The window state, the saved draft, the console and the
Rust side's output never hold it: the Rust runner replaces any secret it was given with `<redacted>` in whatever the
helper prints, neither side writes logs, and tests assert all of that (`src/lib/secrets.test.ts`,
`src-tauri/src/*.rs`, `tests/test_app_m3.py`). Cancel, and a successful save, make Rust forget the token.

## Screens

**First start:** Welcome, Profile, Preferences, Searches (hourly / fixed / both, up to 5), Notifications, Upwork
check, Model download, Finish (with a Close button). On start the app runs `doctor --offline`: only a MISSING config
opens the wizard; once a config file exists the app always opens on Home (a damaged one shows up there, with "Set up
again", which backs the old files up).

**Home** has three tabs.

- **Status:** a summary line ("All set - next check at HH:MM", or "N things need attention"), one compact row per check
  with an equal-width pill (icon + OK / Check / Problem, so colour is not the only signal), a plain-language hint and
  a Fix button where the app can fix it itself (set up again, get WebView2, **Check Upwork**, download the model,
  schedule the checks).
- **Settings:** Profile, Preferences, Searches, Notifications, Schedule, Storage. Each section opens with the values saved now,
  edits only itself, validates like the wizard, and has Save / Cancel. Saving writes the whole configuration through
  the same code as the setup (so an invalid change changes nothing and the replaced files are backed up first) and
  never runs the onboarding. Telegram shows "Connected as @botname"; a new token is asked for only after "Change bot".
  Schedule: every 30 minutes, 1, 2 or 4 hours (never below 30), plus optional quiet hours; saving re-registers the
  scheduled task. Storage: how long jobs are kept (see "Keeping jobs"). After saving a Searches section with a new or
  changed search, one line says "Jobs already posted for this search are added without notifications." (a search is
  known by its name, which comes from its keywords and pay type, so a changed search starts again silently).
  A configuration with hand-edited entries the app does not know is refused ("edit the file") instead
  of being rewritten without them.
- **Jobs:** the latest jobs with score, budget, posted time, Open on Upwork and 👍 / 👎 (stored as labels, the same ones
  `python -m gigradar.label` uses). When the configuration has a minimum score (`[scoring] min_score`), jobs scored
  below it are hidden; the switch "Show jobs below my minimum score" (off by default) shows them dimmed with a "below
  minimum" tag. Open on Upwork works for `https://www.upwork.com/*` only (the shell's capability
  `opener:allow-open-url` is scoped to it, plus BotFather and the WebView2 download page): without a scope Tauri refuses
  every link. A job opens as a card: title, score, budget, posted time, the profile section it
  fits best, the skills that matched, a short description, Open on Upwork, 👍 / 👎.

**Tray icon** (Open / Run check now / Quit). Closing the window (the X or the Close button) only hides it; the app stays
in the tray; the first time ever, a dialog says so ("Gigradar is still running in the tray (bottom right). Your checks keep running even if you quit."; remembered in `tray_hint_shown` in the data folder). "Run check now" is `run-once --ignore-quiet-hours`. "Quit" ends the tray app; the scheduled checks run
without it either way. A second start (a click on a notification, the Start menu) reaches the running copy instead of
opening another.

### Notifications open the job

A toast carries a link: one new job -> `gigradar://job/<id>` (its card), several -> `gigradar://jobs?run=<time>` (the
list of that check; jobs held back as below the minimum score are in it only behind the switch), "Upwork needs a quick check" ->
`gigradar://status`. The installer registers the `gigradar` scheme for the current user (HKCU), so a click starts the
app on that screen when it is closed, and a second start passes the link to the open app. The Rust side accepts only
these three shapes with strict characters (`links.rs`). Telegram messages are unchanged (they link to Upwork).

### When Upwork's security check has expired

The hidden browser waits 20 s for the visitor token. If there is none (the Cloudflare clearance in the app's browser
profile is gone), the run **does not open a window by itself**. It sends ONE notification (toast and Telegram: "Upwork
needs a quick check - open Gigradar and click Check Upwork"), leaves a marker next to the job history and exits with
code 75 (nothing is lost: the jobs stay unseen). While the marker exists, later blocked runs log one line
("Upwork still needs a quick check ...") and stop; nobody is nagged and no window pops up. Status then shows
"Upwork access" with a **Check Upwork** button, which opens the visible Upwork window (`upwork-check`, 5 minutes for the
click). The marker is removed when that check succeeds, or when a scheduled run gets through again by itself. Only
Upwork turning the run away counts (no token, a rejected token, a challenge page, HTTP 403/429/503); a timeout or a
network error does not. This replaces the earlier behaviour (window plus notification every 30 minutes).

### Scores for every job

Every stored job gets an embedding score, not only the ones announced. After the alerts of a run, jobs that have none
are scored: the jobs a search's first run seeded silently, and any whose scoring failed or ran out of its 60 s budget
(`score_backlog`: newest first, at most 300 per run, 120 s per run, saved after every chunk of 10, so the rest
simply follows in the next runs; nothing is announced). Runs with nothing to score do not even load the model.

### Below the minimum score

`[scoring] min_score` (0 = no minimum) keeps low scores out of the notifications. The run still says so: with 3 new jobs
of which 2 are below, the toast and Telegram say "3 new jobs" and "2 below your minimum score" and list only the
others (when none pass, nothing is sent, as before). The toast then opens the list of that check, not a single card.
The setup writes `min_score = 20`. Settings > Preferences > "Minimum match score" (0–80) changes it. A configuration
without the key shows 20 there but checks with no minimum until Preferences are saved once.

### Keeping jobs

`[retention]` (written by the setup as 30 days, kept rated jobs; edited in Settings > Storage: 1 week / 2 weeks /
1 month / forever and "Keep the jobs I rated"): at the end of each run, jobs first seen longer ago are **pruned**: job
text, scores, embeddings and search links are deleted. What stays is a marker (id and date, table `expired_jobs`, store
schema v3) for 60 days, so a pruned job that Upwork still lists never alerts again. Jobs with a 👍/👎 are kept whole
while "Keep the jobs I rated" is on; when it is off they go too, with their label. A configuration **without** a
`[retention]` table never prunes anything (the development checkout's), and the Storage section then shows "forever".

### Schedule and quiet hours

`[schedule]` in `gigradar.toml`: `interval_minutes` (the task's interval) and `quiet_start` / `quiet_end` (local time,
may cross midnight). A scheduled run inside the quiet hours exits at once (no browser, no request, no notification);
the jobs wait for the first run afterwards. A check started by hand (tray, the first check of the setup) ignores them.

## The scheduled task

`\Gigradar Desktop\checks` (folder `Gigradar Desktop`, task `checks`): `gigradarw.exe run-once --config <data>\gigradar.toml`,
every N minutes, interactive logon, no overlap. It is the app's own: the source checkout's task is `\gigradar\gigradar-watch`
(`scripts/windows/gigradar-task.ps1`), and the two can never replace each other. `GIGRADAR_TASK_NAME` renames the task
(not the folder) for tests.

## Data

`%LOCALAPPDATA%\Gigradar Data` (settings, `.env`, job history, logs, scoring model, browser profile), or
`GIGRADAR_HOME`. This is not the install folder (`%LOCALAPPDATA%\Gigradar`) and not the source checkout's folder
(`%LOCALAPPDATA%\gigradar`), so an uninstall can never touch either by mistake.

## Uninstall

The uninstaller removes the app's own scheduled task `\Gigradar Desktop\checks` and the `gigradar://` registration, and
nothing else. The confirm page has a box "Also delete my data (settings, job history and the scoring model)", unticked
by default (also in a silent uninstall); ticked, it deletes `%LOCALAPPDATA%\Gigradar Data`. An update (the installer
running the old uninstaller with `/UPDATE`) keeps the task, the data and the link. The hooks are
`src-tauri/windows/hooks.nsh`; `windows/English.nsh` is Tauri's text file with the one line for that box changed.

## Environment (testing)

| Variable | Effect |
|---|---|
| `GIGRADAR_HOME` | the app home (config, data, logs, models, browser profile) |
| `GIGRADAR_TASK_NAME` | scheduled task name inside `\Gigradar Desktop\` (default `checks`); a test must not use the real one |
| `GIGRADAR_SIDECAR_DIR` | use the helper from this folder |

## Build and test

```powershell
cd app; npm ci
npm test                 # vitest (screens, settings, jobs, token handling) ; npm run check = svelte-check
cd src-tauri; cargo test # argument building, links, secret scrubbing, a fake helper
powershell -ExecutionPolicy Bypass -File app\build.ps1   # helper + installer (CPU heavy: not near :23 / :53)
npm run dev              # in a browser: a mock helper, every screen; ?demo=1&step=4&fail=window_failed:2&tick=100
                         #   ?config=1&tab=settings | ?config=1&link=job:~01aa | ?config=1&recheck=1
```

`bundle.windows.webviewInstallMode` is `embedBootstrapper` (silent): the installer carries Microsoft's 1.8 MB
bootstrapper and installs the WebView2 runtime when the PC lacks it. The installer is per-user (no administrator).
