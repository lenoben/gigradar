<script lang="ts">
  import { onMount } from "svelte";
  import { getBackend } from "../lib/backend";
  import type { JobDetails } from "../lib/jobs";
  import { Run } from "../lib/run.svelte";
  import { t } from "../lib/strings";
  import { ago } from "../lib/time";
  import LabelButtons from "./LabelButtons.svelte";

  // The card a notification opens (or a row of the list): what the job is, how well it fits, and what to do next.
  let { id, onback }: { id: string; onback: (() => void) | null } = $props();

  const load = new Run();
  const labelRun = new Run();
  let label = $state<number | null>(null);
  let labelError = $state("");

  const job = $derived((load.result?.job as JobDetails | undefined) ?? null);
  const notFound = $derived(load.status === "done" && load.error?.code === "not_found");
  const posted = $derived(job ? ago(job.published ?? job.first_seen, new Date()) : "");
  const found = $derived(job ? ago(job.first_seen, new Date()) : "");

  onMount(async () => {
    await load.start("job", { id });
    label = job?.label ?? null;
  });

  async function setLabel(value: "up" | "down" | "clear"): Promise<void> {
    labelError = "";
    await labelRun.start("label", { id, value });
    if (labelRun.ok) label = value === "up" ? 1 : value === "down" ? -1 : null;
    else labelError = t("jobs.labelFailed", { message: labelRun.error?.message ?? "" });
  }
</script>

{#if onback}
  <button type="button" class="link" onclick={onback}>← {t("jobs.back")}</button>
{/if}

{#if load.status !== "done"}
  <p role="status" class="muted">{t("jobs.loading")}</p>
{:else if notFound}
  <div class="callout" role="status"><p>{t("jobs.notFound")}</p></div>
{:else if job === null}
  <div class="callout bad" role="alert"><p>{t("jobs.loadFailed", { message: load.error?.message ?? "" })}</p></div>
{:else}
  <article class="jobcard" aria-labelledby="job-title">
    <div class="jobhead">
      <span class="score" class:none={job.score === null} role="img" aria-label={job.score === null ? t("jobs.noScore") : t("jobs.score", { score: job.score })}>
        {job.score === null ? "–" : job.score}
      </span>
      <h1 id="job-title" tabindex="-1">{job.title}</h1>
    </div>

    <dl class="facts">
      {#if job.pay}<div><dt>{t("jobs.pay")}</dt><dd>{job.pay}</dd></div>{/if}
      {#if posted}<div><dt>{t("jobs.postedLabel")}</dt><dd>{posted}</dd></div>{/if}
      {#if found && job.published}<div><dt>{t("jobs.foundLabel")}</dt><dd>{found}</dd></div>{/if}
      {#if job.section}<div><dt>{t("jobs.bestFit")}</dt><dd>{job.section}</dd></div>{/if}
    </dl>

    {#if job.matched.length > 0}
      <h2>{t("jobs.matched")}</h2>
      <ul class="chips">
        {#each job.matched as skill (skill)}<li class="chip static">{skill}</li>{/each}
      </ul>
    {/if}

    <h2>{t("jobs.description")}</h2>
    {#if job.description}
      <p class="description">{job.description}</p>
    {:else}
      <p class="muted">{t("jobs.noDescription")}</p>
    {/if}

    <div class="row actions">
      <button type="button" class="primary" onclick={() => void getBackend().openUrl(job.url)}>{t("jobs.open")}</button>
      <LabelButtons {label} busy={labelRun.status === "running"} onchange={setLabel} />
    </div>
    {#if labelError}<p class="error-text small" role="alert">{labelError}</p>{/if}
  </article>
{/if}
