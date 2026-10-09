<script lang="ts">
  import { amountValid } from "../lib/answers";
  import { draft } from "../lib/draft.svelte";
  import { t } from "../lib/strings";

  let skillInput = $state("");

  function addSkill(): void {
    const name = skillInput.trim();
    if (name !== "" && !draft.skills.some((s) => s.toLowerCase() === name.toLowerCase())) draft.skills.push(name);
    skillInput = "";
  }

  function onKey(event: KeyboardEvent): void {
    if (event.key === "Enter") {
      event.preventDefault();
      addSkill();
    }
  }

  function removeSkill(name: string): void {
    draft.skills = draft.skills.filter((s) => s !== name);
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
{#if draft.skills.length > 0}
  <ul class="chips" aria-label={t("prefs.skills")}>
    {#each draft.skills as skill (skill)}
      <li class="chip">
        {skill}
        <button type="button" onclick={() => removeSkill(skill)} aria-label={t("prefs.removeSkill", { name: skill })}>×</button>
      </li>
    {/each}
  </ul>
{/if}

<label for="constraints">{t("prefs.constraints")}</label>
<input id="constraints" type="text" bind:value={draft.constraints} placeholder={t("prefs.constraintsPlaceholder")} aria-describedby="constraints-hint" />
<p class="hint" id="constraints-hint">{t("prefs.constraintsHint")}</p>

<div class="row" style="align-items: flex-start; gap: 20px">
  <div style="flex: 1; min-width: 200px">
    <label for="min-hourly">{t("prefs.minHourly")}</label>
    <input id="min-hourly" type="text" inputmode="decimal" bind:value={draft.minHourly} aria-invalid={!amountValid(draft.minHourly)} />
    {#if !amountValid(draft.minHourly)}<p class="hint error-text">{t("prefs.number")}</p>{/if}
  </div>
  <div style="flex: 1; min-width: 200px">
    <label for="min-fixed">{t("prefs.minFixed")}</label>
    <input id="min-fixed" type="text" inputmode="decimal" bind:value={draft.minFixed} aria-invalid={!amountValid(draft.minFixed)} />
    {#if !amountValid(draft.minFixed)}<p class="hint error-text">{t("prefs.number")}</p>{/if}
  </div>
</div>
