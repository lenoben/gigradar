import { amountValid } from "./answers";
import type { Key } from "./strings";
import { profileLongEnough } from "./profile";
import type { Draft } from "./draft.svelte";

export type StepId = "welcome" | "profile" | "preferences" | "searches" | "notifications" | "upwork" | "model" | "finish";

export const STEPS: { id: StepId; title: Key }[] = [
  { id: "welcome", title: "step.welcome" },
  { id: "profile", title: "step.profile" },
  { id: "preferences", title: "step.preferences" },
  { id: "searches", title: "step.searches" },
  { id: "notifications", title: "step.notifications" },
  { id: "upwork", title: "step.upwork" },
  { id: "model", title: "step.model" },
  { id: "finish", title: "step.finish" },
];

export interface Progress {
  upworkOk: boolean;
  upworkSkipped: boolean;
  modelOk: boolean;
}

/** The settings are written when leaving this step: from here on the Upwork check can run. */
export const APPLY_STEP: StepId = "notifications";

/** May the user leave this step with "Next"? */
export function stepValid(id: StepId, draft: Draft, progress: Progress): boolean {
  switch (id) {
    case "welcome":
      return true;
    case "profile":
      return profileLongEnough(draft.profile);
    case "preferences":
      return amountValid(draft.minHourly) && amountValid(draft.minFixed);
    case "searches":
      return draft.searches.some((s) => s.keywords.trim() !== "");
    case "notifications":
      return draft.toast || (draft.telegram.enabled && draft.telegram.connected);
    case "upwork":
      return progress.upworkOk || progress.upworkSkipped;
    case "model":
      return progress.modelOk;
    case "finish":
      return false;
  }
}
