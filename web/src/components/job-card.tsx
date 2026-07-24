"use client";

import * as React from "react";
import { ArrowUpRightIcon } from "lucide-react";

import type { JobResult } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

// RAW enum value -> nice label. Fall back to the raw value if unmapped.
const JOB_TYPE_LABELS: Record<string, string> = {
  HOURLY: "Hourly",
  FIXED: "Fixed price",
  WEEKLY_RETAINER: "Weekly retainer",
};
const TIER_LABELS: Record<string, string> = {
  EntryLevel: "Entry level",
  IntermediateLevel: "Intermediate",
  ExpertLevel: "Expert",
};

/** "$1,500" / "$25" / "$25.50" — null for unparseable/empty. */
function money(value: string | null): string | null {
  if (!value) return null;
  const n = Number.parseFloat(value);
  if (!Number.isFinite(n)) return null;
  return `$${n.toLocaleString(undefined, {
    maximumFractionDigits: Number.isInteger(n) ? 0 : 2,
  })}`;
}

/** Budget line: fixed -> "$500 fixed", hourly -> "$25–$50/hr". null if unknown. */
function budgetLabel(job: JobResult): string | null {
  const isFixed = job.job_type === "FIXED" || (job.job_type == null && !!job.fixed_budget);
  if (isFixed) {
    const b = money(job.fixed_budget);
    return b ? `${b} fixed` : null;
  }
  const lo = money(job.hourly_min);
  const hi = money(job.hourly_max);
  if (lo && hi) return `${lo}–${hi}/hr`;
  if (lo) return `From ${lo}/hr`;
  if (hi) return `Up to ${hi}/hr`;
  return null;
}

/** Compact relative time, e.g. "2h ago". null if missing/invalid. */
function timeAgo(iso: string | null): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const s = Math.floor((Date.now() - t) / 1000);
  if (s < 45) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  const w = Math.floor(d / 7);
  if (w < 5) return `${w}w ago`;
  const mo = Math.floor(d / 30);
  if (mo < 12) return `${mo}mo ago`;
  return `${Math.floor(d / 365)}y ago`;
}

export function JobCard({ job }: { job: JobResult }) {
  const [expanded, setExpanded] = React.useState(false);

  const jobType = job.job_type ? JOB_TYPE_LABELS[job.job_type] ?? job.job_type : null;
  const tier = job.tier ? TIER_LABELS[job.tier] ?? job.tier : null;
  const budget = budgetLabel(job);
  const posted = timeAgo(job.published);

  const skills = job.skills
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const shownSkills = skills.slice(0, 6);
  const extraSkills = skills.length - shownSkills.length;

  const description = job.description.trim();
  // ponytail: length heuristic for the toggle; exact overflow needs measurement we don't need.
  const clampable = description.length > 180 || description.includes("\n");

  return (
    <Card className="transition-shadow hover:ring-foreground/20">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <CardTitle className="pr-1">
            <a
              href={job.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-start gap-1 underline-offset-2 hover:underline focus-visible:underline focus-visible:outline-none"
            >
              <span className="line-clamp-2">{job.title}</span>
              <ArrowUpRightIcon className="mt-0.5 size-3.5 shrink-0 opacity-0 transition-opacity group-hover/card:opacity-50" />
            </a>
          </CardTitle>
          {posted && (
            <span className="shrink-0 pt-0.5 text-xs whitespace-nowrap text-muted-foreground">
              {posted}
            </span>
          )}
        </div>

        {(budget || jobType || tier) && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 pt-1">
            {budget && (
              <span className="text-sm font-semibold text-foreground">{budget}</span>
            )}
            {budget && (jobType || tier) && (
              <span aria-hidden className="text-muted-foreground/40">
                ·
              </span>
            )}
            {jobType && <Badge variant="secondary">{jobType}</Badge>}
            {tier && <Badge variant="outline">{tier}</Badge>}
          </div>
        )}
      </CardHeader>

      <CardContent className="flex flex-col gap-3">
        {description && (
          <div>
            <p
              className={cn(
                "text-sm leading-relaxed whitespace-pre-line text-muted-foreground",
                !expanded && "line-clamp-3"
              )}
            >
              {description}
            </p>
            {clampable && (
              <Button
                variant="link"
                size="sm"
                className="mt-0.5 h-auto p-0 text-xs"
                onClick={() => setExpanded((v) => !v)}
                aria-expanded={expanded}
              >
                {expanded ? "Show less" : "Show more"}
              </Button>
            )}
          </div>
        )}

        {shownSkills.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {shownSkills.map((s) => (
              <Badge key={s} variant="outline" className="font-normal text-muted-foreground">
                {s}
              </Badge>
            ))}
            {extraSkills > 0 && (
              <Badge variant="outline" className="font-normal text-muted-foreground">
                +{extraSkills}
              </Badge>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
