import { SearchXIcon } from "lucide-react";

import type { JobResult } from "@/lib/types";
import { JobCard } from "@/components/job-card";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";

function JobCardSkeleton() {
  return (
    <Card aria-hidden>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-4 w-12" />
        </div>
        <div className="flex gap-2 pt-1">
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-5 w-16" />
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <Skeleton className="h-3.5 w-full" />
          <Skeleton className="h-3.5 w-full" />
          <Skeleton className="h-3.5 w-4/5" />
        </div>
        <div className="flex gap-1.5">
          <Skeleton className="h-5 w-16" />
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-5 w-14" />
        </div>
      </CardContent>
    </Card>
  );
}

export function JobResults({
  jobs,
  loading,
}: {
  jobs: JobResult[];
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="flex flex-col gap-3 sm:gap-4">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <JobCardSkeleton key={i} />
        ))}
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <Empty className="border">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <SearchXIcon />
          </EmptyMedia>
          <EmptyTitle>No jobs match</EmptyTitle>
          <EmptyDescription>
            Try widening your filters or searching different keywords.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <div className="flex flex-col gap-3 sm:gap-4">
      {jobs.map((job) => (
        <JobCard key={job.url} job={job} />
      ))}
    </div>
  );
}
