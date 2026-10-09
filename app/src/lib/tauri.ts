import { Channel, invoke } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import type { AppInfo, Backend, CommandName, RunOutcome, SidecarEvent } from "./backend";

function streamTo(onEvent: (event: SidecarEvent) => void): Channel<SidecarEvent> {
  const channel = new Channel<SidecarEvent>();
  channel.onmessage = onEvent;
  return channel;
}

/** The real backend: Rust commands that run the bundled helper program. */
export const tauriBackend: Backend = {
  info: () => invoke<AppInfo>("app_info"),
  run: (name: CommandName, payload: unknown, runId: number, onEvent) =>
    invoke<RunOutcome>("run_command", { name, payload, runId, onEvent: streamTo(onEvent) }),
  connectTelegram: (token: string | null, runId: number, onEvent) =>
    invoke<RunOutcome>("telegram_connect", { token, runId, onEvent: streamTo(onEvent) }),
  cancel: (runId: number) => invoke<void>("cancel_command", { runId }),
  openUrl: (url: string) => openUrl(url),
  close: () => invoke<void>("close_app"),
};
