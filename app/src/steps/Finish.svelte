<script lang="ts">
  import { getBackend } from "../lib/backend";
  import { clearSavedDraft } from "../lib/draft.svelte";
  import { Run } from "../lib/run.svelte";
  import { t } from "../lib/strings";

  let { onopenstatus }: { onopenstatus: () => void } = $props();

  type Phase = "pending" | "running" | "done" | "failed";
  const schedule = new Run();
  const first = new Run();
  const jobsRun = new Run();
  let started = $state(false);
  let failure = $state("");

  interface JobRow {
    id: string;
    title: string;
    pay?: string;
    score?: number | null;
  }

  const phases = $derived<[string, Phase][]>([
    [t("finish.stepSchedule"), phaseOf(schedule)],
    [t("finish.stepRun"), phaseOf(first)],
    [t("finish.stepJobs"), phaseOf(jobsRun)],
  ]);
  const allDone = $derived(jobsRun.status === "done" && jobsRun.ok);
  const jobs = $derived((jobsRun.result?.jobs as JobRow[] | undefined) ?? []);

  function phaseOf(run: Run): Phase {
    if (run.status === "idle") return "pending";
    if (run.status === "running") return "running";
    return run.ok ? "done" : "failed";
  }

  function reason(run: Run): string {
    return run.error?.message || String(run.result?.meaning ?? "") || String(run.error?.code ?? "");
  }

  async function finish(): Promise<void> {
    started = true;
    failure = "";
    if (!schedule.ok) {
      await schedule.start("task-register", { interval: 30 });
      if (!schedule.ok) {
        failure = t("finish.errorSchedule", { message: reason(schedule) });
        return;
      }
    }
    if (!first.ok) {
      await first.start("run-once", {});
      if (!first.ok) {
        failure = t("finish.errorRun", { message: reason(first) });
        return;
      }
    }
    await jobsRun.start("jobs", { limit: 5 });
    if (jobsRun.ok) clearSavedDraft();
  }

  const label: Record<Phase, () => string> = {
    pending: () => t("finish.pending"),
    running: () => t("finish.running"),
    done: () => t("finish.done"),
    failed: () => t("finish.failed"),
  };
</script>

{#if allDone}
  <h1 tabindex="-1">{t("finish.doneTitle")}</h1>
  <p class="lead">{t("finish.doneBody")}</p>
  <p>{t("finish.firstRun", { seen: Number(first.result?.jobs_seen_after ?? 0) })}</p>
  <h2>{t("finish.latest")}</h2>
  {#if jobs.length === 0}
    <p class="muted">{t("finish.noJobs")}</p>
  {:else}
    <ul class="jobs">
      {#each jobs as job (job.id)}
        <li>
          <span>{job.title}{#if job.pay}<br /><span class="muted small">{job.pay}</span>{/if}</span>
          <span class="muted small">{job.score == null ? t("finish.noScore") : t("finish.score", { score: job.score })}</span>
        </li>
      {/each}
    </ul>
  {/if}
  <div class="row" style="margin-top: 1rem">
    <button type="button" class="primary" onclick={() => void getBackend().close()}>{t("finish.close")}</button>
    <button type="button" onclick={onopenstatus}>{t("finish.openStatus")}</button>
  </div>
{:else}
  <h1 tabindex="-1">{t("finish.title")}</h1>
  <p class="lead">{t("finish.lead")}</p>

  <ul class="checklist" aria-live="polite">
    {#each phases as [name, phase] (name)}
      <li><span>{name}</span><span class={`badge ${phase}`}>{label[phase]()}</span></li>
    {/each}
  </ul>

  {#if failure}
    <div class="callout bad" role="alert"><p>{failure}</p></div>
  {/if}
  <button type="button" class="primary" onclick={finish} disabled={started && !failure}>
    {failure ? t("finish.retry") : t("finish.start")}
  </button>
{/if}
