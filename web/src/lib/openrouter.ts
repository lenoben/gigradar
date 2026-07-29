// Client-side OpenRouter call (BYOK). The user's key lives in their browser and
// is sent only to OpenRouter (CORS-open, Authorization allowed) — never to this
// server. Streams a job-proposal draft token by token.
import type { JobResult } from "@/lib/types";

const ENDPOINT = "https://openrouter.ai/api/v1/chat/completions";
export const DEFAULT_MODEL = "openai/gpt-4o-mini";

const SYSTEM_PROMPT =
  "You are an expert Upwork freelancer writing a short, winning proposal (cover letter) to apply for a job. " +
  "Write in the first person as the freelancer. Tailor it to the specific job, weave in ONLY relevant experience " +
  "from the freelancer's background, keep it ~120–180 words, warm and confident but never salesy or clichéd, and " +
  "end with one concrete next step or question. Never invent skills, tools, or credentials the freelancer didn't list. " +
  "Output only the proposal text — no preamble, no subject line, no signature block.";

function budgetLine(job: JobResult): string {
  if (job.hourly_min || job.hourly_max) return `Budget: $${job.hourly_min ?? "?"}–$${job.hourly_max ?? "?"}/hr`;
  if (job.fixed_budget) return `Budget: $${job.fixed_budget} fixed`;
  return "";
}

export type DraftProposalInput = {
  apiKey: string;
  model: string;
  job: JobResult;
  profile: string;
  signal: AbortSignal;
  onToken: (chunk: string) => void;
};

/** Stream a tailored proposal for `job`, calling `onToken` with each text delta.
 *  @throws Error with the OpenRouter status/message on failure. */
export async function streamProposal(input: DraftProposalInput): Promise<void> {
  const { apiKey, model, job, profile, signal, onToken } = input;

  const userPrompt = [
    `JOB: ${job.title}`,
    budgetLine(job),
    job.tier ? `Experience level: ${job.tier}` : "",
    job.skills ? `Skills: ${job.skills}` : "",
    "",
    "JOB DESCRIPTION:",
    job.description || "(no description)",
    "",
    "MY BACKGROUND / SKILLS:",
    profile.trim() || "(none provided — keep it general but still specific to the job)",
    "",
    "Write my proposal.",
  ]
    .filter((line) => line !== "")
    .join("\n");

  const res = await fetch(ENDPOINT, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
      "HTTP-Referer": typeof location !== "undefined" ? location.origin : "https://github.com/mishafyi/upwork-jobs",
      "X-Title": "Upwork Job Search",
    },
    body: JSON.stringify({
      model,
      stream: true,
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: userPrompt },
      ],
    }),
    signal,
  });

  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => "");
    throw new Error(`OpenRouter ${res.status}: ${detail.slice(0, 200) || res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue; // skip SSE comments / keep-alives
      const data = trimmed.slice(5).trim();
      if (data === "[DONE]") return;
      try {
        const parsed = JSON.parse(data) as { choices?: { delta?: { content?: string } }[] };
        const delta = parsed.choices?.[0]?.delta?.content;
        if (delta) onToken(delta);
      } catch {
        // Non-JSON SSE line (comment/keep-alive) — expected, skip it.
      }
    }
  }
}
