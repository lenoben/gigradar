"use client";

import * as React from "react";
import { toast } from "sonner";

import type { SearchFilters, JobResult, SearchResponse, ApiError } from "@/lib/types";
import { SearchPanel } from "@/components/search-panel";
import { JobResults } from "@/components/job-results";
import { SubscribeDialog } from "@/components/subscribe-dialog";
import { SettingsProvider, SettingsButton } from "@/components/settings-provider";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

const INITIAL_FILTERS: SearchFilters = { query: "AI", limit: 20 };
const OFFSET_CEILING = 5000; // Upwork caps pagination around this offset

export default function Home() {
  const [filters, setFilters] = React.useState<SearchFilters>(INITIAL_FILTERS);
  const [jobs, setJobs] = React.useState<JobResult[]>([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [loadingMore, setLoadingMore] = React.useState(false);
  // ponytail: abort the in-flight request so rapid filter changes don't race/flicker.
  const controllerRef = React.useRef<AbortController | null>(null);
  // The filters the currently-shown results belong to — so "Load more" pages the
  // SAME query even if the user has since edited (but not yet re-run) the filters.
  const activeFiltersRef = React.useRef<SearchFilters>(INITIAL_FILTERS);

  const fetchPage = React.useCallback(async (query: SearchFilters, offset: number, append: boolean) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    if (append) setLoadingMore(true);
    else {
      setLoading(true);
      activeFiltersRef.current = query;
    }
    try {
      const res = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...query, offset }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => null)) as ApiError | null;
        throw new Error(data?.error ?? `Search failed (${res.status})`);
      }
      const data = (await res.json()) as SearchResponse;
      setJobs((prev) => (append ? [...prev, ...data.jobs] : data.jobs));
      setTotal(data.total);
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") return; // superseded
      const message = err instanceof Error ? err.message : "Something went wrong";
      toast.error("Couldn't load jobs", { description: message });
      if (!append) {
        setJobs([]);
        setTotal(0);
      }
    } finally {
      if (controllerRef.current === controller) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, []);

  const runSearch = React.useCallback((next: SearchFilters) => fetchPage(next, 0, false), [fetchPage]);
  const loadMore = React.useCallback(
    () => fetchPage(activeFiltersRef.current, jobs.length, true),
    [fetchPage, jobs.length],
  );

  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional: run the initial search on mount
    fetchPage(INITIAL_FILTERS, 0, false);
  }, [fetchPage]);

  const countLabel = loading
    ? "Searching…"
    : total > 0
      ? `${jobs.length.toLocaleString()} of ${total.toLocaleString()} ${total === 1 ? "job" : "jobs"}`
      : "0 jobs";

  const remaining = total - jobs.length;
  const hasMore = !loading && jobs.length > 0 && remaining > 0 && jobs.length < OFFSET_CEILING;

  return (
    <SettingsProvider>
    <div className="mx-auto w-full max-w-4xl px-4 pb-20">
      <header className="flex items-start justify-between gap-3 pt-6 sm:pt-10">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Upwork Job Search</h1>
          <p className="mt-1 text-sm text-muted-foreground sm:text-base">
            Browse Upwork jobs — no account needed.
          </p>
        </div>
        <SettingsButton />
      </header>

      <div className="mt-4">
        <SearchPanel filters={filters} onFiltersChange={setFilters} onSearch={runSearch} loading={loading} />
      </div>

      <div className="mt-6 mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground" aria-live="polite">
          {countLabel}
        </p>
        <SubscribeDialog currentFilters={filters} />
      </div>

      <JobResults jobs={jobs} loading={loading} />

      {hasMore && (
        <div className="mt-6 flex justify-center">
          <Button
            variant="outline"
            size="lg"
            onClick={loadMore}
            disabled={loadingMore}
            className="min-w-44"
          >
            {loadingMore ? (
              <>
                <Spinner className="size-4" /> Loading…
              </>
            ) : (
              `Load more (${remaining.toLocaleString()} left)`
            )}
          </Button>
        </div>
      )}
    </div>
    </SettingsProvider>
  );
}
