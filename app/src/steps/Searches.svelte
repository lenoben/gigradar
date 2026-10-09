<script lang="ts">
  import { MAX_SEARCHES, draft } from "../lib/draft.svelte";
  import { t } from "../lib/strings";

  function add(): void {
    if (draft.searches.length < MAX_SEARCHES) draft.searches.push({ keywords: "", type: "hourly" });
  }

  function remove(index: number): void {
    draft.searches.splice(index, 1);
  }

  const anyKeywords = $derived(draft.searches.some((s) => s.keywords.trim() !== ""));
</script>

<h1 tabindex="-1">{t("search.title")}</h1>
<p class="lead">{t("search.lead", { max: MAX_SEARCHES })}</p>
<p class="hint">{t("search.note")}</p>

{#each draft.searches as search, index (index)}
  <fieldset class="panel" style="border: 1px solid var(--line)">
    <legend class="label" style="margin: 0; padding: 0 6px">{t("search.n", { n: index + 1 })}</legend>
    <label for={`kw-${index}`} style="margin-top: 0.2rem">{t("search.keywords")}</label>
    <input id={`kw-${index}`} type="text" bind:value={search.keywords} placeholder={t("search.keywordsPlaceholder")} />
    <div role="radiogroup" aria-label={t("search.type")} class="row" style="margin-top: 0.8rem">
      <span class="label" style="margin: 0">{t("search.type")}</span>
      <label class="row" style="margin: 0; font-weight: 400">
        <input type="radio" name={`type-${index}`} value="hourly" bind:group={search.type} /> {t("search.hourly")}
      </label>
      <label class="row" style="margin: 0; font-weight: 400">
        <input type="radio" name={`type-${index}`} value="fixed" bind:group={search.type} /> {t("search.fixed")}
      </label>
      <label class="row" style="margin: 0; font-weight: 400">
        <input type="radio" name={`type-${index}`} value="both" bind:group={search.type} /> {t("search.both")}
      </label>
    </div>
    {#if draft.searches.length > 1}
      <div style="margin-top: 0.6rem">
        <button type="button" class="link" onclick={() => remove(index)} aria-label={t("search.remove", { n: index + 1 })}>
          {t("search.remove", { n: index + 1 })}
        </button>
      </div>
    {/if}
  </fieldset>
{/each}

{#if draft.searches.length < MAX_SEARCHES}
  <button type="button" onclick={add}>{t("search.add")}</button>
{/if}
{#if !anyKeywords}
  <p class="hint">{t("search.needOne")}</p>
{/if}
