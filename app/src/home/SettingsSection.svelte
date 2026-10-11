<script lang="ts">
  import { getBackend } from "../lib/backend";
  import { Run } from "../lib/run.svelte";
  import { draftFromSettings, newSearchCount, scheduleFromSettings, sectionPayload, sectionValid, storageFromSettings } from "../lib/settings";
  import type { ScheduleDraft, SectionId, SettingsData, StorageDraft } from "../lib/settings";
  import { t } from "../lib/strings";
  import type { Key } from "../lib/strings";
  import Notifications from "../steps/Notifications.svelte";
  import Preferences from "../steps/Preferences.svelte";
  import Profile from "../steps/Profile.svelte";
  import Schedule from "../steps/Schedule.svelte";
  import Searches from "../steps/Searches.svelte";
  import Storage from "../steps/Storage.svelte";

  // One section of the settings, opened with the current values. Only this section is written, with the same checks
  // as the setup; Cancel (or leaving) changes nothing.
  let { id, settings, onclose }: { id: SectionId; settings: SettingsData; onclose: (saved: boolean, newSearches: number) => void } = $props();

  // a copy to edit: the saved settings are only replaced by a successful Save
  // svelte-ignore state_referenced_locally  (a copy on purpose: the section opens with the values saved now)
  const model = $state(draftFromSettings(settings));
  // svelte-ignore state_referenced_locally
  const schedule: ScheduleDraft = $state(scheduleFromSettings(settings));
  // svelte-ignore state_referenced_locally
  const storage: StorageDraft = $state(storageFromSettings(settings));
  const save = new Run();
  let error = $state("");

  const valid = $derived(sectionValid(id, model, schedule, storage));
  const saving = $derived(save.status === "running");

  async function submit(): Promise<void> {
    error = "";
    // counted before the save: a search that is new to the helper is added without notifications
    const added = id === "searches" ? newSearchCount(model, settings.searches) : 0;
    await save.start("settings-save", { section: id, data: sectionPayload(id, model, schedule, settings, storage) });
    if (save.ok) {
      await getBackend().forgetToken(); // a bot that was just connected is saved now: the window's helper drops the token
      onclose(true, added);
    } else {
      error = t("settings.saveFailed", { message: save.error?.message || save.error?.code || "" });
    }
  }

  async function cancel(): Promise<void> {
    await getBackend().forgetToken();
    onclose(false, 0);
  }
</script>

<nav aria-label={t(`settings.section.${id}` as Key)}>
  <button type="button" class="link" onclick={cancel}>← {t("home.tab.settings")}</button>
</nav>

{#if id === "profile"}
  <Profile {model} />
{:else if id === "preferences"}
  <Preferences {model} />
{:else if id === "searches"}
  <Searches {model} />
{:else if id === "notifications"}
  <Notifications {model} settingsMode={true} />
{:else if id === "schedule"}
  <Schedule {schedule} />
{:else}
  <Storage {storage} />
{/if}

{#if error}
  <div class="callout bad" role="alert"><p>{error}</p></div>
{/if}

<div class="footer">
  <button type="button" onclick={cancel} disabled={saving}>{t("settings.cancel")}</button>
  <button type="button" class="primary" onclick={submit} disabled={!valid || saving}>
    {saving ? t("settings.saving") : t("settings.save")}
  </button>
</div>
