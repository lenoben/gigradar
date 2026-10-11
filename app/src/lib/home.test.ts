import { mount, unmount } from "svelte";
import { afterEach, describe, expect, it } from "vitest";
import App from "../App.svelte";
import { setBackend } from "./backend";
import { resetDraft } from "./draft.svelte";
import { createMockBackend, defaultMockOptions } from "./mock";
import type { MockBackend, MockOptions } from "./mock";
import { showTab } from "./nav.svelte";

const TOKEN = "987654321:AAcanaryCANARYcanaryCANARYcanary_-xy";

afterEach(() => {
  document.body.innerHTML = "";
  resetDraft();
  showTab("status");
  localStorage.clear();
});

function start(options: Partial<MockOptions> = {}): { backend: MockBackend; stop: () => void } {
  const backend = createMockBackend({ ...defaultMockOptions(), tick: 0, configExists: true, ...options });
  setBackend(backend);
  const component = mount(App, { target: document.body });
  return { backend, stop: () => unmount(component) };
}

const h1 = () => document.querySelector("h1")?.textContent ?? "";
const text = () => document.body.textContent ?? "";
const buttons = (label: string) => [...document.querySelectorAll("button")].filter((b) => b.textContent?.trim() === label);
const button = (label: string) => buttons(label)[0] as HTMLButtonElement | undefined;
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
const saves = (backend: MockBackend) => backend.calls.filter((c) => c.name === "settings-save");

async function openSettings(): Promise<void> {
  await expect.poll(() => button("Settings")).toBeDefined();
  button("Settings")!.click();
  await expect.poll(() => document.querySelectorAll(".sections > li").length).toBe(6);
}

async function listShown(): Promise<void> {
  await expect.poll(() => document.querySelectorAll(".sections > li").length).toBe(6);
}

async function editSection(label: string): Promise<void> {
  const edit = () => document.querySelector<HTMLButtonElement>(`button[aria-label="Edit ${label}"]`);
  await expect.poll(() => edit()?.disabled).toBe(false); // not while the list is read again after a save
  edit()!.click();
}

