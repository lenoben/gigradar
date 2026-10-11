// Settings screen: turns what `settings-get` returns into the same draft the setup wizard edits, and turns an edited
// draft back into the payload of `settings-save` for ONE section. Nothing here talks to the helper.

import { amountValid, minScoreValid, parseAmount, slug } from "./answers";
import type { Draft, PayType } from "./draft.svelte";
import { MAX_SEARCHES, emptyDraft } from "./draft.svelte";
import { normalizeProfile, profileLongEnough } from "./profile";

export type SectionId = "profile" | "preferences" | "searches" | "notifications" | "schedule" | "storage";
export const SECTION_IDS: SectionId[] = ["profile", "preferences", "searches", "notifications", "schedule", "storage"];
export const INTERVALS = [30, 60, 120, 240] as const;
/** How long jobs are kept, in days: a week, two weeks, a month, forever (0). */
export const KEEP_DAYS = [7, 14, 30, 0] as const;

export interface SettingsData {
  profile: { markdown: string };
  preferences: { skills: string[]; constraints: string; min_hourly: number | null; min_fixed: number | null; min_score: number };
  searches: { name: string; query: string; job_type: string | null }[];
  notifications: { toast: boolean; telegram: { enabled: boolean; connected: boolean; bot: string | null } };
  schedule: { interval_minutes: number; quiet_start: string | null; quiet_end: string | null };
  /** keep_days is null when the settings file has no retention at all (nothing is ever deleted: shown as "forever"). */
  storage: { keep_days: number | null; keep_rated: boolean };
  /** Jobs scored below this are not announced and are hidden in the list unless asked for; 0 = no minimum. */
}

export interface StorageDraft {
  keepDays: number;
  keepRated: boolean;
}

export interface ScheduleDraft {
  interval: number;
  quiet: boolean;
  start: string;
  end: string;
}

const DEFAULT_QUIET = { start: "22:00", end: "07:00" };

export function draftFromSettings(settings: SettingsData): Draft {
  const draft = emptyDraft();
  draft.profile = settings.profile.markdown;
  draft.skills = [...settings.preferences.skills];
  draft.constraints = settings.preferences.constraints ?? "";
  draft.minHourly = settings.preferences.min_hourly == null ? "" : String(settings.preferences.min_hourly);
  draft.minFixed = settings.preferences.min_fixed == null ? "" : String(settings.preferences.min_fixed);
  draft.minScore = String(settings.preferences.min_score);
  draft.searches = settings.searches.map((s) => ({
    keywords: s.query,
    type: (s.job_type === "hourly" || s.job_type === "fixed" ? s.job_type : "both") as PayType,
    name: s.name,
  }));
  if (draft.searches.length === 0) draft.searches = emptyDraft().searches;
  draft.toast = settings.notifications.toast;
  const tg = settings.notifications.telegram;
  draft.telegram = { enabled: tg.enabled, connected: tg.connected, botName: tg.bot ?? "", chatId: "" };
  return draft;
}

export function scheduleFromSettings(settings: SettingsData): ScheduleDraft {
  const { interval_minutes, quiet_start, quiet_end } = settings.schedule;
  const quiet = quiet_start != null && quiet_end != null;
  return {
    interval: interval_minutes,
    quiet,
    start: quiet ? quiet_start : DEFAULT_QUIET.start,
    end: quiet ? quiet_end : DEFAULT_QUIET.end,
  };
}

export function storageFromSettings(settings: SettingsData): StorageDraft {
  return { keepDays: settings.storage.keep_days ?? 0, keepRated: settings.storage.keep_rated };
}

/** A bot was connected while editing: its chat id is known only then (the saved settings never reveal it). */
export function connectedNewBot(draft: Draft): boolean {
  return draft.telegram.enabled && draft.telegram.connected && draft.telegram.chatId !== "";
}

function sameSearch(keywords: string, type: PayType, original: SettingsData["searches"][number]): boolean {
  const originalType = original.job_type === "hourly" || original.job_type === "fixed" ? original.job_type : "both";
  return keywords.trim().toLowerCase() === original.query.trim().toLowerCase() && type === originalType;
}

