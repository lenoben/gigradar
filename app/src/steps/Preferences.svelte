<script lang="ts">
  import { amountValid, minScoreValid } from "../lib/answers";
  import { draft } from "../lib/draft.svelte";
  import type { Draft } from "../lib/draft.svelte";
  import { t } from "../lib/strings";

  let { model = draft }: { model?: Draft } = $props();

  let skillInput = $state("");

  function addSkill(): void {
    const name = skillInput.trim();
    if (name !== "" && !model.skills.some((s) => s.toLowerCase() === name.toLowerCase())) model.skills.push(name);
    skillInput = "";
  }

  function onKey(event: KeyboardEvent): void {
    if (event.key === "Enter") {
      event.preventDefault();
      addSkill();
    }
  }

  function removeSkill(name: string): void {
    model.skills = model.skills.filter((s) => s !== name);
  }
</script>

<h1 tabindex="-1">{t("prefs.title")}</h1>
<p class="lead">{t("prefs.lead")}</p>

<label for="skill">{t("prefs.skills")}</label>
<div class="row">
  <input id="skill" type="text" bind:value={skillInput} onkeydown={onKey} placeholder={t("prefs.skillsPlaceholder")} aria-describedby="skill-hint" style="flex: 1" />
  <button type="button" onclick={addSkill} disabled={skillInput.trim() === ""}>{t("prefs.addSkill")}</button>
</div>
<p class="hint" id="skill-hint">{t("prefs.skillsHint")}</p>
{#if model.skills.length > 0}
  <ul class="chips" aria-label={t("prefs.skills")}>
    {#each model.skills as skill (skill)}
      <li class="chip">
        {skill}
        <button type="button" onclick={() => removeSkill(skill)} aria-label={t("prefs.removeSkill", { name: skill })}>×</button>
      </li>
    {/each}
  </ul>
{/if}

<label for="constraints">{t("prefs.constraints")}</label>
<input id="constraints" type="text" bind:value={model.constraints} placeholder={t("prefs.constraintsPlaceholder")} aria-describedby="constraints-hint" />
<p class="hint" id="constraints-hint">{t("prefs.constraintsHint")}</p>

<div class="row" style="align-items: flex-start; gap: 20px">
  <div style="flex: 1; min-width: 200px">
    <label for="min-hourly">{t("prefs.minHourly")}</label>
    <input id="min-hourly" type="text" inputmode="decimal" bind:value={model.minHourly} aria-invalid={!amountValid(model.minHourly)} />
    {#if !amountValid(model.minHourly)}<p class="hint error-text">{t("prefs.number")}</p>{/if}
  </div>
  <div style="flex: 1; min-width: 200px">
    <label for="min-fixed">{t("prefs.minFixed")}</label>
    <input id="min-fixed" type="text" inputmode="decimal" bind:value={model.minFixed} aria-invalid={!amountValid(model.minFixed)} />
    {#if !amountValid(model.minFixed)}<p class="hint error-text">{t("prefs.number")}</p>{/if}
  </div>
</div>

<label for="min-score">{t("prefs.minScore")}</label>
<input id="min-score" type="text" inputmode="numeric" style="max-width: 120px" bind:value={model.minScore} aria-invalid={!minScoreValid(model.minScore)} aria-describedby="min-score-hint" />
<p class="hint" id="min-score-hint">{t("prefs.minScoreHint")}</p>
{#if !minScoreValid(model.minScore)}<p class="hint error-text">{t("prefs.minScoreInvalid")}</p>{/if}
