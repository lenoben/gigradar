import { flushSync, mount, unmount } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setBackend } from "./backend";
import { draft, resetDraft, saveDraft } from "./draft.svelte";
import { createMockBackend, defaultMockOptions } from "./mock";
import Notifications from "../steps/Notifications.svelte";

const TOKEN = "987654321:AAcanaryCANARYcanaryCANARYcanary_-xy";

// import.meta.glob reads the sources at build time: the checks below cannot be skipped by moving a file.
const sources = import.meta.glob("../**/*.{ts,svelte}", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

afterEach(() => {
  document.body.innerHTML = "";
  resetDraft();
  localStorage.clear();
  vi.restoreAllMocks();
});

async function submitToken(options: Partial<ReturnType<typeof defaultMockOptions>>) {
  const backend = createMockBackend({ ...defaultMockOptions(), tick: 0, ...options });
  setBackend(backend);
  draft.telegram.enabled = true;
  const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
  const component = mount(Notifications, { target: document.body });
  flushSync();
  const input = document.querySelector<HTMLInputElement>("#token")!;
  input.value = TOKEN;
  const connect = [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Connect")!;
  connect.click();
  return { backend, input, spies, component };
}

describe("the Telegram bot token", () => {
  it("is emptied from the field the moment it is submitted", async () => {
    const { backend, input, component } = await submitToken({});
    expect(input.value).toBe(""); // synchronously, before the helper has answered
    await vi.waitFor(() => expect(backend.tokensSeen).toEqual([TOKEN]));
    unmount(component);
  });

  it("is not in the draft, the saved draft, the page, or any console output", async () => {
    const { backend, spies, component } = await submitToken({ telegramNeedsStart: false });
    await vi.waitFor(() => expect(draft.telegram.connected).toBe(true));
    flushSync();
    saveDraft();
    expect(JSON.stringify(draft)).not.toContain(TOKEN);
    expect(localStorage.getItem("gigradar.draft.v1") ?? "").not.toContain(TOKEN);
    expect(document.body.innerHTML).not.toContain(TOKEN);
    expect([...document.querySelectorAll("input")].map((i) => i.value).join("")).not.toContain(TOKEN);
    for (const spy of spies) expect(JSON.stringify(spy.mock.calls)).not.toContain(TOKEN);
    expect(backend.tokensSeen).toEqual([TOKEN]);
    unmount(component);
  });

  it("the second try (after /start) sends no token at all", async () => {
    const { backend, component } = await submitToken({});
    await vi.waitFor(() => expect(document.body.textContent).toContain("/start"));
    const retry = [...document.querySelectorAll("button")].find((b) => b.textContent?.includes("I sent /start"))!;
    retry.click();
    await vi.waitFor(() => expect(draft.telegram.connected).toBe(true));
    expect(backend.tokensSeen).toEqual([TOKEN]); // still only the first call carried it
    unmount(component);
  });
});

describe("source rules", () => {
  const own = Object.entries(sources).filter(([path]) => !path.endsWith(".test.ts"));

  it("no code writes to the console", () => {
    for (const [path, text] of own) expect(text, path).not.toMatch(/console\.(log|info|warn|error|debug|trace)/);
  });

  it("only a short list of files mentions the token at all", () => {
    const allowed = ["telegram.ts", "tauri.ts", "run.svelte.ts", "mock.ts", "backend.ts", "Notifications.svelte", "strings.ts", "answers.ts", "draft.svelte.ts", "main.ts", "wizard.ts", "Status.svelte"]; // Status only names the doctor check `telegram_token`
    for (const [path, text] of own) {
      if (/token/i.test(text)) expect(allowed.some((name) => path.endsWith(name)), path).toBe(true);
    }
  });

  it("the draft type has no token field", () => {
    const text = own.find(([path]) => path.endsWith("draft.svelte.ts"))![1];
    expect(text.replace(/\/\/.*$/gm, "")).not.toMatch(/token/i);
  });
});