/**
 * A search keeps its name while its keywords and pay type stay the same: the name is how the helper knows it has
 * already looked at that search (and stays quiet about jobs it has seen). A changed search gets a new name, so its
 * first check is silent instead of announcing everything it finds.
 */
export function searchPayload(draft: Draft, original: SettingsData["searches"]): { name: string; query: string; job_type?: string }[] {
  const used = new Set<string>();
  const filled = draft.searches.filter((s) => s.keywords.trim() !== "");
  const kept: (string | undefined)[] = [];
  for (const s of filled) {
    const same = original.find((o) => sameSearch(s.keywords, s.type, o) && !used.has(o.name));
    if (same) used.add(same.name);
    kept.push(same?.name);
  }
  return filled.map((s, index) => {
    const query = s.keywords.trim();
    let name = kept[index];
    if (!name) {
      const base = `${slug(query)}-${s.type}`;
      name = base;
      for (let n = 2; used.has(name); n++) name = `${base}-${n}`;
      used.add(name);
    }
    return s.type === "both" ? { name, query } : { name, query, job_type: s.type };
  });
}

function amountOrNull(text: string): number | null {
  const value = parseAmount(text);
  return value === null || Number.isNaN(value) ? null : value;
}

/** Searches in the draft that the helper has not looked at before (new, or changed keywords or pay type). */
export function newSearchCount(draft: Draft, original: SettingsData["searches"]): number {
  const known = new Set(original.map((o) => o.name));
  return searchPayload(draft, original).filter((s) => !known.has(s.name)).length;
}

export function sectionPayload(section: SectionId, draft: Draft, schedule: ScheduleDraft, original: SettingsData, storage: StorageDraft): unknown {
  switch (section) {
    case "profile":
      return { markdown: normalizeProfile(draft.profile).markdown };
    case "preferences":
      return {
        skills: [...draft.skills],
        constraints: draft.constraints.trim(),
        min_hourly: amountOrNull(draft.minHourly),
        min_fixed: amountOrNull(draft.minFixed),
        min_score: Number(draft.minScore),
      };
    case "searches":
      return { searches: searchPayload(draft, original.searches) };
    case "notifications": {
      const telegram: Record<string, unknown> = { enabled: draft.telegram.enabled };
      // the token itself is never in the window: the Rust side adds it to the helper's input
      if (connectedNewBot(draft)) {
        Object.assign(telegram, { chat_id: draft.telegram.chatId, bot_username: draft.telegram.botName, useStoredToken: true });
      }
      return { toast: draft.toast, telegram };
    }
    case "schedule":
      return {
        interval_minutes: schedule.interval,
        quiet_start: schedule.quiet ? schedule.start : null,
        quiet_end: schedule.quiet ? schedule.end : null,
      };
    case "storage":
      return { keep_days: storage.keepDays, keep_rated: storage.keepRated };
  }
}

/** May this section be saved? Same rules as the wizard's steps. */
export function sectionValid(section: SectionId, draft: Draft, schedule: ScheduleDraft, storage: StorageDraft): boolean {
  switch (section) {
    case "profile":
      return profileLongEnough(draft.profile);
    case "preferences":
      return amountValid(draft.minHourly) && amountValid(draft.minFixed) && minScoreValid(draft.minScore);
    case "searches":
      return draft.searches.some((s) => s.keywords.trim() !== "") && draft.searches.length <= MAX_SEARCHES;
    case "notifications":
      return draft.toast || (draft.telegram.enabled && draft.telegram.connected);
    case "schedule":
      return (
        (INTERVALS as readonly number[]).includes(schedule.interval) &&
        (!schedule.quiet || (/^\d\d:\d\d$/.test(schedule.start) && /^\d\d:\d\d$/.test(schedule.end) && schedule.start !== schedule.end))
      );
    case "storage":
      return (KEEP_DAYS as readonly number[]).includes(storage.keepDays);
  }
}
