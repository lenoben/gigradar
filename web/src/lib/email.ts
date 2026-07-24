// Resend-backed transactional email. If RESEND_API_KEY is unset every send is a
// no-op that returns false (never throws) so signups still succeed without email
// configured. Templates are self-contained inline-styled HTML (email clients
// strip <style>/external CSS).

import { Resend } from "resend";
import type { JobResult, SearchFilters } from "@/lib/types";

const FROM = process.env.RESEND_FROM ?? "Upwork Jobs <onboarding@resend.dev>";
const BRAND = "#14a800"; // Upwork green
const SNIPPET_LEN = 200;

function getClient(): Resend | null {
  const key = process.env.RESEND_API_KEY;
  if (!key) {
    console.warn("[email] RESEND_API_KEY is unset — skipping email send");
    return null;
  }
  return new Resend(key);
}

/** Escape text for safe interpolation into HTML (job/query fields are untrusted). */
function esc(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function summarizeFilters(filters: SearchFilters): string {
  const parts: string[] = [];
  if (filters.query) parts.push(`"${filters.query}"`);
  if (filters.jobType) parts.push(filters.jobType.replace(/_/g, " "));
  if (filters.tier) parts.push(filters.tier);
  if (filters.workload) parts.push(filters.workload.replace(/_/g, " "));
  if (filters.duration) parts.push(filters.duration);
  if (filters.clientHires) parts.push(`${filters.clientHires} client hires`);
  if (filters.contractToHire) parts.push("contract-to-hire");
  if (filters.hourlyRate) parts.push(`$${filters.hourlyRate}/hr`);
  if (filters.fixedBudget) parts.push(`$${filters.fixedBudget} fixed`);
  if (filters.location) parts.push(filters.location);
  return parts.length > 0 ? parts.join(", ") : "all recent jobs";
}

function shell(heading: string, intro: string, body: string): string {
  return `<!doctype html>
<html lang="en">
<body style="margin:0;padding:0;background:#f4f4f5;-webkit-text-size-adjust:100%;">
  <div style="max-width:600px;margin:0 auto;padding:24px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#18181b;">
    <h1 style="margin:0 0 8px;font-size:20px;line-height:1.3;">${heading}</h1>
    <p style="margin:0 0 20px;font-size:14px;line-height:1.5;color:#52525b;">${intro}</p>
    ${body}
    <p style="margin:24px 0 0;font-size:12px;line-height:1.5;color:#a1a1aa;">You are receiving this because you subscribed to Upwork job alerts. This is an automated message.</p>
  </div>
</body>
</html>`;
}

function jobCardHtml(job: JobResult): string {
  const rate =
    job.hourly_min || job.hourly_max
      ? `${job.hourly_min ? `$${esc(job.hourly_min)}` : ""}${job.hourly_max ? `–$${esc(job.hourly_max)}` : ""}/hr`
      : job.fixed_budget
        ? `$${esc(job.fixed_budget)} fixed`
        : "";
  const meta = [job.tier ? esc(job.tier) : "", job.job_type ? esc(job.job_type) : "", rate].filter(Boolean).join(" · ");
  const collapsed = job.description.trim().replace(/\s+/g, " ");
  const snippet = collapsed.slice(0, SNIPPET_LEN);
  return `
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 12px;background:#ffffff;border:1px solid #e4e4e7;border-radius:8px;">
      <tr><td style="padding:16px;">
        <a href="${esc(job.url)}" style="font-size:15px;font-weight:600;color:${BRAND};text-decoration:none;">${esc(job.title)}</a>
        ${meta ? `<div style="margin:6px 0 0;font-size:12px;color:#71717a;">${meta}</div>` : ""}
        ${snippet ? `<p style="margin:8px 0 0;font-size:13px;line-height:1.5;color:#3f3f46;">${esc(snippet)}${collapsed.length > SNIPPET_LEN ? "…" : ""}</p>` : ""}
        ${job.skills ? `<div style="margin:8px 0 0;font-size:11px;color:#a1a1aa;">${esc(job.skills)}</div>` : ""}
      </td></tr>
    </table>`;
}

async function send(to: string, subject: string, html: string): Promise<boolean> {
  const client = getClient();
  if (!client) return false;
  try {
    const { data, error } = await client.emails.send({ from: FROM, to, subject, html });
    if (error) {
      console.error("[email] Resend returned an error", { to, subject, name: error.name, message: error.message });
      return false;
    }
    console.info("[email] sent", { to, subject, id: data?.id });
    return true;
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("[email] send threw", { to, subject, message });
    return false;
  }
}

export async function sendConfirmation(email: string, filters: SearchFilters): Promise<boolean> {
  const summary = esc(summarizeFilters(filters));
  const body = `
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid #e4e4e7;border-radius:8px;">
      <tr><td style="padding:16px;font-size:14px;line-height:1.6;color:#3f3f46;">
        We will email you whenever a new job matches. You can stop anytime by removing your subscription.
      </td></tr>
    </table>`;
  return send(
    email,
    "You are subscribed to Upwork job alerts",
    shell("You are subscribed", `Your saved search: <strong>${summary}</strong>.`, body),
  );
}

export async function sendJobAlert(email: string, jobs: JobResult[], filters: SearchFilters): Promise<boolean> {
  if (jobs.length === 0) return false;
  const summary = esc(summarizeFilters(filters));
  const heading = `${jobs.length} new ${jobs.length === 1 ? "job" : "jobs"} on Upwork`;
  return send(
    email,
    heading,
    shell(heading, `Matching your saved search: <strong>${summary}</strong>`, jobs.map(jobCardHtml).join("")),
  );
}
