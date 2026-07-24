#!/usr/bin/env node
// Standalone job-alert runner for cron/launchd: `node scripts/notify.mjs`.
// For each stored subscription it runs the Python search, diffs the result URLs
// against the subscription's seenUrls, emails any new jobs via the Resend REST
// API, and records them as seen. Skips gracefully when RESEND_API_KEY is unset.
//
// This duplicates a little logic from src/lib (a .mjs can't import the TS libs),
// which is the intended tradeoff for a zero-build standalone script.

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const execFileAsync = promisify(execFile);

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const WEB_DIR = path.resolve(SCRIPT_DIR, "..");
const ROOT = path.resolve(WEB_DIR, "..");
const PYTHON = path.join(ROOT, ".venv", "bin", "python");
const SEARCH_SCRIPT = path.join(ROOT, "upwork_search.py");
const STORE = path.join(WEB_DIR, "data", "subscriptions.jsonl");

const RESEND_ENDPOINT = "https://api.resend.com/emails";
const EXEC_TIMEOUT_MS = 40_000;
const MAX_BUFFER = 20 * 1024 * 1024;
const SNIPPET_LEN = 200;

// --- minimal .env loader (real process.env wins, then .env.local, then .env) ---
function loadEnv(file) {
  if (!existsSync(file)) return;
  for (const line of readFileSync(file, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let val = trimmed.slice(eq + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    if (!(key in process.env)) process.env[key] = val;
  }
}

function buildArgs(filters) {
  const args = [];
  if (filters.query) args.push("-q", filters.query);
  if (filters.jobType) args.push("--job-type", filters.jobType);
  if (filters.tier) args.push("--tier", filters.tier);
  if (filters.workload) args.push("--workload", filters.workload);
  if (filters.duration) args.push("--duration", filters.duration);
  if (filters.clientHires) args.push("--client-hires", filters.clientHires);
  if (filters.contractToHire === true) args.push("--contract-to-hire");
  if (filters.hourlyRate) args.push("--hourly-rate", filters.hourlyRate);
  if (filters.fixedBudget) args.push("--fixed-budget", filters.fixedBudget);
  if (filters.location) args.push("--location", filters.location);
  const limit = Math.min(100, Math.max(1, filters.limit ?? 30));
  args.push("--limit", String(limit));
  if (process.env.UPWORK_PROXY) args.push("--proxy", process.env.UPWORK_PROXY);
  args.push("--format", "json");
  return args;
}

async function runSearch(filters) {
  const { stdout } = await execFileAsync(PYTHON, [SEARCH_SCRIPT, ...buildArgs(filters)], {
    timeout: EXEC_TIMEOUT_MS,
    maxBuffer: MAX_BUFFER,
  });
  const parsed = JSON.parse(stdout);
  if (!Array.isArray(parsed)) throw new Error("upwork_search.py returned non-array JSON");
  return parsed;
}

function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function jobCardHtml(job) {
  const rate =
    job.hourly_min || job.hourly_max
      ? `${job.hourly_min ? `$${esc(job.hourly_min)}` : ""}${job.hourly_max ? `–$${esc(job.hourly_max)}` : ""}/hr`
      : job.fixed_budget
        ? `$${esc(job.fixed_budget)} fixed`
        : "";
  const meta = [job.tier ? esc(job.tier) : "", job.job_type ? esc(job.job_type) : "", rate].filter(Boolean).join(" · ");
  const collapsed = (job.description || "").trim().replace(/\s+/g, " ");
  const snippet = collapsed.slice(0, SNIPPET_LEN);
  return `
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 12px;background:#fff;border:1px solid #e4e4e7;border-radius:8px;">
      <tr><td style="padding:16px;">
        <a href="${esc(job.url)}" style="font-size:15px;font-weight:600;color:#14a800;text-decoration:none;">${esc(job.title)}</a>
        ${meta ? `<div style="margin:6px 0 0;font-size:12px;color:#71717a;">${meta}</div>` : ""}
        ${snippet ? `<p style="margin:8px 0 0;font-size:13px;line-height:1.5;color:#3f3f46;">${esc(snippet)}${collapsed.length > SNIPPET_LEN ? "…" : ""}</p>` : ""}
      </td></tr>
    </table>`;
}

function alertHtml(jobs) {
  const heading = `${jobs.length} new ${jobs.length === 1 ? "job" : "jobs"} on Upwork`;
  return `<!doctype html>
<html lang="en"><body style="margin:0;padding:0;background:#f4f4f5;">
  <div style="max-width:600px;margin:0 auto;padding:24px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#18181b;">
    <h1 style="margin:0 0 16px;font-size:20px;">${heading}</h1>
    ${jobs.map(jobCardHtml).join("")}
    <p style="margin:24px 0 0;font-size:12px;color:#a1a1aa;">Automated Upwork job alert.</p>
  </div>
</body></html>`;
}

async function sendAlert(apiKey, from, to, jobs) {
  const resp = await fetch(RESEND_ENDPOINT, {
    method: "POST",
    headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from,
      to,
      subject: `${jobs.length} new ${jobs.length === 1 ? "job" : "jobs"} on Upwork`,
      html: alertHtml(jobs),
    }),
  });
  if (!resp.ok) {
    throw new Error(`Resend API ${resp.status}: ${(await resp.text()).slice(0, 300)}`);
  }
  return resp.json();
}

