<script lang="ts">
  import { WEBVIEW2_URL, getBackend } from "../lib/backend";
  import { progress } from "../lib/draft.svelte";
  import { Run } from "../lib/run.svelte";
  import { t } from "../lib/strings";
  import type { Key } from "../lib/strings";

  const MAX_TRIES = 3;
  const run = new Run();
  let tries = $state(0);

  const retrying = $derived(run.status === "running" && run.events.some((e) => e.event === "retry"));
  const failed = $derived(run.status === "done" && !run.ok);
  const code = $derived(run.error?.code ?? "");
  const message = $derived.by(() => {
    const known: Record<string, Key> = {
      window_failed: "upwork.error.window_failed",
      webview2_missing: "upwork.error.webview2_missing",
      blocked: "upwork.error.blocked",
      crash: "upwork.error.crash",
    };
    const key = known[code];
    return key ? t(key) : t("upwork.error.default", { message: run.error?.message ?? "" });
  });

  async function check(): Promise<void> {
    tries += 1;
    await run.start("upwork-check", {});
    progress.upworkOk = run.ok;
  }
</script>

<h1 tabindex="-1">{t("upwork.title")}</h1>
<p class="lead">{t("upwork.lead")}</p>
<div class="callout"><p>{t("upwork.note")}</p></div>

<div class="row">
  {#if !progress.upworkOk}
    <button type="button" class="primary" onclick={check} disabled={run.status === "running" || (failed && tries >= MAX_TRIES)}>
      {#if failed && tries < MAX_TRIES}{t("upwork.again", { n: tries + 1, max: MAX_TRIES })}{:else}{t("upwork.open")}{/if}
    </button>
  {/if}
</div>

<div role="status" aria-live="polite">
  {#if run.status === "running"}
    <p class="muted">{retrying ? t("upwork.retrying") : t("upwork.running")}</p>
    <div class="progress indeterminate" aria-hidden="true"><div></div></div>
  {:else if run.status === "done" && run.ok}
    <div class="callout ok"><p>{t("upwork.ok", { count: Number(run.result?.jobs_found ?? 0) })}</p></div>
  {/if}
</div>

{#if failed}
  <div class="callout bad" role="alert">
    <p><strong>{t("upwork.failedTitle")}</strong> {message}</p>
    {#if code === "webview2_missing"}
      <button type="button" onclick={() => getBackend().openUrl(WEBVIEW2_URL)}>{t("upwork.installWebview2")}</button>
    {/if}
  </div>
  {#if tries >= MAX_TRIES || code === "webview2_missing"}
    <div class="panel">
      <h2 style="margin-top: 0">{t("upwork.helpTitle")}</h2>
      <ul>
        <li>{t("upwork.help1")}</li>
        <li>{t("upwork.help2")}</li>
        <li>{t("upwork.help3")}</li>
      </ul>
      <button type="button" onclick={() => (progress.upworkSkipped = true)}>{t("upwork.skip")}</button>
    </div>
  {/if}
{/if}
