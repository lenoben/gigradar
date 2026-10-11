<script lang="ts">
  import { onMount } from "svelte";
  import { Run } from "../lib/run.svelte";
  import { INTERVALS, SECTION_IDS } from "../lib/settings";
  import type { SectionId, SettingsData } from "../lib/settings";
  import { t } from "../lib/strings";
  import type { Key } from "../lib/strings";
  import SettingsSection from "./SettingsSection.svelte";

  // Settings: the five parts of the setup, each opened on its own with what is saved now.
  const load = new Run();
  let open = $state<SectionId | null>(null);
  let saved = $state<SectionId | null>(null);
  let newSearches = $state(0);

  // the last settings read: kept on screen while they are read again after a save
  let settings = $state<SettingsData | null>(null);

  async function reload(): Promise<void> {
    await load.start("settings-get", {});
    if (load.ok) settings = load.result?.settings as SettingsData;
    else settings = null;
  }

  function closed(wasSaved: boolean, added: number): void {
    saved = wasSaved && open ? open : null;
    newSearches = wasSaved ? added : 0;
    open = null;
    void reload();
  }

  function hours(minutes: number): string {
    return t(`schedule.opt.${minutes}` as Key);
  }

  function summary(id: SectionId, s: SettingsData): string {
    switch (id) {
      case "profile":
        return t("settings.sum.profile", { n: (s.profile.markdown.match(/^## \S/gm) ?? []).length });
      case "preferences": {
        const p = s.preferences;
        const money = (v: number | null) => (v === null ? t("settings.sum.none") : `$${v}`);
        return t("settings.sum.preferences", { skills: p.skills.length, hourly: money(p.min_hourly), fixed: money(p.min_fixed) });
      }
      case "searches":
        return t("settings.sum.searches", { n: s.searches.length, names: s.searches.map((x) => x.query).join(", ") });
      case "notifications": {
        const parts: string[] = [];
        if (s.notifications.toast) parts.push(t("settings.sum.toast"));
        const tg = s.notifications.telegram;
        if (tg.enabled) parts.push(tg.bot ? t("settings.sum.telegram", { bot: tg.bot }) : t("settings.sum.telegramNoName"));
        return parts.length > 0 ? parts.join(" · ") : t("settings.sum.nothing");
      }
      case "schedule": {
        const sc = s.schedule;
        const every = (INTERVALS as readonly number[]).includes(sc.interval_minutes) ? hours(sc.interval_minutes) : `${sc.interval_minutes} min`;
        const quiet = sc.quiet_start && sc.quiet_end ? ` · ${t("settings.sum.quiet", { start: sc.quiet_start, end: sc.quiet_end })}` : "";
        return t("settings.sum.every", { interval: every }) + quiet;
      }
      case "storage": {
        const days = s.storage.keep_days ?? 0;
        const keep = t(`storage.opt.${days}` as Key);
        return s.storage.keep_rated ? t("settings.sum.storage", { keep }) : t("settings.sum.storageAll", { keep });
      }
    }
  }

  onMount(reload);
</script>

{#if open !== null && settings !== null}
  <SettingsSection id={open} {settings} onclose={closed} />
{:else}
  <h1 tabindex="-1">{t("settings.title")}</h1>
  <p class="lead">{t("settings.lead")}</p>

  {#if settings === null && load.status !== "done"}
    <p role="status" class="muted">{t("settings.loading")}</p>
  {:else if settings === null}
    {#if load.error?.code === "unsupported_config"}
      <div class="callout warn" role="alert"><p>{t("settings.unsupported", { message: load.error.message })}</p></div>
    {:else}
      <div class="callout bad" role="alert"><p>{t("settings.loadFailed", { message: load.error?.message || load.error?.code || "" })}</p></div>
    {/if}
  {:else}
    {#if saved}
      <div class="callout ok" role="status">
        <p>{t("settings.saved")} ({t(`settings.section.${saved}` as Key)})</p>
        {#if saved === "searches" && newSearches > 0}<p class="small">{t("settings.newSearchNote")}</p>{/if}
      </div>
    {/if}
    <ul class="sections">
      {#each SECTION_IDS as id (id)}
        <li>
          <div class="body">
            <div class="name">{t(`settings.section.${id}` as Key)}</div>
            <div class="muted small">{summary(id, settings)}</div>
          </div>
          <!-- not while the settings are read again after a save: a section would open on the values from before -->
          <button type="button" disabled={load.status === "running"} onclick={() => (open = id)} aria-label={`${t("settings.edit")} ${t(`settings.section.${id}` as Key)}`}>{t("settings.edit")}</button>
        </li>
      {/each}
    </ul>
  {/if}
{/if}
