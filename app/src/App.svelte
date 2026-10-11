<script lang="ts">
  import { onMount } from "svelte";
  import { getBackend } from "./lib/backend";
  import { loadDraft, progress, resetDraft } from "./lib/draft.svelte";
  import { applyLink } from "./lib/nav.svelte";
  import { Run } from "./lib/run.svelte";
  import { t } from "./lib/strings";
  import Home from "./Home.svelte";
  import Wizard from "./Wizard.svelte";

  let view = $state<"loading" | "wizard" | "home">("loading");
  let home = $state("");
  let trayHint = $state(false); // shown once, the first time the window is closed to the tray

  async function boot(): Promise<void> {
    loadDraft();
    const info = await getBackend().info();
    home = info.home;
    const doctor = new Run();
    await doctor.start("doctor", { offline: true });
    const checks = (doctor.result?.checks as { name: string; status: string; message: string }[] | undefined) ?? [];
    const config = checks.find((c) => c.name === "config");
    // The wizard is for a first start only: once a config file exists the app opens on Home, which shows what is
    // wrong (and offers "Set up again", which replaces the files and keeps a backup).
    const missing = config === undefined || config.message.includes("not found");
    progress.hadConfig = !missing;
    view = missing ? "wizard" : "home";
    if (!missing) await followLink();
  }

  // A notification click starts the app, or reaches the running one, with a link: show what it names.
  async function followLink(): Promise<void> {
    if (view === "home") applyLink(await getBackend().takePendingLink());
  }

  function setupAgain(): void {
    resetDraft();
    progress.hadConfig = true;
    progress.appliedHere = false;
    view = "wizard";
  }

  onMount(() => {
    void boot();
    let stop = () => {};
    let stopHint = () => {};
    void getBackend().subscribe("link-pending", () => void followLink()).then((off) => (stop = off));
    void getBackend().subscribe("close-hint", () => (trayHint = true)).then((off) => (stopHint = off));
    return () => {
      stop();
      stopHint();
    };
  });
</script>

{#if trayHint}
  <div class="modal-backdrop">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="tray-hint">
      <p id="tray-hint">{t("tray.hint")}</p>
      <button type="button" class="primary" onclick={() => { trayHint = false; void getBackend().close(true); }}>{t("tray.hintOk")}</button>
    </div>
  </div>
{/if}

<div class="shell">
  {#if view === "loading"}
    <p role="status" class="muted">{t("app.loading")}</p>
  {:else if view === "wizard"}
    <Wizard onopenstatus={() => (view = "home")} />
  {:else}
    <main class="card">
      <Home {home} onsetup={setupAgain} />
    </main>
  {/if}
</div>
