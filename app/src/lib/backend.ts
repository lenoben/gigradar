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
  | "jobs";

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
  /** Quit the app (checks keep running in the background). */
  close(): Promise<void>;
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
