# Real-PC test of the desktop app

For a second, ordinary Windows PC (not the development machine). The aim is to find what a clean machine and a
non-technical person would hit: the browser download warning, SmartScreen, the installer, the first-run screens,
Upwork's security check, the background checks, and the uninstall. Write down anything that surprises you, with the
exact text of any Windows dialog.

**Installer:** `Gigradar Desktop_<version>_x64-setup.exe` (about 60 MB), built by `app\build.ps1`
(see `docs/desktop-app.md`). It is not code-signed, so Windows warns about it (step 2).

**You need:** a Telegram bot made only for this test (message @BotFather, `/newbot`), an internet connection, and
about 20 minutes. Do not reuse your real bot or profile.

Tick each box; note the time and anything unexpected next to it.

## 1. Download in the browser
- [ ] Put the installer where the PC can download it with a browser (a link, a shared folder opened in the browser,
      not a USB copy: a copy from USB skips the "downloaded from the internet" mark that SmartScreen reacts to).
- [ ] Download it with the browser. Note the browser's own warning, if any ("not commonly downloaded", "keep").
- [ ] Note the file size shown (about 60 MB).

## 2. SmartScreen
- [ ] Open the downloaded file. Note whether "Windows protected your PC" appears and its exact text.
- [ ] Choose "More info" and "Run anyway". (If you have to hunt for the button, that is a finding: a normal user will
      give up here. The fix is code signing.)
- [ ] Note whether antivirus software says anything.

## 3. Install
- [ ] The installer asks for no administrator password (it installs for the current user only).
- [ ] If the PC has no Microsoft Edge WebView2 runtime, the installer installs it by itself: note the extra time.
- [ ] After "Finish" the app starts, or start "Gigradar Desktop" from the Start menu. First start is slower: note how long.
- [ ] The install folder is `%LOCALAPPDATA%\Gigradar Desktop`; the app's data goes to `%LOCALAPPDATA%\gigradar`.

## 4. Onboarding with the test bot
- [ ] Welcome, Profile (paste any short text; try "Copy prompt for Claude"), Preferences, Searches (try "Both").
- [ ] Notifications: "Send a test notification" shows a Windows notification. Turn on Telegram, follow the three
      steps, paste the test bot's token, press Connect, send `/start` to the bot, press "I sent /start".
      Expect a test message in Telegram.
- [ ] Nothing in the window ever shows the token again after you press Connect.

## 5. Upwork check
- [ ] "Open the check" opens a window. Upwork may show a security check: click its box. Note whether it appeared.
- [ ] The window closes by itself and the app says "It works". If it fails, note the message and use "Try again".
- [ ] Model download: note how long it takes.

## 6. Finish and close
- [ ] "Finish setup" runs the three steps and shows the latest jobs.
- [ ] The text says checks run in the background every 30 minutes even when the app is closed. Press "Close".
- [ ] Open Task Scheduler: `\gigradar\gigradar-watch` exists, runs every 30 minutes, "Run only when user is logged on".

## 7. First scheduled run
- [ ] Wait for the task's next run time (shown on the Home screen as "next check at ..."), with the app closed.
- [ ] Task Scheduler "Last Run Result" is `0x0`. The log is `%LOCALAPPDATA%\gigradar\logs\gigradar.log`: it has a
      "run start" and "N new of M found" line for that time.
- [ ] Note whether a window ever popped up by itself (it should not, unless Upwork asks for a click).

## 8. Reboot and the next run
- [ ] Restart Windows and sign in. Do not open the app.
- [ ] After the next 30-minute mark the log has a new run and the task result is `0x0`.
- [ ] Open the app: it shows Home (not the setup) with "All set — next check at ...".

## 9. Uninstall and leftovers
- [ ] Uninstall "Gigradar Desktop" (Settings, Apps). Note any prompt.
- [ ] Check that the scheduled task is still there (it is, until the uninstaller removes it in a later version):
      `schtasks /query /tn "\gigradar\gigradar-watch"` shows it. A task left behind keeps trying to run a deleted
      program every 30 minutes, so remove it with this command (PowerShell or Command Prompt, no administrator needed):

      schtasks /delete /tn "\gigradar\gigradar-watch" /f

      Then `schtasks /query /tn "\gigradar\gigradar-watch"` must say "cannot find the file".
- [ ] The data folder `%LOCALAPPDATA%\gigradar` stays on purpose (settings, job history, model). Delete it by hand
      for a fully clean machine.
- [ ] Note anything else that is left: Start menu entry, the folder `%LOCALAPPDATA%\Gigradar Desktop`.

## Report back
For every box that did not go as written: what happened, the exact text of any message, and a screenshot
(Win+Shift+S). Also: how long the whole thing took, and where a non-technical person would have got stuck.
