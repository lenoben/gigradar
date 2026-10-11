<script lang="ts">
  import { draft } from "../lib/draft.svelte";
  import type { Draft } from "../lib/draft.svelte";
  import { normalizeProfile, profileLongEnough } from "../lib/profile";
  import { t } from "../lib/strings";

  let { model = draft }: { model?: Draft } = $props();

  let copyMessage = $state("");
  const wrapped = $derived(model.profile.trim() !== "" && normalizeProfile(model.profile).wrapped);

  async function copyPrompt(): Promise<void> {
    try {
      await navigator.clipboard.writeText(t("profile.prompt"));
      copyMessage = t("profile.copied");
    } catch {
      copyMessage = t("profile.copyFailed");
    }
  }
</script>

<h1 tabindex="-1">{t("profile.title")}</h1>
<p class="lead">{t("profile.lead")}</p>

<label for="profile">{t("profile.label")}</label>
<textarea id="profile" bind:value={model.profile} placeholder={t("profile.placeholder")} aria-describedby="profile-hint"></textarea>
<p class="hint" id="profile-hint">
  {#if model.profile.trim() !== "" && !profileLongEnough(model.profile)}
    <span class="error-text">{t("profile.tooShort")}</span>
  {:else if wrapped}
    {t("profile.wrapped")}
  {/if}
</p>

<div class="panel">
  <h2 style="margin-top: 0">{t("profile.helpTitle")}</h2>
  <p>{t("profile.helpBody")}</p>
  <div class="row">
    <button type="button" onclick={copyPrompt}>{t("profile.copy")}</button>
    <span role="status" class="muted small">{copyMessage}</span>
  </div>
  <details style="margin-top: 0.8rem">
    <summary>{t("profile.promptLabel")}</summary>
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <pre class="prompt" tabindex="0">{t("profile.prompt")}</pre>
  </details>
</div>
