<script lang="ts">
  import { getBackend } from "./lib/backend";
  import { nav, showTab } from "./lib/nav.svelte";
  import type { Tab } from "./lib/nav.svelte";
  import { t } from "./lib/strings";
  import type { Key } from "./lib/strings";
  import Jobs from "./home/Jobs.svelte";
  import Settings from "./home/Settings.svelte";
  import Status from "./Status.svelte";

  // The screen the app opens on once it is set up: Status, Settings and Jobs.
  let { home, onsetup }: { home: string; onsetup: () => void } = $props();

  const TABS: Tab[] = ["status", "settings", "jobs"];
</script>

<div class="brand">{t("app.name")}</div>
<nav aria-label={t("home.nav")}>
  <ul class="tabs">
    {#each TABS as tab (tab)}
      <li>
        <button type="button" class:current={nav.tab === tab} aria-current={nav.tab === tab ? "page" : undefined} onclick={() => showTab(tab)}>
          {t(`home.tab.${tab}` as Key)}
        </button>
      </li>
    {/each}
  </ul>
</nav>

{#if nav.tab === "status"}
  <h1 tabindex="-1">{t("home.title")}</h1>
  <Status {home} {onsetup} />
{:else if nav.tab === "settings"}
  <Settings />
{:else}
  <Jobs />
{/if}

<p class="footnote muted small">
  <button type="button" class="link" onclick={() => void getBackend().quit()}>{t("home.quit")}</button>
  · {t("home.quitHint")}
</p>
