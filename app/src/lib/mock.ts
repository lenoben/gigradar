import type { AppInfo, Backend, CommandName, RunOutcome, SidecarEvent, SidecarResult } from "./backend";

// A stand-in for the helper program: used in the browser (`npm run dev`, `?mock=1`) to look at every screen, and in
// the tests. Never reaches the network or the disk. It records what it was asked so tests can assert on it.

export interface MockOptions {
  /** Milliseconds per simulated step; 0 = instant (tests). */
  tick: number;
  /** The first N upwork-checks fail with this error code. */
  upworkFailures: number;
  upworkFailureCode: string;
  /** Telegram: the first connect says the bot has no message yet. */
  telegramNeedsStart: boolean;
  /** doctor reports a missing config (first start) or a healthy install. */
  configExists: boolean;
  /** A config file exists but is invalid (not the same as missing). */
  configBroken: boolean;
  doctorLevel: "OK" | "WARN" | "FAIL";
}

export function defaultMockOptions(): MockOptions {
  return {
    tick: 350,
    upworkFailures: 0,
    upworkFailureCode: "window_failed",
    telegramNeedsStart: true,
    configExists: false,
    configBroken: false,
    doctorLevel: "OK",
  };
}

export interface MockBackend extends Backend {
  calls: { name: string; payload: unknown }[];
  tokensSeen: string[];
}

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function failure(command: string, code: string, message: string): RunOutcome {
  return { exitCode: 1, result: { ok: false, command, error: { code, message } } };
}

function success(command: string, extra: Record<string, unknown>): RunOutcome {
  const result: SidecarResult = { ok: true, command, ...extra };
  return { exitCode: 0, result };
}

