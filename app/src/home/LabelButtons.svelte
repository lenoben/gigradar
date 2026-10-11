<script lang="ts">
  import { t } from "../lib/strings";

  // 👍 / 👎 on a job. Pressing the active one again takes the answer back. The labels train nothing by themselves:
  // they are kept next to the job so the scoring can be checked against what you think of the jobs.
  let { label, busy, onchange }: { label: number | null; busy: boolean; onchange: (value: "up" | "down" | "clear") => void } = $props();
</script>

<span class="labels" role="group" aria-label={t("jobs.like") + " / " + t("jobs.dislike")}>
  <button
    type="button"
    class="icon"
    aria-pressed={label === 1}
    aria-label={t("jobs.like")}
    title={t("jobs.like")}
    disabled={busy}
    onclick={() => onchange(label === 1 ? "clear" : "up")}>👍</button>
  <button
    type="button"
    class="icon"
    aria-pressed={label === -1}
    aria-label={t("jobs.dislike")}
    title={t("jobs.dislike")}
    disabled={busy}
    onclick={() => onchange(label === -1 ? "clear" : "down")}>👎</button>
</span>
