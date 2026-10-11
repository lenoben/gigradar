<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { getBackend } from "../lib/backend";
  import type { JobRow } from "../lib/jobs";
  import { closeJob, nav, openJob } from "../lib/nav.svelte";
  import { Run } from "../lib/run.svelte";
  import { t } from "../lib/strings";
  import { ago } from "../lib/time";
  import JobCard from "./JobCard.svelte";
  import LabelButtons from "./LabelButtons.svelte";

  // The list of jobs (all of them, or the ones one check announced) and, when one is chosen, its card.
  const LIMIT = 50;
  const run = new Run();
  const labelRun = new Run();
  let labels = $state<Record<string, number | null>>({});
  let busyId = $state<string | null>(null);
  let labelError = $state("");
  // Jobs scored below the minimum score are stored but not announced; the list hides them unless this is on.
  let showBelow = $state(false);
  let minScore = $state(0);

  const rows = $derived((run.result?.jobs as JobRow[] | undefined) ?? []);
  const below = (row: JobRow): boolean => row.score !== null && row.score < minScore;

  async function load(): Promise<void> {
    const payload: Record<string, unknown> = nav.run ? { limit: LIMIT, run: nav.run } : { limit: LIMIT };
    if (showBelow) payload.includeBelow = true;
    await run.start("jobs", payload);
    minScore = Number(run.result?.min_score ?? 0);
    labels = Object.fromEntries(rows.map((r) => [r.id, r.label]));
  }

  async function setLabel(row: JobRow, value: "up" | "down" | "clear"): Promise<void> {
    labelError = "";
    busyId = row.id;
    await labelRun.start("label", { id: row.id, value });
    if (labelRun.ok) labels[row.id] = value === "up" ? 1 : value === "down" ? -1 : null;
    else labelError = t("jobs.labelFailed", { message: labelRun.error?.message ?? "" });
    busyId = null;
  }

  function showAll(): void {
    nav.run = null;
    void load();
  }

  // the list reloads when it is first shown and whenever the digest filter or the toggle changes; a card does not need it
  $effect(() => {
    void nav.run;
    void showBelow;
    if (nav.jobId === null) untrack(() => void load());
  });

  onMount(() => {
    // a finished check from the tray may have found new jobs
    let stop = () => {};
    void getBackend().subscribe("check-finished", () => nav.jobId === null && void load()).then((off) => (stop = off));
    return () => stop();
  });
</script>

{#if nav.jobId !== null}
  <JobCard id={nav.jobId} onback={closeJob} />
{:else}
  <h1 tabindex="-1">{t("jobs.title")}</h1>
  {#if nav.run}
    <div class="callout" role="status">
      <p>{t("jobs.runBanner")}</p>
      <button type="button" onclick={showAll}>{t("jobs.showAll")}</button>
    </div>
  {/if}

  {#if minScore > 0}
    <label class="switch" for="show-below">
      <input id="show-below" type="checkbox" role="switch" bind:checked={showBelow} />
      <span class="title">{t("jobs.showBelow")}</span>
    </label>
  {/if}

  {#if run.status !== "done"}
    <p role="status" class="muted">{t("jobs.loading")}</p>
  {:else if !run.ok}
    <div class="callout bad" role="alert"><p>{t("jobs.loadFailed", { message: run.error?.message || run.error?.code || "" })}</p></div>
  {:else if rows.length === 0}
    <p class="muted">{t("jobs.empty")}</p>
  {:else}
    <ul class="joblist">
      {#each rows as row (row.id)}
        <li class:dim={below(row)}>
          <span class="score" class:none={row.score === null} role="img" aria-label={row.score === null ? t("jobs.noScore") : t("jobs.score", { score: row.score })}>
            {row.score === null ? "–" : row.score}
          </span>
          <div class="body">
            <button type="button" class="link title" onclick={() => openJob(row.id)}>{row.title}</button>
            {#if below(row)}<span class="tag">{t("jobs.belowTag")}</span>{/if}
            <div class="muted small">{[row.pay, ago(row.published ?? row.first_seen, new Date())].filter(Boolean).join(" · ")}</div>
          </div>
          <div class="row tools">
            <button type="button" onclick={() => void getBackend().openUrl(row.url)}>{t("jobs.open")}</button>
            <LabelButtons label={labels[row.id] ?? null} busy={busyId === row.id} onchange={(value) => setLabel(row, value)} />
          </div>
        </li>
      {/each}
    </ul>
    {#if labelError}<p class="error-text small" role="alert">{labelError}</p>{/if}
  {/if}
{/if}
