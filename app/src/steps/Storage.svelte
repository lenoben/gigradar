<script lang="ts">
  import { KEEP_DAYS } from "../lib/settings";
  import type { StorageDraft } from "../lib/settings";
  import { t } from "../lib/strings";
  import type { Key } from "../lib/strings";

  let { storage }: { storage: StorageDraft } = $props();
</script>

<h1 tabindex="-1">{t("storage.title")}</h1>
<p class="lead">{t("storage.lead")}</p>

<div role="radiogroup" aria-label={t("storage.keepFor")}>
  <span class="label">{t("storage.keepFor")}</span>
  {#each KEEP_DAYS as days (days)}
    <label class="row choice">
      <input type="radio" name="keep-days" value={days} bind:group={storage.keepDays} />
      {t(`storage.opt.${days}` as Key)}
    </label>
  {/each}
</div>

<label class="switch" for="keep-rated">
  <input id="keep-rated" type="checkbox" role="switch" bind:checked={storage.keepRated} />
  <span>
    <span class="title">{t("storage.keepRated")}</span><br />
    <span class="hint">{t("storage.keepRatedHint")}</span>
  </span>
</label>
