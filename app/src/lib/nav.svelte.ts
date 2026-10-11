// Where the Home screen is: which tab, and whether a job card or one run's job list is open. A notification click
// (a gigradar:// link, parsed by the Rust side) sets it through `applyLink`.

import type { AppLink } from "./backend";

export type Tab = "status" | "settings" | "jobs";

export const nav = $state<{ tab: Tab; jobId: string | null; run: string | null }>({ tab: "status", jobId: null, run: null });

export function showTab(tab: Tab): void {
  nav.tab = tab;
  nav.jobId = null;
  nav.run = null;
}

export function openJob(id: string): void {
  nav.tab = "jobs";
  nav.jobId = id;
}

export function closeJob(): void {
  nav.jobId = null;
}

export function applyLink(link: AppLink | null): void {
  if (link === null) return;
  switch (link.kind) {
    case "job":
      nav.tab = "jobs";
      nav.jobId = link.id;
      nav.run = null;
      break;
    case "jobs":
      nav.tab = "jobs";
      nav.jobId = null;
      nav.run = link.run;
      break;
    case "status":
      showTab("status");
      break;
  }
}
