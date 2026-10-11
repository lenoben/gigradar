import { t } from "./strings";

/** "3 h ago", "yesterday", "5 days ago": how long ago an ISO time stamp is. Older than two weeks: the date. */
export function ago(iso: string | null | undefined, now: Date): string {
  if (!iso) return "";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const minutes = Math.max(0, Math.round((now.getTime() - then.getTime()) / 60_000));
  if (minutes < 2) return t("time.justNow");
  if (minutes < 60) return t("time.minutes", { n: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("time.hours", { n: hours });
  const days = Math.floor(hours / 24);
  if (days === 1) return t("time.yesterday");
  if (days <= 14) return t("time.days", { n: days });
  return then.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
}
