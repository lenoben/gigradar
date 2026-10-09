import { draft } from "./draft.svelte";
import type { Run } from "./run.svelte";

export type TelegramState =
  | { kind: "idle" }
  | { kind: "connected"; bot: string }
  | { kind: "needs_start" }
  | { kind: "error"; code: string; message: string };

/**
 * Hands the typed token to the Rust side and forgets it. The caller passes how to read and how to clear the input
 * element: the field is emptied BEFORE the call is awaited, and the token only ever lives in this function's argument
 * list, never in the draft, a store, the DOM after submit, a log or the saved draft.
 */
export async function connectWithToken(readInput: () => string, clearInput: () => void, run: Run): Promise<TelegramState> {
  const token = readInput().trim();
  clearInput();
  if (token === "") return { kind: "error", code: "empty", message: "" };
  await run.startTelegram(token);
  return interpret(run);
}

/** The second try, after the user sent /start to the bot: the Rust side still holds the token. */
export async function retryChat(run: Run): Promise<TelegramState> {
  await run.startTelegram(null);
  return interpret(run);
}

function interpret(run: Run): TelegramState {
  if (run.ok) {
    const bot = String(run.result?.bot ?? "");
    draft.telegram.connected = true;
    draft.telegram.botName = bot;
    draft.telegram.chatId = String(run.result?.chat_id ?? "");
    return { kind: "connected", bot };
  }
  draft.telegram.connected = false;
  const error = run.error ?? { code: "unknown", message: "" };
  if (error.code === "no_message") return { kind: "needs_start" };
  return { kind: "error", code: error.code, message: error.message };
}
