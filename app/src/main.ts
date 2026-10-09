import { mount } from "svelte";
import "./app.css";
import App from "./App.svelte";
import { setBackend } from "./lib/backend";
import { draft, progress } from "./lib/draft.svelte";
import { createMockBackend, defaultMockOptions } from "./lib/mock";
import { tauriBackend } from "./lib/tauri";

// Inside the desktop app: the real helper program. In a plain browser (`npm run dev`): a mock, so every screen can be
// looked at. Mock knobs (URL query): fail=window_failed:2 | webview2_missing:1, config=1, doctor=WARN|FAIL, tick=ms,
// step=0..7, demo=1 (fills in sample answers).
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
