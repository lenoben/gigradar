# Real-PC test of the desktop app

For a second, ordinary Windows PC (not the development machine). The aim is to find what a clean machine and a
non-technical person would hit: the browser download warning, SmartScreen, the installer, the first-run screens,
Upwork's security check, the background checks, notifications that open the app, and the uninstall. Write down
anything that surprises you, with the exact text of any Windows dialog.

**Installer:** `Gigradar_<version>_x64-setup.exe` (about 60 MB), built by `app\build.ps1` (see `docs/desktop-app.md`).
It is not code-signed, so Windows warns about it (step 2).

**You need:** a Telegram bot made only for this test (message @BotFather, `/newbot`), an internet connection, and
about 30 minutes. Do not reuse your real bot or profile.

**Names on the PC:** program `%LOCALAPPDATA%\Gigradar`, data `%LOCALAPPDATA%\Gigradar Data`, scheduled task
`\Gigradar Desktop\checks`. The product is spelled "Gigradar" everywhere you can read it.

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
- [ ] The installer asks for no administrator password (it installs for the current user only) and is called Gigradar.
- [ ] If the PC has no Microsoft Edge WebView2 runtime, the installer installs it by itself: note the extra time.
- [ ] After "Finish" the app starts, or start "Gigradar" from the Start menu. First start is slower: note how long.
- [ ] The window title is "Gigradar" and a Gigradar icon is in the tray (bottom right, maybe under the arrow).

## 4. Onboarding with the test bot
- [ ] Welcome, Profile (paste any short text; try "Copy prompt for Claude"), Preferences, Searches (try "Both").
- [ ] Notifications: "Send a test notification" shows a Windows notification. It must say **Gigradar** with the app
      icon (not "Python" or a file name). Turn on Telegram, follow the three steps, paste the test bot's token, press
      Connect, send `/start` to the bot, press "I sent /start". Expect a test message in Telegram.
- [ ] Nothing in the window ever shows the token again after you press Connect.

## 5. Upwork check
- [ ] "Open the check" opens a window. Upwork may show a security check: click its box. Note whether it appeared.
- [ ] The window closes by itself and the app says "It works". If it fails, note the message and use "Try again".
- [ ] Model download: note how long it takes.

## 6. Finish and close
- [ ] "Finish setup" runs the three steps and shows the latest jobs.
- [ ] The text says checks run in the background every 30 minutes even when the app is closed. Press "Close": the
      window first shows a hint that Gigradar keeps running in the tray (bottom right); press OK and the window disappears, the tray icon stays. Close it a second time: no hint any more.
- [ ] Open Task Scheduler: `\Gigradar Desktop\checks` exists, runs every 30 minutes, "Run only when user is logged on".
- [ ] Right-click the tray icon: Open Gigradar / Run check now / Quit. "Open Gigradar" brings the window back.

## 7. Settings, jobs and notifications that open the app
- [ ] Home > Settings: each of the six sections opens with what you entered. Change the Schedule to 1 hour and Save:
      Task Scheduler now shows a 1-hour repeat for `\Gigradar Desktop\checks`. Change it back to 30 minutes.
- [ ] Settings > Notifications shows "Connected as @<your test bot>". Nothing asks for the token until "Change bot".
- [ ] Home > Jobs lists the latest jobs. Open one: title, score, budget, posted time, matching skills, description.
      "Open on Upwork" opens the browser. 👍 and 👎 stay pressed when you come back.
- [ ] When the next check finds new jobs, click the Windows notification:
  - [ ] with the app **closed from the tray (Quit)**: Gigradar starts on that job's card (several jobs: the list of
        that check);
  - [ ] with the app **open**: it comes to the front on the card.
  Note anything else that happens (a second window, an error, "choose an app").

## 8. First scheduled run
- [ ] Wait for the task's next run time (shown on the Home screen as "next check at ..."), with the app closed.
- [ ] Task Scheduler "Last Run Result" is `0x0`. The log is `%LOCALAPPDATA%\Gigradar Data\logs\gigradar.log`: it has a
      "run start" and "N new of M found" line for that time.
- [ ] Note whether a window ever popped up by itself (it should not, ever).

## 9. Reboot and the next run
- [ ] Restart Windows and sign in. Do not open the app.
- [ ] After the next 30-minute mark the log has a new run and the task result is `0x0`.
- [ ] Open the app from the Start menu: it shows Home (not the setup) with "All set — next check at ...".

## 10. Uninstall and leftovers
- [ ] Uninstall "Gigradar" (Settings, Apps). The confirm page has a box "Also delete my data ..." that is
      **unticked**. Leave it unticked for this run. Note any other prompt.
- [ ] The scheduled task is gone: `schtasks /query /tn "\Gigradar Desktop\checks"` says it cannot find the file. (The
      uninstaller removes it. If it is still there, that is a finding; remove it with
      `schtasks /delete /tn "\Gigradar Desktop\checks" /f`.)
- [ ] `%LOCALAPPDATA%\Gigradar Data` is still there (settings, job history, model), and the program folder
      `%LOCALAPPDATA%\Gigradar` and the tray icon are gone.
- [ ] Install again, start it: it opens on Home with your settings (not the setup). Uninstall again, this time **tick**
      "Also delete my data": `%LOCALAPPDATA%\Gigradar Data` is gone.
- [ ] Note anything else that is left: a Start menu entry, `schtasks /query /tn "\Gigradar Desktop\checks"`.

# Round 2 (installer 0.2.x, second build)

Only what changed since round 1. Same PC, same test bot; update by running the new installer over the old one (it
keeps your data and the scheduled task), or uninstall (keep data) first.

## R1. Open on Upwork
- [ ] Home > Jobs > a job > "Open on Upwork" opens the job page in the browser. Same from the list's button.
- [ ] 👍 or 👎 on a card; go back and open the card again: it is still pressed. In the list too.

## R2. Scores everywhere
- [ ] After the first check (or the next scheduled one) every job in Home > Jobs shows a number, also the ones the
      first run found silently. A "–" can stay for a short while on a very new install: it goes after the next run.

## R3. Below the minimum score
- [ ] Settings > Preferences: "Minimum match score" shows 20 (a fresh setup writes it). Set it to 40 (a high number
      makes sure some jobs fall below it) and Save.
- [ ] After a check that finds new jobs, the notification says "N new jobs" and "M below your minimum score" and lists
      only the others; Telegram says the same in a first line. Clicking the toast opens the list of that check.
- [ ] Home > Jobs: the switch "Show jobs below my minimum score" appears. Off: those jobs are missing. On: they are
      there, dimmed, with a "below minimum" tag.

## R4. Searches note
- [ ] Settings > Searches: change one search's keywords and Save: the page says "Jobs already posted for this search are
      added without notifications." Saving without a change does not show it.

## R5. Storage
- [ ] Settings > Storage shows "1 month" and "Keep the jobs I rated" on. Pick "1 week", Save: the summary reads
      "Keep jobs for 1 week · jobs I rated are kept". Pick it back to 1 month.

# Report back
For every box that did not go as written: what happened, the exact text of any message, and a screenshot
(Win+Shift+S). Also: how long the whole thing took, and where a non-technical person would have got stuck.
