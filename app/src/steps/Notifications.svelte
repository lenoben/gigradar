<script lang="ts">
  import { BOTFATHER_URL, getBackend } from "../lib/backend";
  import { draft } from "../lib/draft.svelte";
  import { Run } from "../lib/run.svelte";
  import { t } from "../lib/strings";
  import type { Key } from "../lib/strings";
  import { connectWithToken, retryChat } from "../lib/telegram";
  import type { TelegramState } from "../lib/telegram";

  const toastRun = new Run();
  const telegramRun = new Run();
  let tokenInput = $state<HTMLInputElement>();
  let tg = $state<TelegramState>(draft.telegram.connected ? { kind: "connected", bot: draft.telegram.botName } : { kind: "idle" });
  let tokenMissing = $state(false);

  const busy = $derived(telegramRun.status === "running");

  async function testToast(): Promise<void> {
    await toastRun.start("notify-test", { channel: "toast" });
  }

  async function connect(): Promise<void> {
    if (!tokenInput) return;
    tokenMissing = tokenInput.value.trim() === "";
    if (tokenMissing) return;
    tg = await connectWithToken(
      () => tokenInput!.value,
      () => {
        tokenInput!.value = "";
      },
      telegramRun,
    );
  }

  async function foundChat(): Promise<void> {
    tg = await retryChat(telegramRun);
  }

  function useOtherBot(): void {
    draft.telegram.connected = false;
    tg = { kind: "idle" };
  }

  function errorText(state: Extract<TelegramState, { kind: "error" }>): string {
    const key = `notify.error.${state.code}` as Key;
    const known = ["bad_token", "network", "send_failed"].includes(state.code);
    return known ? t(key) : t("notify.error.default", { message: state.message });
  }
</script>

<h1 tabindex="-1">{t("notify.title")}</h1>
<p class="lead">{t("notify.lead")}</p>

<label class="switch" for="toast">
  <input id="toast" type="checkbox" role="switch" bind:checked={draft.toast} />
  <span>
    <span class="title">{t("notify.toast")}</span><br />
    <span class="hint">{t("notify.toastHint")}</span>
  </span>
</label>
{#if draft.toast}
  <div class="row" style="margin-left: 34px">
    <button type="button" onclick={testToast} disabled={toastRun.status === "running"}>{t("notify.toastTest")}</button>
    <span role="status" class="small">
      {#if toastRun.status === "done" && toastRun.ok}
        {t("notify.toastTestOk")}
      {:else if toastRun.status === "done"}
        <span class="error-text">{t("notify.toastTestFailed", { message: toastRun.error?.message ?? "" })}</span>
      {/if}
    </span>
  </div>
{/if}

<label class="switch" for="telegram">
  <input id="telegram" type="checkbox" role="switch" bind:checked={draft.telegram.enabled} />
  <span>
    <span class="title">{t("notify.telegram")}</span><br />
    <span class="hint">{t("notify.telegramHint")}</span>
  </span>
</label>

{#if draft.telegram.enabled}
  <div class="panel" style="margin-left: 34px">
    {#if tg.kind === "connected"}
      <div class="callout ok" role="status">
        <p>{tg.bot ? t("notify.connected", { bot: tg.bot }) : t("notify.connectedNoName")}</p>
      </div>
      <button type="button" class="link" onclick={useOtherBot}>{t("notify.change")}</button>
    {:else}
      <h2 style="margin-top: 0">{t("notify.guideTitle")}</h2>
      <ol>
        <li>{t("notify.guide1")}</li>
        <li>{t("notify.guide2")}</li>
        <li>{t("notify.guide3")}</li>
      </ol>
      <button type="button" class="link" onclick={() => getBackend().openUrl(BOTFATHER_URL)}>{t("notify.openBotFather")}</button>

      {#if tg.kind === "needs_start"}
        <div class="callout" role="status">
          <p>{t("notify.startStep")}</p>
          <button type="button" class="primary" onclick={foundChat} disabled={busy}>
            {busy ? t("notify.connecting") : t("notify.findChat")}
          </button>
        </div>
      {:else}
        <label for="token">{t("notify.tokenLabel")}</label>
        <div class="row">
          <input
            id="token"
            bind:this={tokenInput}
            type="password"
            autocomplete="off"
            spellcheck="false"
            style="flex: 1"
            aria-describedby="token-hint"
            onkeydown={(e) => e.key === "Enter" && connect()}
          />
          <button type="button" class="primary" onclick={connect} disabled={busy}>
            {busy ? t("notify.connecting") : t("notify.connect")}
          </button>
        </div>
        <p class="hint" id="token-hint">{t("notify.tokenHint")}</p>
        {#if tokenMissing}<p class="hint error-text">{t("notify.needToken")}</p>{/if}
        {#if tg.kind === "error" && tg.code !== "empty"}
          <div class="callout bad" role="alert"><p>{errorText(tg)}</p></div>
        {/if}
      {/if}
    {/if}
  </div>
{/if}

{#if !draft.toast && !(draft.telegram.enabled && draft.telegram.connected)}
  <p class="hint">{t("notify.needOne")}</p>
{/if}
