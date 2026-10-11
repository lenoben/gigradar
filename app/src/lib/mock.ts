import type { AppEvent, AppInfo, AppLink, Backend, CommandName, RunOutcome, SidecarEvent, SidecarResult } from "./backend";
import type { JobDetails } from "./jobs";
import type { SettingsData } from "./settings";

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
  /** The scheduled checks were turned away by Upwork and wait for the Check Upwork button. */
  upworkNeedsCheck: boolean;
  /** The link the app was "started" with (a notification click). */
  pendingLink: AppLink | null;
  /** A bot was connected before (Settings shows "Connected as @…"). */
  telegramConnected: boolean;
  /** Jobs scored below this are hidden in the list unless asked for (0 = no minimum). */
  minScore: number;
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
    upworkNeedsCheck: false,
    pendingLink: null,
    telegramConnected: true,
    minScore: 0,
  };
}

export interface MockBackend extends Backend {
  calls: { name: string; payload: unknown }[];
  tokensSeen: string[];
  /** Pretend the Rust side emitted an event (a tray check finished, a second start brought a link). */
  emit(event: AppEvent): void;
  /** Queue a link as if a notification had been clicked. */
  deliverLink(link: AppLink): void;
  settings: SettingsData;
  labels: Record<string, number | null>;
}

const SAMPLE_JOBS: JobDetails[] = [
  {
    id: "~01aa", title: "Build a Python tool that syncs invoices", url: "https://www.upwork.com/jobs/~01aa", pay: "Hourly $40–60/hr · Expert",
    skills: "Python, API, PostgreSQL", published: null, first_seen: "", score: 82, section: "Automation and scripting", matched: ["Python", "API"],
    label: null, description: "We need a reliable script that pulls invoices from our accounting tool every night and writes them to PostgreSQL. You will own the code from first commit to deployment and document how it runs.",
  },
  {
    id: "~02bb", title: "Next.js dashboard for a small shop", url: "https://www.upwork.com/jobs/~02bb", pay: "Fixed $1,500 · Intermediate",
    skills: "React, Next.js", published: null, first_seen: "", score: 64, section: "Web development", matched: ["React", "Next.js"],
    label: null, description: "A small online shop wants a dashboard with orders, stock and a few charts. The design is ready; we need the build.",
  },
  {
    id: "~03cc", title: "Data cleanup in spreadsheets", url: "https://www.upwork.com/jobs/~03cc", pay: "Hourly $25/hr",
    skills: "Excel", published: null, first_seen: "", score: 23, section: null, matched: [], label: null, description: "",
  },
];

