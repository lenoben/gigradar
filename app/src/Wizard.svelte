<script lang="ts">
  import { tick } from "svelte";
  import { buildAnswers } from "./lib/answers";
  import { draft, progress, saveDraft } from "./lib/draft.svelte";
  import { Run } from "./lib/run.svelte";
  import { t } from "./lib/strings";
  import { APPLY_STEP, STEPS, stepValid } from "./lib/wizard";
  import Welcome from "./steps/Welcome.svelte";
  import Profile from "./steps/Profile.svelte";
  import Preferences from "./steps/Preferences.svelte";
  import Searches from "./steps/Searches.svelte";
  import Notifications from "./steps/Notifications.svelte";
  import UpworkCheck from "./steps/UpworkCheck.svelte";
  import Model from "./steps/Model.svelte";
  import Finish from "./steps/Finish.svelte";

  let { onopenstatus }: { onopenstatus: () => void } = $props();

  const apply = new Run();
  let applyError = $state("");
  let main: HTMLElement;

  const current = $derived(STEPS[progress.step]);
  const last = $derived(progress.step === STEPS.length - 1);
  const canNext = $derived(stepValid(current.id, draft, progress) && apply.status !== "running");

  async function focusHeading(): Promise<void> {
    await tick();
    main?.querySelector<HTMLElement>("h1")?.focus();
  }

  async function writeSettings(): Promise<boolean> {
    applyError = "";
    await apply.start("setup-apply", {
      answers: buildAnswers(draft),
      force: progress.appliedHere || progress.hadConfig,
      noBackup: progress.appliedHere,
    });
    if (!apply.ok) {
      applyError = t("finish.applyFailed", { message: apply.error?.message ?? "" });
      return false;
    }
    progress.appliedHere = true;
    return true;
  }

  async function next(): Promise<void> {
    if (current.id === APPLY_STEP && !(await writeSettings())) return;
    progress.step += 1;
    saveDraft();
    await focusHeading();
  }

  async function back(): Promise<void> {
    applyError = "";
    progress.step -= 1;
    await focusHeading();
  }
</script>

<nav aria-label={t("nav.steps")}>
  <ol class="stepper">
    {#each STEPS as step, index (step.id)}
      <li class:done={index < progress.step} class:current={index === progress.step} aria-current={index === progress.step ? "step" : undefined}>
        <span class="visually-hidden">{t(step.title)}</span>
      </li>
    {/each}
  </ol>
</nav>
<div class="stepmeta">{t("nav.stepOf", { n: progress.step + 1, total: STEPS.length })} · {t(current.title)}</div>

<main class="card" bind:this={main}>
  {#if current.id === "welcome"}
    <Welcome />
  {:else if current.id === "profile"}
    <Profile />
  {:else if current.id === "preferences"}
    <Preferences />
  {:else if current.id === "searches"}
    <Searches />
  {:else if current.id === "notifications"}
    <Notifications />
  {:else if current.id === "upwork"}
    <UpworkCheck />
  {:else if current.id === "model"}
    <Model />
  {:else}
    <Finish {onopenstatus} />
  {/if}

  {#if applyError}
    <div class="callout bad" role="alert"><p>{applyError}</p></div>
  {/if}

  {#if !last}
    <div class="footer">
      <button type="button" onclick={back} disabled={progress.step === 0}>{t("nav.back")}</button>
      <button type="button" class="primary" onclick={next} disabled={!canNext}>
        {current.id === "welcome" ? t("welcome.start") : t("nav.next")}
      </button>
    </div>
  {:else}
    <div class="footer">
      <button type="button" onclick={back}>{t("nav.back")}</button>
      <span></span>
    </div>
  {/if}
</main>
