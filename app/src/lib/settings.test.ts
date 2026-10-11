import { describe, expect, it } from "vitest";
import { emptyDraft } from "./draft.svelte";
import { applyLink, nav, showTab } from "./nav.svelte";
import { INTERVALS, KEEP_DAYS, connectedNewBot, draftFromSettings, newSearchCount, scheduleFromSettings, searchPayload, sectionPayload, sectionValid, storageFromSettings } from "./settings";
import type { ScheduleDraft, SettingsData, StorageDraft } from "./settings";
import { APP_DISPLAY_NAME, t } from "./strings";
import { ago } from "./time";

const SETTINGS: SettingsData = {
  profile: { markdown: "## Web\nI build web apps with TypeScript.\n" },
  preferences: { skills: ["Python"], constraints: "", min_hourly: 40, min_fixed: null, min_score: 20 },
  searches: [
    { name: "py-hourly", query: "python", job_type: "hourly" },
    { name: "react-both", query: "react", job_type: null },
  ],
  notifications: { toast: true, telegram: { enabled: true, connected: true, bot: "my_bot" } },
  schedule: { interval_minutes: 60, quiet_start: "22:00", quiet_end: "07:00" },
  storage: { keep_days: 30, keep_rated: true },

};

const SCHEDULE: ScheduleDraft = { interval: 60, quiet: false, start: "22:00", end: "07:00" };
const STORAGE: StorageDraft = { keepDays: 30, keepRated: true };

describe("the settings draft", () => {
  it("opens prefilled from the saved settings", () => {
    const draft = draftFromSettings(SETTINGS);
    expect(draft.profile).toContain("## Web");
    expect(draft.skills).toEqual(["Python"]);
    expect([draft.minHourly, draft.minFixed]).toEqual(["40", ""]);
    expect(draft.searches).toEqual([
      { keywords: "python", type: "hourly", name: "py-hourly" },
      { keywords: "react", type: "both", name: "react-both" },
    ]);
    expect(draft.telegram).toEqual({ enabled: true, connected: true, botName: "my_bot", chatId: "" });
    expect(scheduleFromSettings(SETTINGS)).toEqual({ interval: 60, quiet: true, start: "22:00", end: "07:00" });
    expect(scheduleFromSettings({ ...SETTINGS, schedule: { interval_minutes: 30, quiet_start: null, quiet_end: null } }).quiet).toBe(false);
  });

  it("has no field a bot token could be kept in", () => {
    expect(JSON.stringify(draftFromSettings(SETTINGS))).not.toMatch(/token/i);
  });
});

describe("searches keep their name only while they are the same search", () => {
  it("unchanged keeps the name, changed or new gets a new unique one", () => {
    const draft = draftFromSettings(SETTINGS);
    draft.searches[0].keywords = "  PYTHON ";
    draft.searches[1].keywords = "vue";
    draft.searches.push({ keywords: "vue", type: "both" });
    expect(searchPayload(draft, SETTINGS.searches)).toEqual([
      { name: "py-hourly", query: "PYTHON", job_type: "hourly" },
      { name: "vue-both", query: "vue" },
      { name: "vue-both-2", query: "vue" },
    ]);
  });

  it("changing the pay type is a new search", () => {
    const draft = draftFromSettings(SETTINGS);
    draft.searches[0].type = "fixed";
    expect(searchPayload(draft, SETTINGS.searches)[0]).toEqual({ name: "python-fixed", query: "python", job_type: "fixed" });
  });

  it("empty rows are left out and two equal searches do not steal one name", () => {
    const draft = draftFromSettings(SETTINGS);
    draft.searches = [{ keywords: "python", type: "hourly" }, { keywords: "", type: "both" }, { keywords: "python", type: "hourly" }];
    expect(searchPayload(draft, SETTINGS.searches).map((s) => s.name)).toEqual(["py-hourly", "python-hourly"]);
  });
});

describe("section payloads", () => {
  it("each section sends only its own values", () => {
    const draft = draftFromSettings(SETTINGS);
    expect(sectionPayload("profile", draft, SCHEDULE, SETTINGS, STORAGE)).toEqual({ markdown: "## Web\nI build web apps with TypeScript.\n" });
    expect(sectionPayload("preferences", draft, SCHEDULE, SETTINGS, STORAGE)).toEqual({ skills: ["Python"], constraints: "", min_hourly: 40, min_fixed: null, min_score: 20 });
    expect(sectionPayload("schedule", draft, SCHEDULE, SETTINGS, STORAGE)).toEqual({ interval_minutes: 60, quiet_start: null, quiet_end: null });
    expect(sectionPayload("schedule", draft, { ...SCHEDULE, quiet: true }, SETTINGS, STORAGE)).toEqual({ interval_minutes: 60, quiet_start: "22:00", quiet_end: "07:00" });
  });

  it("a profile without a heading gets one, as in the setup", () => {
    const draft = emptyDraft();
    draft.profile = "I build web apps with TypeScript and Python.";
    expect(sectionPayload("profile", draft, SCHEDULE, SETTINGS, STORAGE)).toEqual({ markdown: "## My work\nI build web apps with TypeScript and Python.\n" });
  });

  it("notifications: the saved bot is kept without sending anything; a new one is sent as 'use the stored token'", () => {
    const draft = draftFromSettings(SETTINGS);
    expect(connectedNewBot(draft)).toBe(false);
    expect(sectionPayload("notifications", draft, SCHEDULE, SETTINGS, STORAGE)).toEqual({ toast: true, telegram: { enabled: true } });
    draft.telegram.chatId = "777";
    draft.telegram.botName = "new_bot";
    expect(connectedNewBot(draft)).toBe(true);
    const payload = sectionPayload("notifications", draft, SCHEDULE, SETTINGS, STORAGE);
    expect(payload).toEqual({ toast: true, telegram: { enabled: true, chat_id: "777", bot_username: "new_bot", useStoredToken: true } });
    expect(JSON.stringify(payload)).not.toMatch(/bot_token/);
  });
});