function sampleSettings(connected: boolean, minScore: number): SettingsData {
  return {
    profile: { markdown: "## Web development\nI build web apps with TypeScript, React and Node.js.\n\n## Automation\nI write Python scripts that move data between services.\n" },
    preferences: { skills: ["Python", "TypeScript", "React"], constraints: "Remote only", min_hourly: 40, min_fixed: 500, min_score: minScore },
    searches: [
      { name: "python-automation-hourly", query: "python automation", job_type: "hourly" },
      { name: "react-dashboard-fixed", query: "react dashboard", job_type: "fixed" },
    ],
    notifications: { toast: true, telegram: { enabled: connected, connected, bot: connected ? "my_gigradar_bot" : null } },
    schedule: { interval_minutes: 30, quiet_start: null, quiet_end: null },
    storage: { keep_days: 30, keep_rated: true },
  };
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
  const settings = sampleSettings(options.telegramConnected, options.minScore);
  const labels: Record<string, number | null> = {};
  const listeners = new Map<AppEvent, Set<() => void>>();
  let pending: AppLink | null = options.pendingLink;
  let hintShown = false;
  const firstSeen = new Date(Date.now() - 95 * 60_000).toISOString();
  const now = Date.now();
  const jobs = SAMPLE_JOBS.map((job, index) => ({ ...job, first_seen: firstSeen, published: new Date(now - (3 + index * 21) * 3_600_000).toISOString() }));
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
      { name: "upwork_access", status: options.upworkNeedsCheck ? "WARN" : "OK", message: options.upworkNeedsCheck ? "Upwork needs a quick check: the automatic checks are paused" : "Upwork lets the checks through", fix: options.upworkNeedsCheck ? "open Gigradar and click Check Upwork" : null },
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
    settings,
    labels,
    emit(event: AppEvent): void {
      listeners.get(event)?.forEach((callback) => callback());
    },
    deliverLink(link: AppLink): void {
      pending = link;
      listeners.get("link-pending")?.forEach((callback) => callback());
    },
    async info(): Promise<AppInfo> {
      return { home: "C:\\Users\\you\\AppData\\Local\\Gigradar Data", taskName: "checks" };
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
          return success(name, { task_name: "checks", interval_minutes: settings.schedule.interval_minutes });
        case "settings-get":
          await wait(1);
          return success(name, { settings: structuredClone(settings) });
        case "settings-save": {
          await wait(1);
          const { section, data } = payload as { section: string; data: Record<string, any> };
          if (section === "profile") settings.profile = { markdown: data.markdown };
          else if (section === "preferences") settings.preferences = { skills: data.skills, constraints: data.constraints, min_hourly: data.min_hourly, min_fixed: data.min_fixed, min_score: data.min_score };
          else if (section === "searches") settings.searches = data.searches.map((s: any) => ({ name: s.name, query: s.query, job_type: s.job_type ?? null }));
          else if (section === "notifications") {
            const bot = data.telegram?.bot_username ?? settings.notifications.telegram.bot;
            settings.notifications = { toast: data.toast, telegram: { enabled: data.telegram.enabled, connected: settings.notifications.telegram.connected || data.telegram.useStoredToken === true, bot } };
          } else if (section === "schedule") settings.schedule = { interval_minutes: data.interval_minutes, quiet_start: data.quiet_start, quiet_end: data.quiet_end };
          else if (section === "storage") settings.storage = { keep_days: data.keep_days, keep_rated: data.keep_rated };
          return success(name, { section, written: ["gigradar.toml"], backup: null, ...(section === "schedule" ? { rescheduled: true } : {}) });
        }
        case "job": {
          await wait(1);
          const id = (payload as { id: string }).id;
          const job = jobs.find((j) => j.id === id);
          if (!job) return failure(name, "not_found", `no stored job ${id}`);
          return success(name, { job: { ...job, label: labels[id] ?? null } });
        }
        case "label": {
          await wait(1);
          const { id, value } = payload as { id: string; value: "up" | "down" | "clear" };
          labels[id] = value === "up" ? 1 : value === "down" ? -1 : null;
          return success(name, { job_id: id, label: value });
        }
        case "task-unregister":
          return success(name, { removed: true });
        case "run-once":
          await wait(5);
          return success(name, { exit_code: 0, meaning: "ok", jobs_seen_before: 30, jobs_seen_after: 33 });
        case "jobs": {
          await wait(1);
          const { run, includeBelow } = (payload ?? {}) as { run?: string; includeBelow?: boolean };
          const found = run ? jobs.slice(0, 2) : jobs;
          const shown = includeBelow ? found : found.filter((j) => j.score === null || j.score >= settings.preferences.min_score);
          return success(name, { jobs: shown.map((j) => ({ ...j, label: labels[j.id] ?? null, description: undefined })), min_score: settings.preferences.min_score });
        }
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
    async openUrl(url: string): Promise<void> {
      calls.push({ name: "open-url", payload: url });
    },
    async close(hintDone: boolean): Promise<void> {
      calls.push({ name: "close", payload: hintDone });
      if (!hintDone && !hintShown) {
        hintShown = true;
        listeners.get("close-hint")?.forEach((callback) => callback());
      }
    },
    async quit(): Promise<void> {
      calls.push({ name: "quit", payload: null });
    },
    async forgetToken(): Promise<void> {
      heldToken = null;
      calls.push({ name: "forget-token", payload: null });
    },
    async takePendingLink(): Promise<AppLink | null> {
      const link = pending;
      pending = null;
      return link;
    },
    async subscribe(event: AppEvent, callback: () => void): Promise<() => void> {
      const set = listeners.get(event) ?? new Set<() => void>();
      set.add(callback);
      listeners.set(event, set);
      return () => set.delete(callback);
    },
  };
}