export function createMockBackend(options: MockOptions): MockBackend {
  let upworkAttempts = 0;
  let telegramAttempts = 0;
  let heldToken: string | null = null;
  const calls: MockBackend["calls"] = [];
  const tokensSeen: string[] = [];
  const wait = (steps: number) => pause(options.tick * steps);

  function doctor(): RunOutcome {
    const level = options.doctorLevel;
    const checks = [
      { name: "config", status: options.configExists ? "OK" : "FAIL", message: options.configBroken ? "C:\\Users\\you\\AppData\\Local\\gigradar\\gigradar.toml: [notify] channels must be a list" : options.configExists ? "C:\\Users\\you\\AppData\\Local\\gigradar\\gigradar.toml (2 searches)" : "C:\\Users\\you\\AppData\\Local\\gigradar\\gigradar.toml not found (copy gigradar.example.toml)", fix: options.configExists ? null : "run setup-apply" },
      { name: "profile", status: "OK", message: "3 skill areas, 6 skills", fix: null },
      { name: "telegram_token", status: "OK", message: "bot token present", fix: null },
      { name: "telegram_api", status: level === "FAIL" ? "FAIL" : "OK", message: level === "FAIL" ? "getMe failed: network" : "bot @my_gigradar_bot answers", fix: level === "FAIL" ? "check the bot token and the internet connection" : null },
      { name: "telegram_chat", status: "OK", message: "chat id present", fix: null },
      { name: "webview2_runtime", status: "OK", message: "WebView2 runtime 155.0.4283.45", fix: null },
      { name: "webview_profile", status: level === "OK" ? "OK" : "WARN", message: level === "OK" ? "C:\\Users\\you\\AppData\\Local\\gigradar\\webview2" : "no browser profile yet", fix: level === "OK" ? null : "run upwork-check once" },
      { name: "model", status: "OK", message: "BAAI/bge-small-en-v1.5 cached", fix: null },
      { name: "store", status: "OK", message: "124 jobs seen", fix: null },
      { name: "scheduled_task", status: level === "OK" ? "OK" : "WARN", message: level === "OK" ? "registered, last run 12:23, result 0" : "task is not registered", fix: level === "OK" ? null : "register it", data: level === "OK" ? { next_run: new Date(Date.now() + 25 * 60_000).toISOString().slice(0, 19) } : null },
      { name: "recent_errors", status: "OK", message: "no ERROR in the last 120 log lines", fix: null, details: [] },
    ];
    const status = checks.some((c) => c.status === "FAIL") ? "FAIL" : checks.some((c) => c.status === "WARN") ? "WARN" : "OK";
    return { exitCode: status === "FAIL" ? 1 : 0, result: { ok: status !== "FAIL", command: "doctor", status, checks } };
  }

  return {
    calls,
    tokensSeen,
    async info(): Promise<AppInfo> {
      return { home: "C:\\Users\\you\\AppData\\Local\\gigradar", taskName: "gigradar-watch" };
    },
    async run(name: CommandName, payload: unknown, _runId: number, onEvent: (e: SidecarEvent) => void): Promise<RunOutcome> {
      calls.push({ name, payload });
      switch (name) {
        case "doctor":
          await wait(2);
          return doctor();
        case "setup-apply":
          await wait(1);
          return success(name, { written: ["gigradar.toml", "profile.md"], backup: null });
        case "notify-test":
          await wait(2);
          return success(name, { channels: ["toast"] });
        case "upwork-check": {
          onEvent({ event: "window", message: "A window opens." });
          await wait(5);
          upworkAttempts++;
          if (upworkAttempts <= options.upworkFailures) {
            const code = options.upworkFailureCode;
            return failure(name, code, code === "webview2_missing" ? "WebView2 is not installed" : "Main window failed to start");
          }
          return success(name, { search: "python-hourly", jobs_found: 30, seeded: 30, jobs_seen_total: 30, attempts: { window: 1, fetch: 1 } });
        }
        case "model-download": {
          const total = 67_465_124;
          onEvent({ event: "start", model: "BAAI/bge-small-en-v1.5", total_bytes: total });
          for (let i = 1; i <= 10; i++) {
            await wait(1);
            onEvent({ event: "progress", bytes: Math.round((total * i) / 10), total_bytes: total, percent: Math.min(99, i * 10) });
          }
          onEvent({ event: "verify", message: "downloaded; checking it loads offline" });
          await wait(2);
          return success(name, { model: "BAAI/bge-small-en-v1.5", bytes: total, dim: 384 });
        }
        case "task-register":
          await wait(2);
          return success(name, { task_name: "gigradar-watch", interval_minutes: 30 });
        case "task-unregister":
          return success(name, { removed: true });
        case "run-once":
          await wait(5);
          return success(name, { exit_code: 0, meaning: "ok", jobs_seen_before: 30, jobs_seen_after: 33 });
        case "jobs":
          await wait(1);
          return success(name, {
            jobs: [
              { id: "~01", title: "Build a Python tool that syncs invoices", pay: "$40-60/hr", score: 82, skills: ["Python", "API"] },
              { id: "~02", title: "Next.js dashboard for a small shop", pay: "$1,500 fixed", score: 64, skills: ["React", "Next.js"] },
              { id: "~03", title: "Data cleanup in spreadsheets", pay: "$25/hr", score: 23, skills: ["Excel"] },
            ],
          });
      }
    },
    async connectTelegram(token: string | null, _runId: number, _onEvent: (e: SidecarEvent) => void): Promise<RunOutcome> {
      if (token !== null) {
        tokensSeen.push(token);
        heldToken = token;
      }
      await wait(2);
      if (heldToken === null) return failure("telegram-connect", "no_token", "no token given");
      if (!/^\d{5,}:[A-Za-z0-9_-]{20,}$/.test(heldToken)) {
        heldToken = null;
        return failure("telegram-connect", "bad_token", "not a bot token");
      }
      telegramAttempts++;
      if (options.telegramNeedsStart && telegramAttempts === 1) return failure("telegram-connect", "no_message", "no message to the bot found");
      return success("telegram-connect", { bot: "my_gigradar_bot", chat_id: "424242", chat_name: "Ada", test_message: true });
    },
    async cancel(): Promise<void> {},
    async openUrl(): Promise<void> {},
    async close(): Promise<void> {
      calls.push({ name: "close", payload: null });
    },
  };
}