function loadSubscriptions() {
  if (!existsSync(STORE)) return [];
  return readFileSync(STORE, "utf8")
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => JSON.parse(line));
}

function writeSubscriptions(subscriptions) {
  writeFileSync(STORE, subscriptions.map((s) => JSON.stringify(s)).join("\n") + "\n", "utf8");
}

async function main() {
  loadEnv(path.join(WEB_DIR, ".env.local"));
  loadEnv(path.join(WEB_DIR, ".env"));

  const apiKey = process.env.RESEND_API_KEY;
  if (!apiKey) {
    console.warn("[notify] RESEND_API_KEY unset — nothing to send, exiting");
    return;
  }
  const from = process.env.RESEND_FROM ?? "Upwork Jobs <onboarding@resend.dev>";

  const subscriptions = loadSubscriptions();
  if (subscriptions.length === 0) {
    console.log("[notify] no subscriptions");
    return;
  }

  let changed = false;
  for (const sub of subscriptions) {
    try {
      const jobs = await runSearch(sub.filters);
      // First run for this subscription: silently seed the current matches as
      // "seen" so alerts only fire for jobs posted after they subscribed
      // (rather than emailing the entire current result set on the first run).
      if (!sub.seenUrls || sub.seenUrls.length === 0) {
        sub.seenUrls = jobs.map((j) => j.url).filter(Boolean);
        changed = true;
        console.log(`[notify] ${sub.email}: seeded ${sub.seenUrls.length} current jobs (first run, no email)`);
        continue;
      }
      const seen = new Set(sub.seenUrls);
      const newJobs = jobs.filter((j) => j.url && !seen.has(j.url));
      if (newJobs.length === 0) {
        console.log(`[notify] ${sub.email}: 0 new of ${jobs.length} matched`);
        continue;
      }
      await sendAlert(apiKey, from, sub.email, newJobs);
      // Only record as seen after a successful send, so a failed email retries.
      sub.seenUrls = Array.from(new Set([...(sub.seenUrls ?? []), ...jobs.map((j) => j.url).filter(Boolean)]));
      changed = true;
      console.log(`[notify] ${sub.email}: ${newJobs.length} new of ${jobs.length} matched -> emailed`);
    } catch (err) {
      console.error(`[notify] ${sub.email}: FAILED — ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  if (changed) writeSubscriptions(subscriptions);
}

main().catch((err) => {
  console.error(`[notify] fatal: ${err instanceof Error ? err.stack : String(err)}`);
  process.exit(1);
});
