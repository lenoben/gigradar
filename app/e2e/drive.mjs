// Drives the real desktop app through the whole onboarding, over the WebView2 DevTools port, and saves a screenshot
// of every screen. For manual verification on a PC (not part of the unit tests).
//
//   node e2e/drive.mjs --exe <gigradar-app.exe> --home C:/Temp/gg-app-home --sidecar <dist/gigradar> --out C:/Temp/gg-app-shots
//
// The app runs with a throw-away GIGRADAR_HOME and the scheduled task name gigradar-app-test; the task is ALWAYS
// removed again at the end (also when a step fails), so no second watcher is left running.
// Needs Node 22+ (global WebSocket). The Telegram step is only photographed (it needs a real bot token).

import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
for (const key of ["exe", "home", "sidecar", "out"]) if (!args[key]) throw new Error(`missing --${key}`);
const PORT = Number(args.port ?? 9333);
const TASK = "gigradar-app-test";
const sidecarExe = join(args.sidecar, "gigradar.exe");
const env = { ...process.env, GIGRADAR_HOME: args.home, GIGRADAR_TASK_NAME: TASK, GIGRADAR_SIDECAR_DIR: args.sidecar,
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${PORT}` };

const log = [];
const note = (message) => { log.push(message); process.stdout.write(message + "\n"); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
        return;
      }
    } catch { /* the app is still starting */ }
    await sleep(500);
  }
  throw new Error("the app's DevTools port never came up");
}

function cleanup() {
  // the scheduled task must not outlive the test
  if (existsSync(sidecarExe)) {
    const out = spawnSync(sidecarExe, ["task-unregister", "--task-name", TASK], { env, encoding: "utf8" });
    note(`task-unregister: ${out.stdout.trim()}`);
  }
  const query = spawnSync("schtasks", ["/query", "/tn", `\\gigradar\\${TASK}`], { encoding: "utf8" });
  note(query.status === 0 ? "WARNING: the test task still exists" : "the test task is gone");
  if (app && !app.killed) app.kill();
}

try {
  app = spawn(args.exe, [], { env, stdio: "ignore" });
  await connect();
  await call("Page.enable");
  await call("Emulation.setDeviceMetricsOverride", { width: 900, height: 780, deviceScaleFactor: 1, mobile: false });

  await waitFor(heading("Welcome to gigradar"), "welcome screen");
  await shot("welcome");
  await clickText("Get started");

  await waitFor(heading("Tell us what you do"), "profile screen");
  await fill("#profile", "## Web development\nI build web apps with TypeScript, React and Node.js, from small tools to full products.\n\n## Automation\nI write Python scripts that move data between services and clean it up.");
  await shot("profile");
  await clickText("Copy prompt for Claude");
  await sleep(500);
  await shot("profile-prompt-copied");
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
  await waitFor(`document.body.textContent.includes('Sent.')`, "toast test result", 30);
  await shot("notifications-toast");
  await click("#telegram");
  await sleep(300);
  await shot("notifications-telegram-guide");
  await click("#telegram");
  await clickText("Next");

  await waitFor(heading("Check the Upwork connection"), "upwork screen");
  await shot("upwork-before");
  await clickText("Open the check");
  note("upwork-check running: solve the security check in the window if it appears");
  await waitFor(`document.body.textContent.includes('It works') || document.body.textContent.includes("That didn't work.")`, "upwork result", 300);
  await shot("upwork-result");
  note(`upwork-check text: ${await evaluate("document.querySelector('[role=status]')?.textContent.trim()")}`);
  for (let tries = 1; tries < 3 && (await evaluate(`document.body.textContent.includes("That didn't work.")`)); tries++) {
    await clickText("Try again");
    await waitFor(`document.body.textContent.includes('It works') || document.body.textContent.includes("That didn't work.")`, "upwork retry result", 300);
    await shot(`upwork-retry-${tries}`);
  }
  await clickText("Next");

  await waitFor(heading("Download the scoring model"), "model screen");
  await shot("model-before");
  await clickText("Download");
  await waitFor(`document.querySelector('[role=progressbar]')`, "progress bar", 30).catch(() => {});
  await sleep(1500);
  await shot("model-progress").catch(() => {});
  await waitFor(`document.body.textContent.includes('The model is ready')`, "model ready", 300);
  await shot("model-done");
  await clickText("Next");

  await waitFor(heading("Almost done"), "finish screen");
  await shot("finish-before");
  await clickText("Finish setup");
  await waitFor(heading("You're all set"), "finish done", 300);
  await shot("finish-done");
  note(`finish text: ${await evaluate("document.querySelector('main').textContent.replace(/\\s+/g, ' ').slice(0, 400)")}`);
  await clickText("Close");
  for (let i = 0; i < 60 && app.exitCode === null; i++) await sleep(250);
  note(app.exitCode === null ? "WARNING: Close did not quit the app" : `Close quit the app (exit code ${app.exitCode})`);
  socket.close();

  // reopening a set-up app: Home, never the wizard
  app = spawn(args.exe, [], { env, stdio: "ignore" });
  await connect();
  await call("Page.enable");
  await call("Emulation.setDeviceMetricsOverride", { width: 900, height: 780, deviceScaleFactor: 1, mobile: false });
  await waitFor(heading("Home"), "home screen after reopening");
  await waitFor(`document.querySelectorAll('.status-list > li').length > 5 && !document.querySelector('.summary')?.textContent.includes('Checking')`, "status checks", 90);
  await sleep(500);
  await shot("home-reopened");
  note(`home summary: ${await evaluate("document.querySelector('.summary')?.textContent")}`);
  note(`wizard shown again: ${await evaluate("document.body.textContent.includes('Welcome to gigradar')")}`);
  note("ONBOARDING OK");
} catch (error) {
  note(`FAILED: ${error.message}`);
  try { await shot("failure"); } catch { /* no page */ }
  process.exitCode = 1;
} finally {
  cleanup();
  writeFileSync(join(args.out, "drive.log"), log.join("\n") + "\n");
  socket?.close();
}
