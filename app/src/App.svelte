<script lang="ts">
  import { onMount } from "svelte";
  import { getBackend } from "./lib/backend";
  import { loadDraft, progress, resetDraft } from "./lib/draft.svelte";
  import { Run } from "./lib/run.svelte";
  import { t } from "./lib/strings";
  import Home from "./Home.svelte";
  import Wizard from "./Wizard.svelte";

  let view = $state<"loading" | "wizard" | "home">("loading");
  let home = $state("");

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
  }

  function setupAgain(): void {
    resetDraft();
    progress.hadConfig = true;
    progress.appliedHere = false;
    view = "wizard";
  }

  onMount(boot);
</script>

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
