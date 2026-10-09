import type { Draft } from "./draft.svelte";
import { normalizeProfile } from "./profile";

export interface Answers {
  searches: { name: string; query: string; job_type?: string }[];
  notify: { channels: string[] };
  profile: { markdown: string; skills?: string[]; min_hourly?: number; min_fixed?: number; constraints?: string };
  telegram?: { chat_id: string };
}

export function slug(text: string): string {
  const base = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 30)
    .replace(/-+$/g, "");
  return base || "search";
}

export function parseAmount(text: string): number | null {
  const value = text.trim().replace(",", ".");
  if (value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : Number.NaN;
}

export function amountValid(text: string): boolean {
  return !Number.isNaN(parseAmount(text));
}

/** The answers file for `setup-apply`. It never holds the bot token: the Rust side adds that to the helper's stdin. */
export function buildAnswers(draft: Draft): Answers {
  const used = new Set<string>();
  const searches = draft.searches
    .filter((s) => s.keywords.trim() !== "")
    .map((s) => {
      const base = `${slug(s.keywords)}-${s.type}`;
      let name = base;
      for (let n = 2; used.has(name); n++) name = `${base}-${n}`;
      used.add(name);
      const query = s.keywords.trim();
      // "both" = no job-type filter: hourly and fixed jobs
      return s.type === "both" ? { name, query } : { name, query, job_type: s.type };
    });

  const channels: string[] = [];
  if (draft.toast) channels.push("toast");
  if (draft.telegram.enabled && draft.telegram.connected) channels.push("telegram");

  const profile: Answers["profile"] = { markdown: normalizeProfile(draft.profile).markdown };
  if (draft.skills.length > 0) profile.skills = [...draft.skills];
  const minHourly = parseAmount(draft.minHourly);
  const minFixed = parseAmount(draft.minFixed);
  if (minHourly !== null) profile.min_hourly = minHourly;
  if (minFixed !== null) profile.min_fixed = minFixed;
  if (draft.constraints.trim() !== "") profile.constraints = draft.constraints.trim();

  const answers: Answers = { searches, notify: { channels }, profile };
  if (channels.includes("telegram")) answers.telegram = { chat_id: draft.telegram.chatId };
  return answers;
}
