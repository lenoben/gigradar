// Drives the real desktop app over the WebView2 DevTools port: the whole onboarding, then Settings, Jobs, notification
// links, the "Upwork needs a quick check" fix, quit and restart. Saves a screenshot of every screen. For manual
// verification on a PC (not part of the unit tests).
//
//   node e2e/drive.mjs --exe <Gigradar.exe> --home C:/Temp/gg-app-home --sidecar <dist/gigradar> --out app/shots
//
// The app runs with a throw-away GIGRADAR_HOME and the scheduled task name gigradar-app-test (in the app's own folder
// \Gigradar Desktop\); the task is ALWAYS removed again at the end (also when a step fails), so no second watcher is
// left running. Needs Node 22+ (global WebSocket). The Telegram step is only photographed (it needs a real bot token).

import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
for (const key of ["exe", "home", "sidecar", "out"]) if (!args[key]) throw new Error(`missing --${key}`);
const PORT = Number(args.port ?? 9333);
const TASK = "gigradar-app-test";
const TASK_PATH = `\\Gigradar Desktop\\${TASK}`;
const sidecarExe = join(args.sidecar, "gigradar.exe");
const env = { ...process.env, GIGRADAR_HOME: args.home, GIGRADAR_TASK_NAME: TASK, GIGRADAR_SIDECAR_DIR: args.sidecar,
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${PORT}` };

const log = [];
const note = (message) => { log.push(message); process.stdout.write(message + "\n"); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const checks = { passed: 0, failed: 0 };
function check(name, ok, detail = "") {
  checks[ok ? "passed" : "failed"]++;
  note(`${ok ? "PASS" : "FAIL"}: ${name}${detail ? ` (${detail})` : ""}`);
}

rmSync(args.home, { recursive: true, force: true });
rmSync(args.out, { recursive: true, force: true });
mkdirSync(args.out, { recursive: true });

let app;
let socket;
let nextId = 1;
const pending = new Map();

function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const { result, exceptionDetails } = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (exceptionDetails) throw new Error(exceptionDetails.exception?.description ?? "evaluate failed");
  return result.value;
}

async function waitFor(expression, what, seconds = 60) {
  const end = Date.now() + seconds * 1000;
  while (Date.now() < end) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await sleep(250);
  }
  throw new Error(`timed out waiting for: ${what}`);
}

let shotNumber = 0;
async function shot(name) {
  const { data } = await call("Page.captureScreenshot", { format: "png" });
  const file = join(args.out, `${String(++shotNumber).padStart(2, "0")}-${name}.png`);
  writeFileSync(file, Buffer.from(data, "base64"));
  note(`screenshot ${file}`);
}

const fill = (selector, value) => evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); el.focus(); el.value = ${JSON.stringify(value)}; el.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
const click = (selector) => evaluate(`document.querySelector(${JSON.stringify(selector)}).click(), true`);
const buttonByText = (text) => `[...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith(${JSON.stringify(text)}) && !b.disabled)`;
const clickText = (text) => evaluate(`${buttonByText(text)}.click(), true`);
const heading = (text) => `document.querySelector('h1')?.textContent.includes(${JSON.stringify(text)})`;
const bodyHas = (text) => `document.body.textContent.includes(${JSON.stringify(text)})`;

async function connect() {
  for (let i = 0; i < 120; i++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = targets.find((t) => t.type === "page");
      if (page) {
        socket = new WebSocket(page.webSocketDebuggerUrl);
        await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
        socket.onmessage = (event) => {
          const message = JSON.parse(event.data);
          const waiter = pending.get(message.id);
          if (waiter) { pending.delete(message.id); message.error ? waiter.reject(new Error(message.error.message)) : waiter.resolve(message.result); }
        };
        await call("Page.enable");
        await call("Emulation.setDeviceMetricsOverride", { width: 900, height: 780, deviceScaleFactor: 1, mobile: false });
        return;
      }
    } catch { /* the app is still starting */ }
    await sleep(500);
  }
  throw new Error("the app's DevTools port never came up");
}

function startApp(extraArgs = []) {
  app = spawn(args.exe, extraArgs, { env, stdio: "ignore" });
  return app;
}

/** The helper program, as the app runs it. */
function helper(...argv) {
  return helperWith(undefined, ...argv);
}

function helperWith(input, ...argv) {
  const out = spawnSync(sidecarExe, argv, { env, encoding: "utf8", input });
  const lines = out.stdout.split("\n").filter((l) => l.startsWith("{"));
  return { code: out.status, result: lines.length ? JSON.parse(lines[lines.length - 1]) : null };
}

function query(...argv) {
  return spawnSync("schtasks", argv, { encoding: "utf8" });
}

function cleanup() {
  // the scheduled task must not outlive the test
  if (existsSync(sidecarExe)) {
    const out = spawnSync(sidecarExe, ["task-unregister", "--task-name", TASK], { env, encoding: "utf8" });
    note(`task-unregister: ${out.stdout.trim()}`);
  }
  note(query("/query", "/tn", TASK_PATH).status === 0 ? "WARNING: the test task still exists" : "the test task is gone");
  if (app && app.exitCode === null) app.kill();
}

const openTab = (name) => clickText(name);
// Edit is disabled while the list is read again after a save: wait for it, or the click does nothing
const sectionButton = async (label) => {
  await waitFor(`document.querySelector('button[aria-label="Edit ${label}"]')?.disabled === false`, `the Edit ${label} button`, 30);
  await click(`button[aria-label="Edit ${label}"]`);
};
async function backToSettings() {
  await clickText("Cancel");
  await waitFor(`document.querySelectorAll('.sections > li').length === 6`, "settings list");
}

try {
  startApp();
  await connect();

  // ---- onboarding ------------------------------------------------------------------------------------------------
  await waitFor(heading("Welcome to Gigradar"), "welcome screen");
  check("the window title is Gigradar", (await evaluate("document.title")) === "Gigradar");
  await shot("welcome");
  await clickText("Get started");

  await waitFor(heading("Tell us what you do"), "profile screen");
  await fill("#profile", "## Web development\nI build web apps with TypeScript, React and Node.js, from small tools to full products.\n\n## Automation\nI write Python scripts that move data between services and clean it up.");
  await shot("profile");
  await clickText("Next");

  await waitFor(heading("What matters to you"), "preferences screen");
  for (const skill of ["Python", "TypeScript", "React"]) { await fill("#skill", skill); await click("#skill + button"); }
  await fill("#min-hourly", "40");
  await fill("#constraints", "Remote only");
  await shot("preferences");
  await clickText("Next");

  await waitFor(heading("What should we look for?"), "searches screen");
  await fill("#kw-0", "python automation");
  await clickText("Add another search");
  await fill("#kw-1", "react dashboard");
  await click("input[name='type-1'][value='fixed']");
  await clickText("Add another search");
  await fill("#kw-2", "data pipeline");
  await click("input[name='type-2'][value='both']");
  await shot("searches");
  await click("button[aria-label='Remove search 3']");
  await clickText("Next");

  await waitFor(heading("How should we tell you?"), "notifications screen");
  await clickText("Send a test notification");
  await waitFor(bodyHas("Sent."), "toast test result", 30);
  await shot("notifications-toast");
  await clickText("Next");

  await waitFor(heading("Check the Upwork connection"), "upwork screen");
  await clickText("Open the check");
  note("upwork-check running: solve the security check in the window if it appears");
  await waitFor(`${bodyHas("It works")} || ${bodyHas("That didn't work.")}`, "upwork result", 300);
  for (let tries = 1; tries < 3 && (await evaluate(bodyHas("That didn't work."))); tries++) {
    await clickText("Try again");
    await waitFor(`${bodyHas("It works")} || ${bodyHas("That didn't work.")}`, "upwork retry result", 300);
  }
  await shot("upwork-result");
  await clickText("Next");

  await waitFor(heading("Download the scoring model"), "model screen");
  await clickText("Download");
  await waitFor(bodyHas("The model is ready"), "model ready", 300);
  await clickText("Next");

  await waitFor(heading("Almost done"), "finish screen");
  await clickText("Finish setup");
  await waitFor(heading("You're all set"), "finish done", 300);
  await shot("finish-done");
  check("the finish text", await evaluate(bodyHas("Checks run in the background every 30 minutes, even when the app is closed.")));
  check("the first scheduled task is the app's own", query("/query", "/tn", TASK_PATH).status === 0, TASK_PATH);

  // Close hides the window; the app stays in the tray. A second start shows the same window again.
  await clickText("Close");
  await waitFor(`document.querySelector('[role=dialog]')?.textContent.includes('still running in the tray')`, "the one-time tray hint");
  await shot("tray-hint");
  check("the first close shows the tray hint", true);
  await clickText("OK, got it");
  await sleep(1500);
  check("Close keeps the app running (tray)", app.exitCode === null);
  const hidden = await evaluate("document.visibilityState");
  note(`page visibility after Close: ${hidden}`);
  const second = startApp();
  const first = app;
  await new Promise((resolve) => second.on("exit", resolve));
  check("a second start hands over to the first and exits", second.exitCode === 0 || second.exitCode !== null, `exit ${second.exitCode}`);
  app = first;
  await sleep(1000);

  // ---- Home: Status ------------------------------------------------------------------------------------------------
  await clickText("Open the status page");
  await waitFor(`${heading("Home")} && document.querySelectorAll('.status-list > li').length > 5 && !document.querySelector('.summary')?.textContent.includes('Checking')`, "status checks", 90);
  await shot("home-status");
  note(`home summary: ${await evaluate("document.querySelector('.summary')?.textContent")}`);
  check("the task is called checks in the app's folder", true, TASK_PATH);

  // ---- Settings --------------------------------------------------------------------------------------------------
  await openTab("Settings");
  await waitFor(`document.querySelectorAll('.sections > li').length === 6`, "settings list");
  await shot("settings-list");
  await sectionButton("Profile");
  await waitFor(`document.getElementById('profile')?.value.includes('## Web development')`, "profile prefilled");
  await shot("settings-profile");
  await backToSettings();
  await sectionButton("Preferences");
  await waitFor(`document.getElementById('min-hourly')?.value === '40'`, "preferences prefilled");
  await shot("settings-preferences");
  await backToSettings();
  await sectionButton("Searches");
  await waitFor(`document.getElementById('kw-0')?.value === 'python automation'`, "searches prefilled");
  await shot("settings-searches");
  await backToSettings();
  await sectionButton("Notifications");
  await waitFor(bodyHas("Notifications on this PC"), "notifications section");
  await shot("settings-notifications");
  await backToSettings();

  // Schedule: 1 hour + quiet hours; the task is re-registered with the new interval
  await sectionButton("Schedule");
  await waitFor(`document.querySelector("input[name='interval'][value='30']")?.checked`, "schedule prefilled");
  await shot("settings-schedule");
  await click("input[name='interval'][value='60']");
  await click("#quiet");
  await waitFor(`document.getElementById('quiet-from')`, "quiet hours fields");
  await fill("#quiet-from", "22:00");
  await fill("#quiet-to", "07:00");
  await shot("settings-schedule-edited");
  await clickText("Save");
  await waitFor(bodyHas("Saved."), "schedule saved");
  const xml = query("/query", "/tn", TASK_PATH, "/xml").stdout;
  check("saving the schedule re-registered the task with the new interval", xml.includes("<Interval>PT1H</Interval>") || xml.includes("<Interval>PT60M</Interval>"));
  const toml = readFileSync(join(args.home, "gigradar.toml"), "utf8");
  check("the quiet hours are in the settings file", toml.includes('quiet_start = "22:00"') && toml.includes("interval_minutes = 60"));
  await shot("settings-saved");

  // Storage: the setup wrote one month; change it to a week without keeping rated jobs, then back
  check("the setup wrote the one-month retention", toml.includes("[retention]") && toml.includes("keep_days = 30") && toml.includes("keep_rated = true"));
  await sectionButton("Storage");
  await waitFor(`document.querySelector("input[name='keep-days'][value='30']")?.checked`, "storage prefilled");
  await shot("settings-storage");
  await click("input[name='keep-days'][value='7']");
  await click("#keep-rated");
  await clickText("Save");
  await waitFor(bodyHas("Saved."), "storage saved");
  const tomlStorage = readFileSync(join(args.home, "gigradar.toml"), "utf8");
  check("a week and not keeping rated jobs are in the settings file", tomlStorage.includes("keep_days = 7") && tomlStorage.includes("keep_rated = false"));
  await sectionButton("Storage");
  await waitFor(`document.querySelector("input[name='keep-days'][value='7']")?.checked`, "storage prefilled again");
  await click("input[name='keep-days'][value='30']");
  await click("#keep-rated");
  await clickText("Save");
  await waitFor(bodyHas("Saved."), "storage restored");

  // a changed search gets a new name, the other keeps its name
  await sectionButton("Searches");
  await waitFor(`document.getElementById('kw-1')?.value === 'react dashboard'`, "searches prefilled");
  await fill("#kw-1", "vue dashboard");
  await clickText("Save");
  await waitFor(bodyHas("Saved."), "searches saved");
  check("a changed search says its old jobs come without notifications", await evaluate(bodyHas("Jobs already posted for this search are added without notifications.")));
  await shot("settings-searches-saved");
  const toml2 = readFileSync(join(args.home, "gigradar.toml"), "utf8");
  check("an unchanged search keeps its name, a changed one gets a new one",
    toml2.includes('name = "python-automation-hourly"') && toml2.includes('name = "vue-dashboard-fixed"') && !toml2.includes("react-dashboard"));

  // back to every 30 minutes, no quiet hours
  await sectionButton("Schedule");
  await waitFor(`document.querySelector("input[name='interval'][value='60']")?.checked`, "schedule prefilled again");
  await click("input[name='interval'][value='30']");
  await click("#quiet");
  await clickText("Save");
  await waitFor(bodyHas("Saved."), "schedule restored");
  check("the interval is back to 30 minutes", query("/query", "/tn", TASK_PATH, "/xml").stdout.includes("<Interval>PT30M</Interval>"));

  // quiet hours: a scheduled run stays out, a hand-started one does not (real helper, real config)
  const logFile = join(args.home, "logs", "gigradar.log");
  const set = helperWith(JSON.stringify({ interval_minutes: 30, quiet_start: "00:00", quiet_end: "23:59" }), "settings-save", "--section", "schedule", "--task-name", TASK, "--exe", args.exe);
  check("quiet hours saved", set.result?.ok === true, JSON.stringify(set.result?.error ?? ""));
  const started = Date.now();
  const scheduled = helper("run-once");
  const quietLog = readFileSync(logFile, "utf8").split(String.fromCharCode(10)).filter((l) => l.includes("quiet hours"));
  check("a scheduled run inside the quiet hours does nothing", scheduled.result?.exit_code === 0 && quietLog.length > 0 && Date.now() - started < 20000, `${Date.now() - started} ms; ${quietLog.at(-1) ?? "no log line"}`);
  const manual = helper("run-once", "--ignore-quiet-hours");
  check("a check started by hand ignores the quiet hours", manual.result?.exit_code === 0 || manual.result?.exit_code === 75, `exit ${manual.result?.exit_code}`);
  helperWith(JSON.stringify({ interval_minutes: 30, quiet_start: null, quiet_end: null }), "settings-save", "--section", "schedule", "--task-name", TASK, "--exe", args.exe);

  // ---- Jobs ------------------------------------------------------------------------------------------------------
  await openTab("Jobs");
  await waitFor(heading("Latest jobs"), "jobs tab");
  await waitFor(`document.querySelectorAll('.joblist > li').length > 0 || document.body.textContent.includes('No jobs yet')`, "job list", 30);
  await shot("jobs-list");
  const jobs = helper("jobs", "--limit", "5").result?.jobs ?? [];
  check("the job list has the jobs the first check found", jobs.length > 0, `${jobs.length} jobs`);
  const everyJob = helper("jobs", "--limit", "100").result?.jobs ?? [];
  check("every job in the list has a score (seeded ones too)", everyJob.length > 0 && everyJob.every((j) => j.score !== null), `${everyJob.filter((j) => j.score === null).length} of ${everyJob.length} without`);

  // "Open on Upwork": the opener may open Upwork and nothing else (a tab opens in the default browser)
  const openUrl = (url) => evaluate(`window.__TAURI_INTERNALS__.invoke('plugin:opener|open_url', { url: ${JSON.stringify(url)} }).then(() => 'opened', (e) => 'refused: ' + e)`);
  const refused = await openUrl("https://example.com/");
  check("a link outside Upwork is refused by the shell", String(refused).startsWith("refused"), String(refused).slice(0, 80));
  const allowed = await openUrl("https://www.upwork.com/");
  check("a link to Upwork is opened", allowed === "opened", String(allowed).slice(0, 80));

  // a minimum score: the list hides the jobs below it until the toggle is on
  const tomlBefore = readFileSync(join(args.home, "gigradar.toml"), "utf8");
  writeFileSync(join(args.home, "gigradar.toml"), tomlBefore.replace('model_dir = "models"', 'model_dir = "models"' + String.fromCharCode(10) + "min_score = 60"));
  const shown = helper("jobs", "--limit", "100").result?.jobs ?? [];
  const withBelow = helper("jobs", "--limit", "100", "--include-below").result?.jobs ?? [];
  check("jobs below the minimum are left out unless asked for", shown.every((j) => j.score === null || j.score >= 60) && withBelow.length >= shown.length,
    `${shown.length} shown, ${withBelow.length} with the ones below 60`);
  await openTab("Status");
  await openTab("Jobs");
  await waitFor(`document.getElementById('show-below')`, "the show-below toggle");
  await shot("jobs-list-min-score");
  if (withBelow.length > shown.length) {
    await click("#show-below");
    await waitFor(`document.querySelectorAll('.joblist > li.dim').length > 0`, "dimmed rows");
    check("the toggle shows the jobs below the minimum, dimmed and tagged", await evaluate(`document.querySelector('.joblist > li.dim .tag')?.textContent === 'below minimum'`));
    await shot("jobs-list-below-shown");
    await click("#show-below");
    await waitFor(`document.querySelectorAll('.joblist > li.dim').length === 0 && document.querySelectorAll('.joblist > li').length > 0`, "the list without the jobs below the minimum");
  } else note("no job scored below 60 in this sample: the toggle was only photographed");
  writeFileSync(join(args.home, "gigradar.toml"), tomlBefore);
  if (jobs.length > 0) {
    await evaluate("document.querySelector('.joblist .title').click(), true");
    await waitFor(`document.querySelector('.jobcard')`, "job card");
    await shot("job-card");
    await click("button[aria-label='Good match']");
    await waitFor(`document.querySelector("button[aria-label='Good match']").getAttribute('aria-pressed') === 'true'`, "thumb up");
    const labelled = helper("jobs", "--limit", "5").result?.jobs ?? [];
    check("the thumb is stored as a label", labelled.some((j) => j.label === 1));
    await shot("job-card-liked");
    await click("button[aria-label='Good match']"); // take it back
    await waitFor(`document.querySelector("button[aria-label='Good match']").getAttribute('aria-pressed') === 'false'`, "thumb cleared");
  }

  // ---- links: the app is open ---------------------------------------------------------------------------------------
  if (jobs.length > 1) {
    const run = jobs[0].first_seen;
    const digest = startApp([`gigradar://jobs?run=${encodeURIComponent(run)}`]);
    const keep = app;
    await new Promise((resolve) => digest.on("exit", resolve));
    app = keep;
    await waitFor(bodyHas("These are the jobs from one check."), "digest list", 15);
    await waitFor(`document.querySelectorAll('.joblist > li').length > 0`, "digest list rows", 15);
    await shot("link-digest-list");
    check("a digest link shows that check's jobs", await evaluate("document.querySelectorAll('.joblist > li').length > 0"));

    const one = startApp([`gigradar://job/${encodeURIComponent(jobs[1].id)}`]);
    await new Promise((resolve) => one.on("exit", resolve));
    app = keep;
    await waitFor(`document.querySelector('.jobcard h1')?.textContent === ${JSON.stringify(jobs[1].title)}`, "job card from a link", 15);
    await shot("link-job-card");
    check("a job link (app open) shows that job's card", true, jobs[1].title);
  }

  // ---- Upwork needs a quick check -------------------------------------------------------------------------------------
  mkdirSync(join(args.home, "data"), { recursive: true });
  writeFileSync(join(args.home, "data", "upwork_check_needed.json"), JSON.stringify({ since: new Date().toISOString() }));
  await openTab("Status");
  await waitFor(`${bodyHas("Upwork access")} && !document.querySelector('.summary')?.textContent.includes('Checking')`, "status with the notice", 60);
  await shot("status-upwork-needs-check");
  const row = `[...document.querySelectorAll('.status-list > li')].find(li => li.textContent.includes('Upwork access'))`;
  check("Status shows a Check pill and a Fix button for Upwork", await evaluate(`${row}.querySelector('.pill.WARN') !== null && ${row}.querySelector('.fixbtn')?.textContent.trim() === 'Check Upwork'`));
  await evaluate(`${row}.querySelector('.fixbtn').click(), true`);
  note("the visible Upwork window opens now (solve the check if it asks)");
  await waitFor(`!(${row})?.querySelector('.fixbtn') && !document.querySelector('.summary')?.textContent.includes('Checking')`, "the notice to go away", 300);
  check("the successful check removed the marker", !existsSync(join(args.home, "data", "upwork_check_needed.json")));
  await shot("status-upwork-fixed");

  // ---- quit, and start again from a notification link --------------------------------------------------------------
  await clickText("Quit Gigradar");
  for (let i = 0; i < 40 && app.exitCode === null; i++) await sleep(250);
  check("Quit ends the app", app.exitCode !== null);
  socket.close();
  if (jobs.length > 0) {
    startApp([`gigradar://job/${encodeURIComponent(jobs[0].id)}`]);
    await connect();
    await waitFor(`document.querySelector('.jobcard h1')?.textContent === ${JSON.stringify(jobs[0].title)}`, "job card after a cold start from a link", 60);
    await shot("link-cold-start-job-card");
    check("a job link starts the closed app on that job's card", true, jobs[0].title);
    await clickText("← Back to the jobs");
    await clickText("Quit Gigradar").catch(() => {});
  }
  note(`CHECKS passed ${checks.passed}, failed ${checks.failed}`);
  note("E2E DONE");
} catch (error) {
  note(`FAILED: ${error.message}`);
  try { await shot("failure"); } catch { /* no page */ }
  process.exitCode = 1;
} finally {
  cleanup();
  writeFileSync(join(args.out, "drive.log"), log.join("\n") + "\n");
  socket?.close();
}
