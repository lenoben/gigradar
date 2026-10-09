import { describe, expect, it } from "vitest";
import { buildAnswers, parseAmount, slug } from "./answers";
import { MAX_SEARCHES, emptyDraft } from "./draft.svelte";
import type { Draft } from "./draft.svelte";
import { normalizeProfile, profileLongEnough } from "./profile";
import { en, t } from "./strings";
import { STEPS, stepValid } from "./wizard";

describe("profile", () => {
  it("keeps text that already has skill-area headings", () => {
    const text = "## Web\nI build things.\n\n## Data\nI clean data.";
    expect(normalizeProfile(text)).toEqual({ markdown: text + "\n", wrapped: false });
  });

  it("adds one heading when there is none", () => {
    expect(normalizeProfile("I build web apps.")).toEqual({ markdown: "## My work\nI build web apps.\n", wrapped: true });
  });

  it("unwraps the code block Claude replies with", () => {
    const reply = "```markdown\n## Web\nI build things.\n```";
    expect(normalizeProfile(reply)).toEqual({ markdown: "## Web\nI build things.\n", wrapped: false });
  });

  it("asks for a few words at least", () => {
    expect(profileLongEnough("short")).toBe(false);
    expect(profileLongEnough("I build web apps with Python")).toBe(true);
  });

  it("the prompt asks for the format the app reads", () => {
    const prompt = t("profile.prompt");
    expect(prompt).toContain('"## Web development"');
    expect(prompt).toMatch(/2 to 6 skill areas/);
  });
});

describe("answers", () => {
  const filled = (): Draft => ({
    ...emptyDraft(),
    profile: "I build web apps with Python and TypeScript.",
    skills: ["Python"],
    minHourly: "40",
    constraints: "  Remote only ",
    searches: [
      { keywords: "Python  automation", type: "hourly" as const },
      { keywords: "python automation", type: "hourly" as const },
      { keywords: "   ", type: "fixed" as const },
    ],
  });

  it("builds the setup-apply answers", () => {
    const answers = buildAnswers(filled());
    expect(answers.searches).toEqual([
      { name: "python-automation-hourly", query: "Python  automation", job_type: "hourly" },
      { name: "python-automation-hourly-2", query: "python automation", job_type: "hourly" },
    ]);
    expect(answers.notify.channels).toEqual(["toast"]);
    expect(answers.profile).toEqual({ markdown: "## My work\nI build web apps with Python and TypeScript.\n", skills: ["Python"], min_hourly: 40, constraints: "Remote only" });
    expect(answers.telegram).toBeUndefined();
  });

  it("\"both\" sends no job-type filter; up to five searches", () => {
    const draft = filled();
    draft.searches = [{ keywords: "react", type: "both" }, { keywords: "react", type: "fixed" }];
    expect(buildAnswers(draft).searches).toEqual([
      { name: "react-both", query: "react" },
      { name: "react-fixed", query: "react", job_type: "fixed" },
    ]);
    expect(MAX_SEARCHES).toBe(5);
  });

  it("holds the chat id but never a bot token", () => {
    const draft = filled();
    draft.telegram = { enabled: true, connected: true, botName: "my_bot", chatId: "424242" };
    const answers = buildAnswers(draft);
    expect(answers.notify.channels).toEqual(["toast", "telegram"]);
    expect(answers.telegram).toEqual({ chat_id: "424242" });
    expect(JSON.stringify(answers)).not.toMatch(/token/i);
  });

  it("telegram counts only once it is connected", () => {
    const draft = filled();
    draft.telegram.enabled = true;
    expect(buildAnswers(draft).notify.channels).toEqual(["toast"]);
  });

  it("parses optional amounts", () => {
    expect(parseAmount("")).toBeNull();
    expect(parseAmount(" 12,5 ")).toBe(12.5);
    expect(parseAmount("abc")).toBeNaN();
    expect(parseAmount("-3")).toBeNaN();
    expect(slug("Next.js / React!")).toBe("next-js-react");
    expect(slug("???")).toBe("search");
  });
});

describe("wizard gating", () => {
  const progress = { upworkOk: false, upworkSkipped: false, modelOk: false };

  it("each step needs what it needs", () => {
    const draft = emptyDraft();
    expect(stepValid("welcome", draft, progress)).toBe(true);
    expect(stepValid("profile", draft, progress)).toBe(false);
    draft.profile = "I build web apps with Python.";
    expect(stepValid("profile", draft, progress)).toBe(true);
    draft.minFixed = "lots";
    expect(stepValid("preferences", draft, progress)).toBe(false);
    draft.minFixed = "";
    expect(stepValid("preferences", draft, progress)).toBe(true);
    expect(stepValid("searches", draft, progress)).toBe(false);
    draft.searches[0].keywords = "python";
    expect(stepValid("searches", draft, progress)).toBe(true);
    expect(stepValid("notifications", draft, progress)).toBe(true);
    draft.toast = false;
    expect(stepValid("notifications", draft, progress)).toBe(false);
    draft.telegram = { enabled: true, connected: true, botName: "b", chatId: "1" };
    expect(stepValid("notifications", draft, progress)).toBe(true);
    expect(stepValid("upwork", draft, progress)).toBe(false);
    expect(stepValid("upwork", draft, { ...progress, upworkSkipped: true })).toBe(true);
    expect(stepValid("model", draft, progress)).toBe(false);
    expect(stepValid("model", draft, { ...progress, modelOk: true })).toBe(true);
  });

  it("has the eight screens in order", () => {
    expect(STEPS.map((s) => s.id)).toEqual(["welcome", "profile", "preferences", "searches", "notifications", "upwork", "model", "finish"]);
  });
});

describe("strings", () => {
  it("fills placeholders", () => {
    expect(t("nav.stepOf", { n: 2, total: 8 })).toBe("Step 2 of 8");
    expect(t("upwork.ok", { count: 30 })).toContain("30");
  });

  it("explains every check the helper's doctor can report", () => {
    for (const name of ["config", "profile", "telegram_token", "telegram_api", "telegram_chat", "webview2_runtime", "webview_profile", "model", "store", "scheduled_task", "recent_errors"]) {
      expect(en).toHaveProperty(`check.${name}`);
      expect(en).toHaveProperty(`fix.${name}`);
    }
  });

  it("has no placeholder without a value in the table", () => {
    for (const [key, text] of Object.entries(en)) {
      expect(text.trim(), key).not.toBe("");
    }
  });
});
