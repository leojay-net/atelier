import { Suspense } from "react";
import { ListChecks } from "lucide-react";
import { api } from "@/lib/api";
import type { JobStatus } from "@/lib/types";
import { JobRow } from "@/components/jobs/JobRow";
import { StatusFilter } from "@/components/jobs/StatusFilter";
import { JobsAutoRefresh } from "@/components/jobs/JobsAutoRefresh";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pagination } from "@/components/ui/Pagination";
import { Button } from "@/components/ui/Button";
import Link from "next/link";

const LIMIT = 20;

// Always live data from Django, never a build-time static render.
export const dynamic = "force-dynamic";

export default async function JobsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; offset?: string }>;
}) {
  const { status, offset: offsetParam } = await searchParams;
  const offset = Number(offsetParam ?? 0) || 0;
  const { items, count } = await api.listJobs({ status, limit: LIMIT, offset });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="animate-rise text-[30px] font-semibold leading-tight tracking-[-0.03em] text-fg">Jobs</h1>
        <p className="mt-1 text-[13px] text-fg-secondary">{count} total.</p>
      </div>

      <Suspense>
        <StatusFilter />
      </Suspense>

      {items.length === 0 ? (
        <EmptyState
          icon={ListChecks}
          title="No jobs yet"
          description="Make something in Create to see it here."
          action={
            <Link href="/">
              <Button size="sm">Start creating</Button>
            </Link>
          }
        />
      ) : (
        <>
          <ul className="space-y-1.5">
            {items.map((job) => (
              <li key={job.id}>
                <JobRow job={job} />
              </li>
            ))}
          </ul>
          <Pagination
            count={count}
            limit={LIMIT}
            offset={offset}
            hrefFor={(o) => `/jobs?${status ? `status=${status}&` : ""}offset=${o}`}
          />
        </>
      )}

      <JobsAutoRefresh statuses={items.map((j) => j.status as JobStatus)} />
    </div>
  );
}
