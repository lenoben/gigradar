import { mount } from "svelte";
import "./app.css";
import App from "./App.svelte";
import { setBackend } from "./lib/backend";
import type { AppLink } from "./lib/backend";
import { draft, progress } from "./lib/draft.svelte";
import { showTab } from "./lib/nav.svelte";
import type { Tab } from "./lib/nav.svelte";
import { createMockBackend, defaultMockOptions } from "./lib/mock";
import { tauriBackend } from "./lib/tauri";

// Inside the desktop app: the real helper program. In a plain browser (`npm run dev`): a mock, so every screen can be
// looked at. Mock knobs (URL query): fail=window_failed:2 | webview2_missing:1, config=1, doctor=WARN|FAIL, tick=ms,
// step=0..7, demo=1 (fills in sample answers), recheck=1 (Upwork needs a quick check), tg=0 (no bot connected yet),
// tab=status|settings|jobs, link=job:~01aa | jobs:run | status (as if a notification had been clicked).
const params = new URLSearchParams(window.location.search);
if ("__TAURI_INTERNALS__" in window) {
  setBackend(tauriBackend);
} else {
  const options = defaultMockOptions();
  const [failCode, failCount] = (params.get("fail") ?? "").split(":");
  if (failCode) {
    options.upworkFailureCode = failCode;
    options.upworkFailures = Number(failCount ?? 1);
  }
  options.configExists = params.get("config") === "1";
  options.doctorLevel = (params.get("doctor") as "OK" | "WARN" | "FAIL" | null) ?? "OK";
  if (params.has("tick")) options.tick = Number(params.get("tick"));
  options.upworkNeedsCheck = params.get("recheck") === "1";
  options.telegramConnected = params.get("tg") !== "0";
  const link = params.get("link");
  if (link) {
    const [kind, value] = link.split(":");
    options.pendingLink = kind === "job" ? { kind: "job", id: value } : kind === "jobs" ? { kind: "jobs", run: value ?? null } : { kind: "status" } satisfies AppLink;
  }
  if (params.has("tab")) showTab(params.get("tab") as Tab);
  setBackend(createMockBackend(options));
  if (params.get("demo") === "1") {
    draft.profile = "## Web development\nI build web apps with TypeScript, React and Node.js, from small tools to full products.\n\n## Automation\nI write Python scripts that move data between services and clean it up.";
    draft.skills = ["Python", "TypeScript", "React"];
    draft.searches = [{ keywords: "python automation", type: "hourly" }, { keywords: "react dashboard", type: "fixed" }];
    draft.telegram.enabled = params.get("telegram") === "1";
  }
  if (params.has("step")) progress.step = Number(params.get("step"));
}

mount(App, { target: document.getElementById("app")! });