describe("the storage section", () => {
  it("opens with what is saved; a file without retention reads as forever", () => {
    expect(storageFromSettings(SETTINGS)).toEqual({ keepDays: 30, keepRated: true });
    expect(storageFromSettings({ ...SETTINGS, storage: { keep_days: null, keep_rated: true } })).toEqual({ keepDays: 0, keepRated: true });
  });

  it("saves keep_days and keep_rated, and only the offered choices are valid", () => {
    const draft = draftFromSettings(SETTINGS);
    expect(sectionPayload("storage", draft, SCHEDULE, SETTINGS, { keepDays: 7, keepRated: false })).toEqual({ keep_days: 7, keep_rated: false });
    for (const keepDays of KEEP_DAYS) expect(sectionValid("storage", draft, SCHEDULE, { keepDays, keepRated: true })).toBe(true);
    expect(sectionValid("storage", draft, SCHEDULE, { keepDays: 5, keepRated: true })).toBe(false);
  });

  it("counts the searches that are new to the helper, so the note about silent seeding only shows when it applies", () => {
    const draft = draftFromSettings(SETTINGS);
    expect(newSearchCount(draft, SETTINGS.searches)).toBe(0);
    draft.searches[0].keywords = "python scripts";
    expect(newSearchCount(draft, SETTINGS.searches)).toBe(1);
    draft.searches.push({ keywords: "rust", type: "both", name: "" });
    expect(newSearchCount(draft, SETTINGS.searches)).toBe(2);
  });
});

describe("validation is the setup's", () => {
  it("profile, preferences, searches, notifications, schedule", () => {
    const draft = draftFromSettings(SETTINGS);
    expect(sectionValid("profile", draft, SCHEDULE, STORAGE)).toBe(true);
    draft.profile = "hi";
    expect(sectionValid("profile", draft, SCHEDULE, STORAGE)).toBe(false);

    draft.minHourly = "abc";
    expect(sectionValid("preferences", draft, SCHEDULE, STORAGE)).toBe(false);
    draft.minHourly = "";
    expect(sectionValid("preferences", draft, SCHEDULE, STORAGE)).toBe(true);

    draft.searches = [{ keywords: " ", type: "both" }];
    expect(sectionValid("searches", draft, SCHEDULE, STORAGE)).toBe(false);
    draft.searches = Array.from({ length: 6 }, (_, i) => ({ keywords: `k${i}`, type: "both" as const }));
    expect(sectionValid("searches", draft, SCHEDULE, STORAGE)).toBe(false);

    draft.toast = false;
    draft.telegram.connected = false;
    expect(sectionValid("notifications", draft, SCHEDULE, STORAGE)).toBe(false);
    draft.toast = true;
    expect(sectionValid("notifications", draft, SCHEDULE, STORAGE)).toBe(true);

    expect(INTERVALS).toEqual([30, 60, 120, 240]);
    expect(sectionValid("schedule", draft, { ...SCHEDULE, interval: 45 }, STORAGE)).toBe(false);
    expect(sectionValid("schedule", draft, { ...SCHEDULE, interval: 15 }, STORAGE)).toBe(false);
    expect(sectionValid("schedule", draft, { ...SCHEDULE, quiet: true, start: "08:00", end: "08:00" }, STORAGE)).toBe(false);
    expect(sectionValid("schedule", draft, { ...SCHEDULE, quiet: true, start: "", end: "08:00" }, STORAGE)).toBe(false);
    expect(sectionValid("schedule", draft, { ...SCHEDULE, interval: 240, quiet: true }, STORAGE)).toBe(true);
  });
});

describe("how long ago", () => {
  const now = new Date("2026-10-09T12:00:00Z");
  it("is short and plain", () => {
    expect(ago("2026-10-09T11:59:30Z", now)).toBe("just now");
    expect(ago("2026-10-09T11:40:00Z", now)).toBe("20 min ago");
    expect(ago("2026-10-09T09:00:00Z", now)).toBe("3 h ago");
    expect(ago("2026-10-08T09:00:00Z", now)).toBe("yesterday");
    expect(ago("2026-10-05T09:00:00Z", now)).toBe("4 days ago");
    expect(ago("2026-01-05T09:00:00Z", now)).toMatch(/2026/);
    expect(ago(null, now)).toBe("");
    expect(ago("garbage", now)).toBe("");
    expect(ago("2026-10-09T13:00:00Z", now)).toBe("just now"); // a clock a little ahead is not "in the future"
  });
});

describe("links", () => {
  it("a job opens its card, a run opens that run's list, status opens Status", () => {
    applyLink({ kind: "job", id: "~01" });
    expect(nav).toEqual({ tab: "jobs", jobId: "~01", run: null });
    applyLink({ kind: "jobs", run: "R" });
    expect(nav).toEqual({ tab: "jobs", jobId: null, run: "R" });
    applyLink({ kind: "status" });
    expect(nav).toEqual({ tab: "status", jobId: null, run: null });
    applyLink(null);
    showTab("status");
  });
});

describe("the name", () => {
  it("is Gigradar, with a capital G, in every text that names the app", () => {
    expect(APP_DISPLAY_NAME).toBe("Gigradar");
    expect(t("app.name")).toBe("Gigradar");
    expect(t("welcome.title")).toBe("Welcome to Gigradar");
    expect(t("home.quit")).toBe("Quit Gigradar");
  });
});
