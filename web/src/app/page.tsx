"use client";

import * as React from "react";
import { toast } from "sonner";

import type { SearchFilters, JobResult, SearchResponse, ApiError } from "@/lib/types";
import { SearchPanel } from "@/components/search-panel";
import { JobResults } from "@/components/job-results";
import { SubscribeDialog } from "@/components/subscribe-dialog";

const INITIAL_FILTERS: SearchFilters = { query: "AI", limit: 20 };

export default function Home() {
  const [filters, setFilters] = React.useState<SearchFilters>(INITIAL_FILTERS);
  const [jobs, setJobs] = React.useState<JobResult[]>([]);
  const [count, setCount] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  // ponytail: abort the in-flight request so rapid filter changes don't race/flicker.
  const controllerRef = React.useRef<AbortController | null>(null);

  const runSearch = React.useCallback(async (next: SearchFilters) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    try {
      const res = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
        signal: controller.signal,
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => null)) as ApiError | null;
        throw new Error(data?.error ?? `Search failed (${res.status})`);
      }
      const data = (await res.json()) as SearchResponse;
      setJobs(data.jobs);
      setCount(data.count);
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") return; // superseded
      const message = err instanceof Error ? err.message : "Something went wrong";
      toast.error("Couldn't load jobs", { description: message });
      setJobs([]);
      setCount(0);
    } finally {
      if (controllerRef.current === controller) setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    runSearch(INITIAL_FILTERS);
  }, [runSearch]);

  const countLabel = loading
    ? "Searching…"
    : `${count.toLocaleString()} ${count === 1 ? "job" : "jobs"}`;

  return (
    <div className="mx-auto w-full max-w-4xl px-4 pb-20">
      <header className="pt-6 sm:pt-10">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
          Upwork Job Search
        </h1>
        <p className="mt-1 text-sm text-muted-foreground sm:text-base">
          Browse Upwork jobs — no account needed.
        </p>
      </header>

      <div className="mt-4">
        <SearchPanel
          filters={filters}
          onFiltersChange={setFilters}
          onSearch={runSearch}
          loading={loading}
        />
      </div>

      <div className="mt-6 mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground" aria-live="polite">
          {countLabel}
        </p>
        <SubscribeDialog currentFilters={filters} />
      </div>

      <JobResults jobs={jobs} loading={loading} />
    </div>
  );
}
