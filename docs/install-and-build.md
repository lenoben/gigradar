# Install, build and run Gigradar

Three ways, from easiest to most hands-on:

1. [Install the desktop app](#1-install-the-desktop-app) (Windows, the installer)
2. [Build the installer yourself](#2-build-the-installer-yourself)
3. [Run from a source checkout](#3-run-from-a-source-checkout) (no app, scheduled with Task Scheduler)

What each needs is in [requirements.md](requirements.md). What Gigradar contacts and stores is in
[transparency.md](transparency.md).

## 1. Install the desktop app

1. Run `Gigradar_<version>_x64-setup.exe`. It installs **for your user** (no administrator) into
   `%LOCALAPPDATA%\Gigradar`, adds a Start-menu entry "Gigradar", registers the `gigradar://` link that
   notifications use, and installs the Microsoft WebView2 runtime if the PC does not have it.
2. The installer is **not code-signed**. Windows SmartScreen may show "Windows protected your PC": *More info →
   Run anyway*. Only do that for an installer you built yourself or got from someone you trust.
3. Start **Gigradar**. The wizard asks for:
   - a short description of what you do (your profile; used only for scoring, on your PC),
   - optional skills, lowest rates, and anything a job must fit (e.g. "remote only"),
   - one to five searches,
   - how to be notified: Windows toast, and/or Telegram (create a bot with @BotFather and paste its token; the app
     finds your chat after you send the bot a message),
   - **Check Upwork**: a one-time check that Upwork lets the app's hidden browser page in; if Upwork wants a click,
     a window opens once,
   - the scoring model download (67 MB, once).
4. Finish. The app registers the scheduled task `\Gigradar Desktop\checks` and runs the first check. Jobs that
   already exist are added **silently**; you are notified about jobs that appear afterwards.

**Update:** run the newer installer over the old one. Settings, history and the scheduled task are kept.

**Where things are:** program in `%LOCALAPPDATA%\Gigradar`; settings, `.env` (Telegram token), job history, logs,
model and browser profile in `%LOCALAPPDATA%\Gigradar Data` (or the folder in `GIGRADAR_HOME`).

**Uninstall:** *Settings → Apps → Gigradar → Uninstall*. It removes the program, the scheduled task and the link
registration. A box "Also delete my data" is **unticked by default**; tick it to delete
`%LOCALAPPDATA%\Gigradar Data` as well (settings, history, the model).

## 2. Build the installer yourself

Needs Windows 10/11 64-bit, Git, Python 3.11+, Node 22, and Rust with the MSVC toolchain (see
[requirements.md](requirements.md#building)).

```powershell
git clone <this repository's URL> gigradar
cd gigradar
py -3.12 -m venv .venv
.venv\Scripts\pip install curl_cffi -r requirements-webview.txt -r requirements-toast.txt -r requirements-scoring.txt -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File app\build.ps1
```

`app\build.ps1` does three things: it freezes the Python helper with PyInstaller into `dist\gigradar`
(`packaging\windows\build.ps1`), copies that folder into the app's resources, then runs `npm ci` and
`tauri build`. The installer ends up in

    app\src-tauri\target\release\bundle\nsis\Gigradar_<version>_x64-setup.exe

The first build downloads the Rust crates and NSIS and takes a while; later builds are faster. The build uses the
CPU heavily. If a scheduled Gigradar check runs on the same PC, do not build in the minutes around it: the hidden
browser page can miss its start window and that one check fails (it is retried at the next run and loses nothing).

Check your build:

```powershell
.venv\Scripts\python -m unittest discover -s tests      # Python, offline
cd app
npm test                                                # screens and settings
npm run check                                           # svelte-check
cd src-tauri; cargo test                                # Rust side
```

Developing the screens without the Windows tooling: `cd app; npm run dev` opens the UI in a browser against a mock
helper. `app/e2e/drive.mjs` drives the real, packaged app (see [desktop-app.md](desktop-app.md#build-and-test) and
[real-pc-test.md](real-pc-test.md)).

## 3. Run from a source checkout

No installer, no tray, no wizard: a config file and a scheduled task. This is how Gigradar was developed.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install curl_cffi -r requirements-webview.txt -r requirements-toast.txt -r requirements-scoring.txt
copy gigradar.example.toml gigradar.toml      # edit searches, [profile], [scoring]
.venv\Scripts\python -m gigradar.embed --download
.venv\Scripts\python -m gigradar.watch        # one run; the first run seeds the history silently
powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Register
```

- Secrets (Telegram bot token and chat id, an optional proxy URL) go in `.env`, never in `gigradar.toml`; Gigradar
  refuses to start if the config holds them. `gigradar.toml`, `.env`, `profile.md`, `data/` and `logs/` are ignored
  by git.
- The scheduled task is `\gigradar\gigradar-watch`; `-Status`, `-Run` and `-Unregister` manage it
  ([windows-scheduler.md](windows-scheduler.md)).
- Optional extra: profile import from documents (`requirements-profile.txt`).
- Do not edit or switch branches in the checkout the task runs from; develop in a git worktree.
