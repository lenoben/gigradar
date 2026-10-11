<script lang="ts">
  import { progress } from "../lib/draft.svelte";
  import { Run } from "../lib/run.svelte";
  import { t } from "../lib/strings";

  const run = new Run();

  const last = $derived([...run.events].reverse().find((e) => e.event === "progress"));
  const percent = $derived(typeof last?.percent === "number" ? last.percent : null);
  const verifying = $derived(run.events.some((e) => e.event === "verify"));
  const megabytes = $derived(Math.round(Number(last?.bytes ?? 0) / 1_000_000));
  const failed = $derived(run.status === "done" && !run.ok);

  async function download(): Promise<void> {
    await run.start("model-download", {});
    progress.modelOk = run.ok;
  }
</script>

<h1 tabindex="-1">{t("model.title")}</h1>
<p class="lead">{t("model.lead")}</p>

{#if !progress.modelOk && run.status !== "running"}
  <button type="button" class="primary" onclick={download}>{failed ? t("model.retry") : t("model.start")}</button>
{/if}

<div role="status" aria-live="polite">
  {#if run.status === "running"}
    <p class="muted">
      {#if verifying}{t("model.verify")}{:else if percent !== null}{t("model.progress", { percent })}{:else}{t("model.progressUnknown", { mb: megabytes })}{/if}
    </p>
    <div
      class="progress"
      class:indeterminate={percent === null}
      role="progressbar"
      aria-label={t("model.title")}
      aria-valuemin="0"
      aria-valuemax="100"
      aria-valuenow={percent ?? undefined}
    >
      <div style:width={percent !== null ? `${verifying ? 100 : percent}%` : undefined}></div>
    </div>
  {:else if progress.modelOk}
    <div class="callout ok"><p>{t("model.ok")}</p></div>
  {/if}
</div>

{#if failed}
  <div class="callout bad" role="alert">
    <p>{run.error?.code === "crash" ? t("model.error.crash") : t("model.failed", { message: run.error?.message ?? "" })}</p>
  </div>
{/if}
