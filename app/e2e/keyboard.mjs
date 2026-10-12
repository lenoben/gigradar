// Keyboard check on the mock UI (run `npm run build && npx vite preview --port 4173` first):
// for every screen, press Tab through the page and report any control that is never reached or has no visible focus ring.
//   node e2e/keyboard.mjs [baseUrl] [edgePath]
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = process.argv[2] ?? "http://127.0.0.1:4173";
const edge = process.argv[3] ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const browser = spawn(edge, ["--headless=new", "--remote-debugging-port=9445", `--user-data-dir=${mkdtempSync(join(tmpdir(), "kb-"))}`, "--window-size=980,900", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let socket;
let nextId = 1;
const pending = new Map();
const call = (method, params = {}) => new Promise((resolve, reject) => {
  const id = nextId++;
  const timer = setTimeout(() => reject(new Error(`timeout ${method}`)), 20000);
  pending.set(id, (m) => { clearTimeout(timer); m.error ? reject(new Error(m.error.message)) : resolve(m.result); });
  socket.send(JSON.stringify({ id, method, params }));
});
const evaluate = async (expression) => (await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true })).result.value;
const key = (type, k, extra = {}) => call("Input.dispatchKeyEvent", { type, key: k, code: k, windowsVirtualKeyCode: k === "Tab" ? 9 : 0, ...extra });

const describeActive = `(() => { const e = document.activeElement; if (!e || e === document.body) return null;
  const s = getComputedStyle(e); const ring = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0;
  const label = e.id || e.getAttribute('aria-label') || e.textContent.trim().slice(0, 30) || e.tagName;
  return { label: e.tagName.toLowerCase() + ':' + label, ring }; })()`;
const focusables = `[...document.querySelectorAll('button,input,textarea,a[href],summary,[tabindex="0"]')].filter((e, i, all) => !e.disabled && e.offsetParent !== null && e.tabIndex >= 0 && !(e.closest('details:not([open])') && e.tagName !== 'SUMMARY') && !(e.type === 'radio' && all.findIndex(o => o.type === 'radio' && o.name === e.name && !o.disabled) !== i)).map(e => e.tagName.toLowerCase() + ':' + (e.id || e.getAttribute('aria-label') || e.textContent.trim().slice(0, 30) || e.tagName))`;

let failures = 0;
try {
  let page;
  for (let i = 0; i < 40 && !page; i++) {
    try { page = (await (await fetch("http://127.0.0.1:9445/json/list")).json()).find((t) => t.type === "page"); } catch { /* starting */ }
    await sleep(300);
  }
  socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r) => (socket.onopen = r));
  socket.onmessage = (e) => { const m = JSON.parse(e.data); pending.get(m.id)?.(m); pending.delete(m.id); };
  await call("Runtime.enable");
  await call("Page.enable");
  const screens = [[0, "welcome"], [1, "profile"], [2, "preferences"], [3, "searches"], [4, "notifications"], [4, "notifications+telegram", "&telegram=1"], [5, "upwork"], [6, "model"], [7, "finish"]];
  for (const [step, name, extra = ""] of [...screens, [0, "status", "&config=1"]]) {
    await call("Page.navigate", { url: `${base}/?demo=1&step=${step}&tick=0${extra}` });
    await sleep(1200);
    const expected = await evaluate(focusables);
    const reached = new Set();
    let noRing = [];
    for (let i = 0; i < expected.length + 3; i++) {
      await key("keyDown", "Tab");
      await key("keyUp", "Tab");
      const active = await evaluate(describeActive);
      if (active) { reached.add(active.label); if (!active.ring) noRing.push(active.label); }
    }
    const missing = expected.filter((label) => !reached.has(label));
    if (missing.length || noRing.length) failures++;
    console.log(`${name.padEnd(24)} controls ${String(expected.length).padStart(2)}  reached ${reached.size}  ${missing.length ? "NOT REACHED: " + missing.join(", ") : ""} ${noRing.length ? "NO FOCUS RING: " + [...new Set(noRing)].join(", ") : ""}`);
  }
  console.log(failures ? `${failures} screens with problems` : "all controls reachable with a visible focus ring");
} catch (error) {
  console.log("FAILED:", error.message);
  failures = 1;
} finally {
  browser.kill();
  process.exit(failures ? 1 : 0);
}
