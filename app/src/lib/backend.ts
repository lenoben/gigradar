// The only door to the helper program (the packaged Python CLI). Every call goes through a Rust command that
// runs it and streams its JSON Lines back; this file is the TypeScript side of that contract.

export type CommandName =
  | "doctor"
  | "setup-apply"
  | "notify-test"
  | "upwork-check"
  | "model-download"
  | "task-register"
  | "task-unregister"
  | "run-once"
  | "jobs"
  | "job"
  | "label"
  | "settings-get"
  | "settings-save";

export interface SidecarEvent {
  event: string;
  [key: string]: unknown;
}

export interface SidecarError {
  code: string;
  message: string;
}

/** The final line the helper prints: `ok` plus command-specific fields. */
export interface SidecarResult {
  ok: boolean;
  command: string;
  error?: SidecarError;
  [key: string]: unknown;
}

/** `result` is null when the helper died without printing one (a crash); then only the exit code is known. */
export interface RunOutcome {
  exitCode: number | null;
  result: SidecarResult | null;
}

export interface AppInfo {
  home: string;
  taskName: string;
}

/** What a notification click asks for (the Rust side parsed and checked the gigradar:// link). */
export type AppLink = { kind: "job"; id: string } | { kind: "jobs"; run: string | null } | { kind: "status" };

/** Things the Rust side tells the window about. */
export type AppEvent = "link-pending" | "check-finished" | "close-hint";

export interface Backend {
  info(): Promise<AppInfo>;
  run(name: CommandName, payload: unknown, runId: number, onEvent: (event: SidecarEvent) => void): Promise<RunOutcome>;
  /**
   * The token goes to the helper's stdin and is kept in the Rust process only, never in the UI. `null` means
   * "use the token already given" (the second try, after the user sent /start to the bot).
   */
  connectTelegram(token: string | null, runId: number, onEvent: (event: SidecarEvent) => void): Promise<RunOutcome>;
  cancel(runId: number): Promise<void>;
  openUrl(url: string): Promise<void>;
  /**
   * Hide the window; the app stays in the tray and the scheduled checks are not affected. The first time ever the
   * Rust side answers with the "close-hint" event instead; then close(true) after the user has read it.
   */
  close(hintDone: boolean): Promise<void>;
  /** End the app (tray icon included). The scheduled checks keep running. */
  quit(): Promise<void>;
  /** The Rust side drops a bot token it was holding (the user gave up changing the bot). */
  forgetToken(): Promise<void>;
  /** The link the app was started with (or a second start passed on), once. */
  takePendingLink(): Promise<AppLink | null>;
  /** Calls `callback` whenever the Rust side emits `event`; resolves to the function that stops listening. */
  subscribe(event: AppEvent, callback: () => void): Promise<() => void>;
}

let backend: Backend | null = null;
let nextRunId = 1;

export function setBackend(value: Backend): void {
  backend = value;
}

export function getBackend(): Backend {
  if (backend === null) throw new Error("no backend set");
  return backend;
}

export function newRunId(): number {
  return nextRunId++;
}

export const WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703";
export const BOTFATHER_URL = "https://t.me/BotFather";
