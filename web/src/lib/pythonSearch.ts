// Runs the existing Python CLI (../upwork_search.py) as a child process and
// parses its JSON output. Uses execFile with an args ARRAY (never a shell
// string) so filter values can't be interpreted as shell syntax.
//
// child_process only works on the Node.js runtime — every route that imports
// this must declare `export const runtime = "nodejs"`.

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import type { JobResult, SearchFilters } from "@/lib/types";

const execFileAsync = promisify(execFile);

// The Next dev server's cwd is <repoRoot>/web, so the repo root is one level up.
const ROOT = path.resolve(process.cwd(), "..");
const PYTHON = path.join(ROOT, ".venv", "bin", "python");
const SCRIPT = path.join(ROOT, "upwork_search.py");

const LIMIT_DEFAULT = 30;
const LIMIT_MIN = 1;
const LIMIT_MAX = 100;
const EXEC_TIMEOUT_MS = 40_000;
const MAX_BUFFER = 20 * 1024 * 1024;

/** Map SearchFilters onto the upwork_search.py argv array. */
function buildArgs(filters: SearchFilters, offset: number): string[] {
  const args: string[] = [];
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
  const limit = Math.min(LIMIT_MAX, Math.max(LIMIT_MIN, filters.limit ?? LIMIT_DEFAULT));
  args.push("--limit", String(limit));
  args.push("--offset", String(Math.max(0, Math.floor(offset))));
  args.push("--meta"); // {total, offset, count, jobs} so the UI can paginate
  // On a datacenter IP Upwork/Cloudflare 403s the token fetch; route through a
  // residential/rotating proxy when UPWORK_PROXY is set (needed on the VPS).
  const proxy = process.env.UPWORK_PROXY;
  if (proxy) args.push("--proxy", proxy);
  args.push("--format", "json");
  return args;
}

/** Read the last few stderr lines for an error message (stderr also carries
 * benign token-fetch warnings, so we keep the tail rather than the whole thing). */
function stderrTail(err: unknown): string {
  const stderr = typeof err === "object" && err !== null && "stderr" in err ? String((err as { stderr: unknown }).stderr) : "";
  return stderr.trim().split("\n").slice(-6).join(" | ");
}

/**
 * Run the Upwork search CLI from `offset` and return the page of jobs plus the
 * full result `total` Upwork reports (for pagination).
 * @throws Error (with the stderr tail) on nonzero exit, timeout, or unparseable output.
 */
export async function runSearch(filters: SearchFilters, offset: number): Promise<{ jobs: JobResult[]; total: number }> {
  const args = [SCRIPT, ...buildArgs(filters, offset)];

  let stdout: string;
  try {
    const result = await execFileAsync(PYTHON, args, { timeout: EXEC_TIMEOUT_MS, maxBuffer: MAX_BUFFER });
    stdout = result.stdout;
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    const tail = stderrTail(err);
    throw new Error(`upwork_search.py failed (${message})${tail ? ` — stderr: ${tail}` : ""}`);
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(stdout);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(`could not parse upwork_search.py JSON output (${message}) — output head: ${stdout.slice(0, 300)}`);
  }
  // With --meta the tool emits { total, offset, count, jobs }.
  if (typeof parsed !== "object" || parsed === null || !Array.isArray((parsed as { jobs?: unknown }).jobs)) {
    throw new Error(`upwork_search.py returned unexpected JSON: ${stdout.slice(0, 300)}`);
  }
  const obj = parsed as { total?: unknown; jobs: JobResult[] };
  const total = typeof obj.total === "number" ? obj.total : obj.jobs.length;
  // Trusted boundary: the Python Job dataclass guarantees the JobResult shape.
  return { jobs: obj.jobs, total };
}
