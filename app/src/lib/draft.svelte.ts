// Everything the user has typed so far. There is deliberately NO field for the Telegram bot token: it goes from the
// input straight to the Rust side (see telegram.ts) and is never stored here, in the saved draft, or in a log.

export type PayType = "hourly" | "fixed" | "both";

export interface SearchDraft {
  keywords: string;
  type: PayType;
}

export interface Draft {
  profile: string;
  skills: string[];
  constraints: string;
  minHourly: string;
  minFixed: string;
  searches: SearchDraft[];
  toast: boolean;
  telegram: { enabled: boolean; connected: boolean; botName: string; chatId: string };
}

export const MAX_SEARCHES = 5;
const STORAGE_KEY = "gigradar.draft.v1";

export function emptyDraft(): Draft {
  return {
    profile: "",
    skills: [],
    constraints: "",
    minHourly: "",
    minFixed: "",
    searches: [{ keywords: "", type: "hourly" }],
    toast: true,
    telegram: { enabled: false, connected: false, botName: "", chatId: "" },
  };
}

export const draft: Draft = $state(emptyDraft());

/** Progress that is not an answer: which long steps have been completed in this session. */
export const progress = $state({
  step: 0,
  /** The config files exist (written by this wizard or already there) and may be rewritten without a backup. */
  appliedHere: false,
  /** An earlier install's files exist: the first write replaces them and keeps a backup. */
  hadConfig: false,
  upworkOk: false,
  upworkSkipped: false,
  modelOk: false,
});

export function loadDraft(): void {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw) as Partial<Draft>;
    const base = emptyDraft();
    Object.assign(draft, base, saved, {
      // a saved "connected" only means something while the Rust side still holds the token; start unconnected
      telegram: { ...base.telegram, enabled: saved.telegram?.enabled ?? false },
    });
  } catch {
    // no storage or a damaged draft: start empty
  }
}

export function saveDraft(): void {
  try {
    const { telegram, ...rest } = $state.snapshot(draft);
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...rest, telegram: { enabled: telegram.enabled } }));
  } catch {
    // storage unavailable: the draft just is not remembered
  }
}

export function clearSavedDraft(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // nothing to clear
  }
}

export function resetDraft(): void {
  Object.assign(draft, emptyDraft());
  progress.step = 0;
  progress.upworkOk = progress.upworkSkipped = progress.modelOk = false;
}
