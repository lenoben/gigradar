// Shared contract for the Upwork job-search UI.
// Both the API routes and the frontend import from here. Do not fork these shapes.

/** One job, exactly as emitted by ../upwork_search.py (the Python dataclass Job). */
export type JobResult = {
  title: string;
  url: string;
  job_type: string | null; // "HOURLY" | "FIXED" | "WEEKLY_RETAINER" | null
  published: string | null; // ISO timestamp
  hourly_min: string | null;
  hourly_max: string | null;
  fixed_budget: string | null;
  tier: string | null; // "EntryLevel" | "IntermediateLevel" | "ExpertLevel" | null
  skills: string; // comma-separated
  description: string; // full text
};

export type JobType = "hourly" | "fixed" | "weekly_retainer";
export type Tier = "entry" | "intermediate" | "expert";
export type Workload = "part_time" | "full_time" | "as_needed";
export type Duration = "ongoing" | "week" | "month" | "semester";
export type ClientHires = "0" | "1-9" | "10-";

/** Search filters. Sent by the frontend to POST /api/search and stored on subscriptions. */
export type SearchFilters = {
  query?: string;
  jobType?: JobType;
  tier?: Tier;
  workload?: Workload;
  duration?: Duration;
  clientHires?: ClientHires;
  contractToHire?: boolean;
  hourlyRate?: string; // "MIN-MAX" e.g. "25-50" (forces hourly)
  fixedBudget?: string; // "MIN-MAX" or "5000-" (forces fixed)
  location?: string; // client country, exact name e.g. "United States"
  limit?: number; // default 30, max ~5000
};

/** POST /api/search  -> SearchResponse */
export type SearchRequest = SearchFilters;
export type SearchResponse = { jobs: JobResult[]; count: number };
export type ApiError = { error: string };

/** POST /api/subscribe  -> { ok: true } | ApiError */
export type SubscribeRequest = { email: string; filters: SearchFilters };

// ---- Option lists for the UI (mirror the Python enums) ----
export const JOB_TYPE_OPTIONS: { value: JobType; label: string }[] = [
  { value: "hourly", label: "Hourly" },
  { value: "fixed", label: "Fixed price" },
  { value: "weekly_retainer", label: "Weekly retainer" },
];
export const TIER_OPTIONS: { value: Tier; label: string }[] = [
  { value: "entry", label: "Entry level" },
  { value: "intermediate", label: "Intermediate" },
  { value: "expert", label: "Expert" },
];
export const WORKLOAD_OPTIONS: { value: Workload; label: string }[] = [
  { value: "full_time", label: "Full time (30+ hrs/wk)" },
  { value: "part_time", label: "Part time" },
  { value: "as_needed", label: "As needed" },
];
export const DURATION_OPTIONS: { value: Duration; label: string }[] = [
  { value: "week", label: "< 1 month" },
  { value: "month", label: "1–3 months" },
  { value: "semester", label: "3–6 months" },
  { value: "ongoing", label: "Ongoing" },
];
export const CLIENT_HIRES_OPTIONS: { value: ClientHires; label: string }[] = [
  { value: "0", label: "No hires yet" },
  { value: "1-9", label: "1–9 hires" },
  { value: "10-", label: "10+ hires" },
];
// Common client countries (the `location` filter needs the exact country name).
export const COUNTRY_OPTIONS: string[] = [
  "United States",
  "United Kingdom",
  "Canada",
  "Australia",
  "Germany",
  "United Arab Emirates",
  "India",
  "Netherlands",
  "France",
  "Singapore",
  "Switzerland",
  "Spain",
];
