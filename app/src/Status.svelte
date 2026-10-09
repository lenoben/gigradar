<script lang="ts">
  import { onMount } from "svelte";
  import { WEBVIEW2_URL, getBackend } from "./lib/backend";
  import type { CommandName } from "./lib/backend";
  import { Run } from "./lib/run.svelte";
  import { t } from "./lib/strings";
  import type { Key } from "./lib/strings";

  let { home, onsetup }: { home: string; onsetup: () => void } = $props();

  type Level = "OK" | "WARN" | "FAIL";
  interface CheckRow {
    name: string;
    status: Level;
    message: string;
    fix: string | null;
    details?: string[];
    data?: { next_run?: string | null } | null;
  }
  type FixAction = { label: Key; run: () => void };

  const ICON: Record<Level, string> = { OK: "✓", WARN: "!", FAIL: "✕" };
  const KNOWN = ["config", "profile", "telegram_token", "telegram_api", "telegram_chat", "webview2_runtime", "webview_profile", "model", "store", "scheduled_task", "recent_errors"];

  const run = new Run();
  const fixRun = new Run();
  let fixing = $state<string | null>(null);
  let fixFailed = $state<string | null>(null);

  const checks = $derived((run.result?.checks as CheckRow[] | undefined) ?? []);
  const attention = $derived(checks.filter((c) => c.status !== "OK").length);
  const nextRun = $derived(checks.find((c) => c.name === "scheduled_task")?.data?.next_run ?? null);

  const summary = $derived.by(() => {
    if (run.status !== "done" || checks.length === 0) return t("status.checking");
    if (attention === 1) return t("status.summary.one");
    if (attention > 1) return t("status.summary.many", { n: attention });
    if (nextRun) return t("status.summary.ok", { time: new Date(nextRun).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) });
    return t("status.summary.okNoTime");
  });

  function title(name: string): string {
    return KNOWN.includes(name) ? t(`check.${name}` as Key) : name;
  }

  function fixText(row: CheckRow): string | null {
    if (row.status === "OK") return null;
    return KNOWN.includes(row.name) ? t(`fix.${row.name}` as Key) : row.fix;
  }

  async function inline(name: string, command: CommandName, payload: unknown): Promise<void> {
    fixing = name;
    fixFailed = null;
    await fixRun.start(command, payload);
    if (!fixRun.ok) fixFailed = `${name}: ${fixRun.error?.message || fixRun.error?.code || ""}`;
    fixing = null;
    await recheck();
  }

  function action(row: CheckRow): FixAction | null {
    if (row.status === "OK") return null;
    switch (row.name) {
      case "config":
      case "profile":
      case "telegram_token":
      case "telegram_api":
      case "telegram_chat":
        return { label: "fix.btn.setup", run: onsetup };
      case "webview2_runtime":
        return { label: "fix.btn.webview2", run: () => void getBackend().openUrl(WEBVIEW2_URL) };
      case "webview_profile":
        return { label: "fix.btn.upwork", run: () => void inline(row.name, "upwork-check", {}) };
      case "model":
        return { label: "fix.btn.model", run: () => void inline(row.name, "model-download", {}) };
      case "scheduled_task":
        return { label: "fix.btn.task", run: () => void inline(row.name, "task-register", { interval: 30 }) };
      default:
        return null;
    }
  }

  async function recheck(): Promise<void> {
    await run.start("doctor", { offline: false });
  }

  onMount(recheck);
</script>

<h2 aria-live="polite" class="summary" class:good={run.status === "done" && attention === 0}>{summary}</h2>

<div class="row">
  <button type="button" onclick={recheck} disabled={run.status === "running" || fixing !== null}>
    {run.status === "running" ? t("status.checking") : t("status.recheck")}
  </button>
  <button type="button" onclick={onsetup}>{t("status.setupAgain")}</button>
</div>

{#if run.status === "done" && run.error?.code === "crash"}
  <div class="callout bad" role="alert"><p>{t("error.crash", { code: run.error.message })}</p></div>
{/if}
{#if fixFailed}
  <div class="callout bad" role="alert"><p>{t("error.generic", { message: fixFailed })}</p></div>
{/if}

<ul class="status-list">
  {#each checks as row (row.name)}
    {@const fix = fixText(row)}
    {@const act = action(row)}
    <li>
      <span class={`pill ${row.status}`}><span aria-hidden="true">{ICON[row.status]}</span> {t(`status.level.${row.status}` as Key)}</span>
      <div class="body">
        <div class="name">{title(row.name)}</div>
        <div class="muted small">{row.message}</div>
        {#if row.details && row.details.length > 1}
          <ul class="small muted details">
            {#each row.details as line (line)}<li>{line}</li>{/each}
          </ul>
        {/if}
        {#if fix}<div class="small">{fix}</div>{/if}
      </div>
      {#if act}
        <button type="button" class="fixbtn" onclick={act.run} disabled={fixing !== null || run.status === "running"}>
          {fixing === row.name ? t("fix.working") : t(act.label)}
        </button>
      {/if}
    </li>
  {/each}
</ul>

<p class="muted small">{t("status.home", { home })}</p>
