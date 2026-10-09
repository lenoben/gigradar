import { flushSync, mount, unmount } from "svelte";
import { afterEach, describe, expect, it } from "vitest";
import App from "../App.svelte";
import { setBackend } from "./backend";
import { progress, resetDraft } from "./draft.svelte";
import { createMockBackend, defaultMockOptions } from "./mock";

afterEach(() => {
  document.body.innerHTML = "";
  resetDraft();
  localStorage.clear();
});

describe("the app", () => {
  it("shows the welcome screen on a first start and the status screen on an existing install", async () => {
    setBackend(createMockBackend({ ...defaultMockOptions(), tick: 0 }));
    let app = mount(App, { target: document.body });
    await expect.poll(() => document.querySelector("h1")?.textContent).toContain("Welcome to gigradar");
    unmount(app);
    document.body.innerHTML = "";

    setBackend(createMockBackend({ ...defaultMockOptions(), tick: 0, configExists: true }));
    app = mount(App, { target: document.body });
    await expect.poll(() => document.querySelector("h1")?.textContent).toContain("Home");
    await expect.poll(() => document.querySelectorAll(".status-list > li").length).toBeGreaterThan(5);
    unmount(app);
  });

  it("reopening a set-up app opens Home, also when the config is damaged, never the wizard", async () => {
    setBackend(createMockBackend({ ...defaultMockOptions(), tick: 0, configBroken: true }));
    const app = mount(App, { target: document.body });
    await expect.poll(() => document.querySelector("h1")?.textContent).toContain("Home");
    await expect.poll(() => document.querySelector(".summary")?.textContent).toContain("needs attention");
    const fixes = [...document.querySelectorAll(".status-list button")].map((b) => b.textContent?.trim());
    expect(fixes).toContain("Set up again");
    expect(document.body.textContent).not.toContain("Welcome to gigradar");
    unmount(app);
  });

  it("the status page: equal pills with an icon, a summary with the next check, fix buttons", async () => {
    setBackend(createMockBackend({ ...defaultMockOptions(), tick: 0, configExists: true }));
    const app = mount(App, { target: document.body });
    await expect.poll(() => document.querySelector(".summary")?.textContent).toMatch(/^All set — next check at \d\d?[:.]\d\d/);
    const pills = [...document.querySelectorAll(".pill")];
    expect(pills.length).toBeGreaterThan(5);
    expect(pills.every((p) => p.querySelector("[aria-hidden='true']") !== null)).toBe(true);
    expect(new Set(pills.map((p) => p.textContent?.trim().slice(2))).size).toBe(1); // all "OK": same text, same widths via css
    unmount(app);

    document.body.innerHTML = "";
    const backend = createMockBackend({ ...defaultMockOptions(), tick: 0, configExists: true, doctorLevel: "WARN" });
    setBackend(backend);
    const second = mount(App, { target: document.body });
    await expect.poll(() => document.querySelector(".summary")?.textContent).toContain("2 things need attention");
    const labels = [...document.querySelectorAll(".fixbtn")].map((b) => b.textContent?.trim());
    expect(labels).toEqual(["Check Upwork", "Schedule checks"]);
    expect(document.querySelector(".pill.WARN")?.textContent).toContain("Check");
    (document.querySelector(".fixbtn") as HTMLButtonElement).click();
    await expect.poll(() => backend.calls.some((c) => c.name === "upwork-check")).toBe(true);
    await expect.poll(() => backend.calls.filter((c) => c.name === "doctor").length).toBe(3); // start-up, the page itself, and again after the fix
    unmount(second);
  });

  it("walks through the whole wizard with the mock helper", async () => {
    const backend = createMockBackend({ ...defaultMockOptions(), tick: 0 });
    setBackend(backend);
    const app = mount(App, { target: document.body });
    const heading = () => document.querySelector("h1")?.textContent ?? "";
    const button = (text: string) => [...document.querySelectorAll("button")].find((b) => b.textContent?.trim().startsWith(text) && !b.disabled);
    const fill = (selector: string, value: string) => {
      const el = document.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector)!;
      el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
    };
    await expect.poll(() => heading()).toContain("Welcome");
    button("Get started")!.click();
    await expect.poll(() => heading()).toContain("what you do");
    expect(button("Next")).toBeUndefined(); // nothing typed yet
    fill("#profile", "I build web apps with Python and TypeScript.");
    await expect.poll(() => button("Next")).toBeDefined();
    button("Next")!.click();
    await expect.poll(() => heading()).toContain("What matters");
    button("Next")!.click();
    await expect.poll(() => heading()).toContain("look for");
    expect(button("Next")).toBeUndefined();
    fill("#kw-0", "python automation");
    await expect.poll(() => button("Next")).toBeDefined();
    button("Next")!.click();
    await expect.poll(() => heading()).toContain("tell you");
    button("Next")!.click(); // writes the settings first
    await expect.poll(() => heading()).toContain("Upwork connection");
    const apply = backend.calls.find((c) => c.name === "setup-apply")!;
    expect(apply.payload).toMatchObject({ force: false, noBackup: false });
    expect(JSON.stringify(apply.payload)).not.toMatch(/token/i);
    button("Open the check")!.click();
    await expect.poll(() => document.body.textContent).toContain("It works");
    button("Next")!.click();
    await expect.poll(() => heading()).toContain("scoring model");
    button("Download")!.click();
    await expect.poll(() => document.body.textContent).toContain("The model is ready");
    button("Next")!.click();
    await expect.poll(() => heading()).toContain("Almost done");
    button("Finish setup")!.click();
    await expect.poll(() => heading()).toContain("all set");
    expect(backend.calls.map((c) => c.name)).toEqual(["doctor", "setup-apply", "upwork-check", "model-download", "task-register", "run-once", "jobs"]);
    flushSync();
    expect(progress.step).toBe(7);
    expect(document.body.textContent).toContain("Checks run in the background every 30 minutes, even when the app is closed");
    (document.querySelector("button.primary") as HTMLButtonElement).click();
    await expect.poll(() => backend.calls.some((c) => c.name === "close")).toBe(true);
    unmount(app);
  });

  it("offers a limited number of tries when the Upwork check keeps failing, then a way on", async () => {
    setBackend(createMockBackend({ ...defaultMockOptions(), tick: 0, upworkFailures: 9, upworkFailureCode: "window_failed" }));
    progress.step = 5;
    const app = mount(App, { target: document.body });
    const button = (text: string) => [...document.querySelectorAll("button")].find((b) => b.textContent?.trim().startsWith(text) && !b.disabled);
    await expect.poll(() => document.querySelector("h1")?.textContent).toContain("Upwork connection");
    button("Open the check")!.click();
    await expect.poll(() => button("Try again (2 of 3)")).toBeDefined();
    button("Try again (2 of 3)")!.click();
    await expect.poll(() => button("Try again (3 of 3)")).toBeDefined();
    button("Try again (3 of 3)")!.click();
    await expect.poll(() => button("Continue anyway")).toBeDefined();
    expect(button("Open the check")).toBeUndefined();
    expect(document.body.textContent).toContain("slow to start");
    button("Continue anyway")!.click();
    flushSync();
    await expect.poll(() => button("Next")).toBeDefined();
    unmount(app);
  });
});