function type(el: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("Home", () => {
  it("has Status, Settings and Jobs, and the Gigradar name", async () => {
    const { stop } = start();
    await expect.poll(() => h1()).toBe("Home");
    expect(document.querySelector(".brand")?.textContent).toBe("Gigradar");
    expect([...document.querySelectorAll(".tabs button")].map((b) => b.textContent?.trim())).toEqual(["Status", "Settings", "Jobs"]);
    button("Jobs")!.click();
    await expect.poll(() => h1()).toBe("Latest jobs");
    await expect.poll(() => document.querySelectorAll(".joblist > li").length).toBe(3);
    button("Settings")!.click();
    await expect.poll(() => h1()).toBe("Settings");
    stop();
  });

  it("the first close shows the tray hint once; OK then hides the window, later closes hide at once", async () => {
    const { backend, stop } = start();
    await expect.poll(() => h1()).toBe("Home");
    await backend.close(false); // what the X or a Close button does
    await expect.poll(() => document.querySelector("[role=dialog]")?.textContent).toContain("Gigradar is still running in the tray (bottom right). Your checks keep running even if you quit.");
    button("OK, got it")!.click();
    await expect.poll(() => document.querySelector("[role=dialog]")).toBeNull();
    expect(backend.calls.filter((c) => c.name === "close").map((c) => c.payload)).toEqual([false, true]);
    await backend.close(false); // the second time: no hint
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(document.querySelector("[role=dialog]")).toBeNull();
    stop();
  });

  it("Quit ends the app, Close only hides the window", async () => {
    const { backend, stop } = start();
    await expect.poll(() => button("Quit Gigradar")).toBeDefined();
    button("Quit Gigradar")!.click();
    await expect.poll(() => backend.calls.some((c) => c.name === "quit")).toBe(true);
    stop();
  });

  it("an Upwork check that is waiting for a click shows up with a Fix button that opens the Upwork window", async () => {
    const { backend, stop } = start({ upworkNeedsCheck: true });
    await expect.poll(() => document.querySelector(".summary")?.textContent).toContain("needs attention");
    const row = [...document.querySelectorAll(".status-list > li")].find((li) => li.textContent?.includes("Upwork access"))!;
    expect(row.querySelector(".pill")?.textContent).toContain("Check");
    (row.querySelector(".fixbtn") as HTMLButtonElement).click();
    await expect.poll(() => backend.calls.some((c) => c.name === "upwork-check")).toBe(true);
    stop();
  });
});

describe("Settings", () => {
  it("lists the six sections with what is saved now", async () => {
    const { stop } = start();
    await openSettings();
    const names = [...document.querySelectorAll(".sections .name")].map((n) => n.textContent);
    expect(names).toEqual(["Profile", "Preferences", "Searches", "Notifications", "Schedule", "Storage"]);
    const summaries = [...document.querySelectorAll(".sections .muted")].map((n) => n.textContent);
    expect(summaries[0]).toBe("2 skill areas");
    expect(summaries[2]).toContain("python automation, react dashboard");
    expect(summaries[3]).toBe("Notifications on this PC · Telegram (@my_gigradar_bot)");
    expect(summaries[4]).toBe("Every 30 minutes");
    expect(summaries[5]).toBe("Keep jobs for 1 month · jobs I rated are kept");
    stop();
  });

  it("every section opens prefilled with the current values and never runs the setup", async () => {
    const { backend, stop } = start();
    await openSettings();

    await editSection("Profile");
    await expect.poll(() => (document.getElementById("profile") as HTMLTextAreaElement | null)?.value).toContain("## Web development");
    button("Cancel")!.click();
    await listShown();

    await editSection("Preferences");
    await expect.poll(() => input("min-hourly")?.value).toBe("40");
    expect(input("min-fixed").value).toBe("500");
    expect(input("constraints").value).toBe("Remote only");
    expect([...document.querySelectorAll(".chips .chip")].map((c) => c.textContent?.replace("×", "").trim())).toEqual(["Python", "TypeScript", "React"]);
    button("Cancel")!.click();
    await listShown();

    await editSection("Searches");
    await expect.poll(() => input("kw-0")?.value).toBe("python automation");
    expect(input("kw-1").value).toBe("react dashboard");
    expect((document.querySelector("input[name='type-0'][value='hourly']") as HTMLInputElement).checked).toBe(true);
    expect((document.querySelector("input[name='type-1'][value='fixed']") as HTMLInputElement).checked).toBe(true);
    button("Cancel")!.click();
    await listShown();

    await editSection("Schedule");
    await expect.poll(() => (document.querySelector("input[name='interval'][value='30']") as HTMLInputElement | null)?.checked).toBe(true);
    expect(input("quiet").checked).toBe(false);
    button("Cancel")!.click();
    await expect.poll(() => h1()).toBe("Settings");

    expect(saves(backend)).toEqual([]);
    expect(backend.calls.some((c) => c.name === "setup-apply")).toBe(false);
    expect(text()).not.toContain("Welcome to Gigradar");
    stop();
  });

  it("saving one section writes only that section", async () => {
    const { backend, stop } = start();
    await openSettings();
    const before = structuredClone(backend.settings);
    await editSection("Preferences");
    await expect.poll(() => input("min-hourly")?.value).toBe("40");
    type(input("min-hourly"), "55");
    button("Save")!.click();
    await expect.poll(() => text()).toContain("Saved. (Preferences)");
    expect(saves(backend)).toEqual([
      { name: "settings-save", payload: { section: "preferences", data: { skills: ["Python", "TypeScript", "React"], constraints: "Remote only", min_hourly: 55, min_fixed: 500, min_score: 0 } } },
    ]);
    expect(backend.settings.preferences.min_hourly).toBe(55);
    expect({ ...backend.settings, preferences: before.preferences }).toEqual(before);
    stop();
  });

  it("Cancel changes nothing and an invalid section cannot be saved", async () => {
    const { backend, stop } = start();
    await openSettings();
    await editSection("Profile");
    await expect.poll(() => document.getElementById("profile")).not.toBeNull();
    type(document.getElementById("profile") as HTMLTextAreaElement, "hi");
    await expect.poll(() => button("Save")!.disabled).toBe(true);
    button("Cancel")!.click();
    await expect.poll(() => h1()).toBe("Settings");
    expect(saves(backend)).toEqual([]);

    await editSection("Preferences");
    await expect.poll(() => input("min-fixed")).not.toBeNull();
    type(input("min-fixed"), "lots");
    await expect.poll(() => button("Save")!.disabled).toBe(true);
    button("Cancel")!.click();
    await listShown();

    await editSection("Schedule");
    await expect.poll(() => input("quiet")).not.toBeNull();
    input("quiet").click();
    await expect.poll(() => input("quiet-from")).not.toBeNull();
    type(input("quiet-to"), input("quiet-from").value);
    await expect.poll(() => button("Save")!.disabled).toBe(true);
    expect(text()).toContain("Choose two different times");
    stop();
  });

  it("the schedule: 30 / 60 / 120 / 240 minutes only, quiet hours optional", async () => {
    const { backend, stop } = start();
    await openSettings();
    await editSection("Schedule");
    await expect.poll(() => document.querySelectorAll("input[name='interval']").length).toBe(4);
    expect([...document.querySelectorAll<HTMLInputElement>("input[name='interval']")].map((i) => i.value)).toEqual(["30", "60", "120", "240"]);
    (document.querySelector("input[name='interval'][value='120']") as HTMLInputElement).click();
    input("quiet").click();
    await expect.poll(() => input("quiet-from")).not.toBeNull();
    type(input("quiet-from"), "23:00");
    type(input("quiet-to"), "06:30");
    button("Save")!.click();
    await expect.poll(() => saves(backend).length).toBe(1);
    expect(saves(backend)[0].payload).toEqual({ section: "schedule", data: { interval_minutes: 120, quiet_start: "23:00", quiet_end: "06:30" } });
    await expect.poll(() => document.querySelectorAll(".sections .muted")[4]?.textContent).toBe("Every 2 hours · quiet 23:00–06:30");
    stop();
  });

  it("storage: keep jobs for a week, two weeks, a month or forever, and whether rated jobs are kept", async () => {
    const { backend, stop } = start();
    await openSettings();
    await editSection("Storage");
    await expect.poll(() => document.querySelectorAll("input[name='keep-days']").length).toBe(4);
    expect([...document.querySelectorAll<HTMLInputElement>("input[name='keep-days']")].map((i) => i.value)).toEqual(["7", "14", "30", "0"]);
    expect((document.querySelector("input[name='keep-days'][value='30']") as HTMLInputElement).checked).toBe(true);
    expect(input("keep-rated").checked).toBe(true);
    (document.querySelector("input[name='keep-days'][value='14']") as HTMLInputElement).click();
    input("keep-rated").click();
    button("Save")!.click();
    await expect.poll(() => saves(backend).length).toBe(1);
    expect(saves(backend)[0].payload).toEqual({ section: "storage", data: { keep_days: 14, keep_rated: false } });
    await expect.poll(() => document.querySelectorAll(".sections .muted")[5]?.textContent).toBe("Keep jobs for 2 weeks · rated jobs too");
    // opened again right after the save, the section shows what was just saved, not the values from before
    await editSection("Storage");
    await expect.poll(() => (document.querySelector("input[name='keep-days'][value='14']") as HTMLInputElement | null)?.checked).toBe(true);
    expect(input("keep-rated").checked).toBe(false);
    stop();
  });

  it("Telegram shows 'Connected as @bot'; a new token is asked for only after 'Change bot'", async () => {
    const { backend, stop } = start({ telegramNeedsStart: false });
    await openSettings();
    await editSection("Notifications");
    await expect.poll(() => text()).toContain("Connected as @my_gigradar_bot.");
    expect(document.querySelector("#token")).toBeNull();
    expect(button("Change bot")).toBeDefined();

    button("Change bot")!.click();
    await expect.poll(() => document.querySelector("#token")).not.toBeNull();
    input("token").value = TOKEN;
    button("Connect")!.click();
    await expect.poll(() => text()).toContain("The new bot is used once you press Save.");
    expect(input("token")).toBeNull();
    expect(document.documentElement.outerHTML).not.toContain(TOKEN);

    button("Save")!.click();
    await expect.poll(() => saves(backend).length).toBe(1);
    expect(saves(backend)[0].payload).toEqual({
      section: "notifications",
      data: { toast: true, telegram: { enabled: true, chat_id: "424242", bot_username: "my_gigradar_bot", useStoredToken: true } },
    });
    expect(JSON.stringify(backend.calls)).not.toContain(TOKEN.split(":")[1]); // the window never passes a token on
    await expect.poll(() => backend.calls.some((c) => c.name === "forget-token")).toBe(true);
    stop();
  });

  it("changing nothing about Telegram sends no bot data; Cancel after 'Change bot' forgets the token", async () => {
    const { backend, stop } = start();
    await openSettings();
    await editSection("Notifications");
    await expect.poll(() => button("Change bot")).toBeDefined();
    button("Save")!.click();
    await expect.poll(() => saves(backend).length).toBe(1);
    expect(saves(backend)[0].payload).toEqual({ section: "notifications", data: { toast: true, telegram: { enabled: true } } });

    await listShown();
    await editSection("Notifications");
    await expect.poll(() => button("Change bot")).toBeDefined();
    button("Change bot")!.click();
    button("Cancel")!.click();
    await expect.poll(() => backend.calls.filter((c) => c.name === "forget-token").length).toBeGreaterThan(0);
    expect(saves(backend).length).toBe(1);
    stop();
  });

  it("a changed search gets a new name (its first check is silent); an unchanged one keeps its name; at most five", async () => {
    const { backend, stop } = start();
    await openSettings();
    await editSection("Searches");
    await expect.poll(() => input("kw-0")?.value).toBe("python automation");
    type(input("kw-1"), "vue dashboard");
    button("Save")!.click();
    await expect.poll(() => saves(backend).length).toBe(1);
    expect(saves(backend)[0].payload).toEqual({
      section: "searches",
      data: { searches: [{ name: "python-automation-hourly", query: "python automation", job_type: "hourly" }, { name: "vue-dashboard-fixed", query: "vue dashboard", job_type: "fixed" }] },
    });

    // a changed search is added without notifications, and the note says so; saving nothing new does not
    await expect.poll(() => text()).toContain("Jobs already posted for this search are added without notifications.");
    await listShown();
    await editSection("Searches");
    await expect.poll(() => input("kw-0")?.value).toBe("python automation");
    button("Save")!.click();
    await expect.poll(() => saves(backend).length).toBe(2);
    await listShown();
    expect(text()).not.toContain("added without notifications");
    await editSection("Searches");
    await expect.poll(() => input("kw-0")?.value).toBe("python automation");
    for (let i = 0; i < 3; i++) button("Add another search")?.click();
    await expect.poll(() => document.querySelectorAll("fieldset").length).toBe(5);
    expect(button("Add another search")).toBeUndefined();
    stop();
  });
});

describe("Jobs and notification links", () => {
  it("a notification for one job opens its card at start, with score, budget, posted time, fit, skills and description", async () => {
    const { stop } = start({ pendingLink: { kind: "job", id: "~01aa" } });
    await expect.poll(() => h1()).toBe("Build a Python tool that syncs invoices");
    expect(document.querySelector(".jobcard .score")?.textContent?.trim()).toBe("82");
    const facts = document.querySelector(".facts")?.textContent ?? "";
    expect(facts).toContain("Hourly $40–60/hr · Expert");
    expect(facts).toContain("3 h ago");
    expect(facts).toContain("Automation and scripting");
    expect([...document.querySelectorAll(".jobcard .chip")].map((c) => c.textContent)).toEqual(["Python", "API"]);
    expect(document.querySelector(".description")?.textContent).toContain("accounting tool");
    expect(button("Open on Upwork")).toBeDefined();
    expect(document.querySelector("button[aria-label='Good match']")).not.toBeNull();
    expect(document.querySelector("button[aria-label='Not for me']")).not.toBeNull();
    stop();
  });

  it("works while the app is open: a click on a toast brings the card up", async () => {
    const { backend, stop } = start();
    await expect.poll(() => h1()).toBe("Home");
    backend.deliverLink({ kind: "job", id: "~02bb" });
    await expect.poll(() => h1()).toBe("Next.js dashboard for a small shop");
    backend.deliverLink({ kind: "status" });
    await expect.poll(() => h1()).toBe("Home");
    stop();
  });

  it("Open on Upwork opens the job's page; thumbs save a label and pressing again takes it back", async () => {
    const { backend, stop } = start({ pendingLink: { kind: "job", id: "~01aa" } });
    await expect.poll(() => button("Open on Upwork")).toBeDefined();
    button("Open on Upwork")!.click();
    await expect.poll(() => backend.calls.find((c) => c.name === "open-url")?.payload).toBe("https://www.upwork.com/jobs/~01aa");

    const up = () => document.querySelector("button[aria-label='Good match']") as HTMLButtonElement;
    const down = () => document.querySelector("button[aria-label='Not for me']") as HTMLButtonElement;
    up().click();
    await expect.poll(() => up().getAttribute("aria-pressed")).toBe("true");
    expect(backend.labels["~01aa"]).toBe(1);
    down().click();
    await expect.poll(() => down().getAttribute("aria-pressed")).toBe("true");
    expect(up().getAttribute("aria-pressed")).toBe("false");
    down().click();
    await expect.poll(() => down().getAttribute("aria-pressed")).toBe("false");
    expect(backend.labels["~01aa"]).toBeNull();
    stop();
  });

  it("a digest notification opens the list of that check only; the list opens cards and labels jobs", async () => {
    const { backend, stop } = start({ pendingLink: { kind: "jobs", run: "2026-10-09T10:00:00+00:00" } });
    await expect.poll(() => document.querySelectorAll(".joblist > li").length).toBe(2);
    expect(text()).toContain("These are the jobs from one check.");
    expect(backend.calls.find((c) => c.name === "jobs")?.payload).toEqual({ limit: 50, run: "2026-10-09T10:00:00+00:00" });

    (document.querySelector(".joblist .title") as HTMLButtonElement).click();
    await expect.poll(() => h1()).toBe("Build a Python tool that syncs invoices");
    button("← Back to the jobs")!.click();
    await expect.poll(() => document.querySelectorAll(".joblist > li").length).toBe(2);

    (document.querySelector(".joblist button[aria-label='Not for me']") as HTMLButtonElement).click();
    await expect.poll(() => backend.labels["~01aa"]).toBe(-1);

    button("Show all jobs")!.click();
    await expect.poll(() => document.querySelectorAll(".joblist > li").length).toBe(3);
    expect(text()).not.toContain("These are the jobs from one check.");
    stop();
  });

  it("jobs below the minimum score are hidden until the toggle is on, then dimmed and tagged", async () => {
    const { backend, stop } = start({ pendingLink: { kind: "jobs", run: null }, minScore: 30 });
    await expect.poll(() => document.querySelectorAll(".joblist > li").length).toBe(2);
    expect(backend.calls.find((c) => c.name === "jobs")?.payload).toEqual({ limit: 50 });
    expect(document.querySelectorAll(".joblist .tag").length).toBe(0);
    input("show-below").click();
    await expect.poll(() => document.querySelectorAll(".joblist > li").length).toBe(3);
    expect(backend.calls.filter((c) => c.name === "jobs").at(-1)?.payload).toEqual({ limit: 50, includeBelow: true });
    const dimmed = [...document.querySelectorAll(".joblist > li.dim")];
    expect(dimmed.length).toBe(1);
    expect(dimmed[0].textContent).toContain("Data cleanup in spreadsheets");
    expect(dimmed[0].querySelector(".tag")?.textContent).toBe("below minimum");
    input("show-below").click();
    await expect.poll(() => document.querySelectorAll(".joblist > li").length).toBe(2);
    stop();
  });

  it("a 👍 given on a card is still there when the card is opened again, in the card and in the list", async () => {
    const { backend, stop } = start({ pendingLink: { kind: "job", id: "~01aa" } });
    await expect.poll(() => document.querySelector(".jobcard button[aria-label='Good match']")).not.toBeNull();
    (document.querySelector(".jobcard button[aria-label='Good match']") as HTMLButtonElement).click();
    await expect.poll(() => backend.labels["~01aa"]).toBe(1);
    button("← Back to the jobs")!.click();
    await expect.poll(() => document.querySelectorAll(".joblist > li").length).toBe(3);
    expect(document.querySelector(".joblist li button[aria-label='Good match']")?.getAttribute("aria-pressed")).toBe("true");
    (document.querySelector(".joblist .title") as HTMLButtonElement).click();
    await expect.poll(() => document.querySelector(".jobcard button[aria-label='Good match']")?.getAttribute("aria-pressed")).toBe("true");
    stop();
  });

  it("without a minimum score there is no toggle", async () => {
    const { stop } = start({ pendingLink: { kind: "jobs", run: null } });
    await expect.poll(() => document.querySelectorAll(".joblist > li").length).toBe(3);
    expect(document.getElementById("show-below")).toBeNull();
    stop();
  });

  it("a job that is not in the history says so", async () => {
    const { stop } = start({ pendingLink: { kind: "job", id: "~gone" } });
    await expect.poll(() => text()).toContain("This job is not in your history");
    stop();
  });

  it("a finished check from the tray reloads the list", async () => {
    const { backend, stop } = start({ pendingLink: { kind: "jobs", run: null } });
    await expect.poll(() => document.querySelectorAll(".joblist > li").length).toBe(3);
    const loads = backend.calls.filter((c) => c.name === "jobs").length;
    backend.emit("check-finished");
    await expect.poll(() => backend.calls.filter((c) => c.name === "jobs").length).toBe(loads + 1);
    stop();
  });
});
