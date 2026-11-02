import Link from "next/link";
import type { Job } from "@/lib/types";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { Badge } from "@/components/ui/Badge";
import { formatRelative, formatDuration } from "@/lib/format";

function titleFor(job: Job): string {
  if (job.kind === "prompt") return job.prompt || "(empty prompt)";
  if (job.recipe) return `recipe: ${job.recipe}`;
  const steps = (job.spec?.steps as unknown[] | undefined)?.length;
  return steps ? `spec, ${steps} step${steps === 1 ? "" : "s"}` : "spec";
}

export function JobRow({ job }: { job: Job }) {
  const duration =
    job.started_at && job.finished_at
      ? (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000
      : null;

  return (
    <Link
      href={`/jobs/${job.id}`}
      className="flex items-center gap-3 rounded-sm border border-border px-4 py-3 hover:bg-bg-subtle"
    >
      <StatusBadge status={job.status} />
      <Badge>{job.kind}</Badge>
      <span className="min-w-0 flex-1 truncate text-[13px] text-fg">{titleFor(job)}</span>
      {duration !== null ? (
        <span className="shrink-0 text-[12px] text-fg-tertiary">{formatDuration(duration)}</span>
      ) : null}
      <span className="shrink-0 text-[12px] text-fg-tertiary">
        {formatRelative(job.created_at)}
      </span>
    </Link>
  );
}
