<script lang="ts">
  import { INTERVALS } from "../lib/settings";
  import type { ScheduleDraft } from "../lib/settings";
  import { t } from "../lib/strings";
  import type { Key } from "../lib/strings";

  let { schedule }: { schedule: ScheduleDraft } = $props();

  const invalid = $derived(schedule.quiet && schedule.start === schedule.end);
</script>

<h1 tabindex="-1">{t("schedule.title")}</h1>
<p class="lead">{t("schedule.lead")}</p>

<div role="radiogroup" aria-label={t("schedule.interval")}>
  <span class="label">{t("schedule.interval")}</span>
  {#each INTERVALS as minutes (minutes)}
    <label class="row choice">
      <input type="radio" name="interval" value={minutes} bind:group={schedule.interval} />
      {t(`schedule.opt.${minutes}` as Key)}
    </label>
  {/each}
</div>

<label class="switch" for="quiet">
  <input id="quiet" type="checkbox" role="switch" bind:checked={schedule.quiet} />
  <span>
    <span class="title">{t("schedule.quietOn")}</span><br />
    <span class="hint">{t("schedule.quietHint")}</span>
  </span>
</label>

{#if schedule.quiet}
  <div class="row" style="margin-left: 34px">
    <div>
      <label for="quiet-from">{t("schedule.from")}</label>
      <input id="quiet-from" type="time" bind:value={schedule.start} aria-invalid={invalid} />
    </div>
    <div>
      <label for="quiet-to">{t("schedule.to")}</label>
      <input id="quiet-to" type="time" bind:value={schedule.end} aria-invalid={invalid} />
    </div>
  </div>
  {#if invalid}<p class="hint error-text" style="margin-left: 34px">{t("schedule.quietInvalid")}</p>{/if}
{/if}
