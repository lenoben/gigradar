import { getBackend, newRunId } from "./backend";
import type { CommandName, RunOutcome, SidecarError, SidecarEvent } from "./backend";

/** One helper-program call whose events and outcome a screen can show. */
export class Run {
  status = $state<"idle" | "running" | "done">("idle");
  events = $state<SidecarEvent[]>([]);
  outcome = $state<RunOutcome | null>(null);
  private runId = 0;

  get ok(): boolean {
    return this.outcome?.result?.ok === true;
  }

  /** The error to show: the helper's own, or `crash` when it died without an answer. */
  get error(): SidecarError | null {
    if (this.outcome === null || this.ok) return null;
    if (this.outcome.result === null) {
      return { code: "crash", message: String(this.outcome.exitCode ?? "") };
    }
    return this.outcome.result.error ?? { code: "unknown", message: "" };
  }

  get result(): Record<string, unknown> | null {
    return this.outcome?.result ?? null;
  }

  get last(): SidecarEvent | null {
    return this.events.length > 0 ? this.events[this.events.length - 1] : null;
  }

  private begin(): number {
    this.status = "running";
    this.events = [];
    this.outcome = null;
    this.runId = newRunId();
    return this.runId;
  }

  private finish(outcome: RunOutcome): RunOutcome {
    this.outcome = outcome;
    this.status = "done";
    return outcome;
  }

  async start(name: CommandName, payload: unknown): Promise<RunOutcome> {
    const id = this.begin();
    try {
      return this.finish(await getBackend().run(name, payload, id, (e) => this.events.push(e)));
    } catch (failure) {
      return this.finish({ exitCode: null, result: { ok: false, command: name, error: { code: "backend", message: describe(failure) } } });
    }
  }

  async startTelegram(token: string | null): Promise<RunOutcome> {
    const id = this.begin();
    try {
      return this.finish(await getBackend().connectTelegram(token, id, (e) => this.events.push(e)));
    } catch (failure) {
      return this.finish({ exitCode: null, result: { ok: false, command: "telegram-connect", error: { code: "backend", message: describe(failure) } } });
    }
  }

  async cancel(): Promise<void> {
    if (this.status === "running") await getBackend().cancel(this.runId);
  }
}

function describe(failure: unknown): string {
  return failure instanceof Error ? failure.message : String(failure);
}
