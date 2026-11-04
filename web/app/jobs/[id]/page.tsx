import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { ApiClientError } from "@/lib/errors";
import { JobDetail } from "@/components/jobs/JobDetail";
import { formatDate } from "@/lib/format";
import Link from "next/link";

// Always live data from Django, never a build-time static render.
export const dynamic = "force-dynamic";

export default async function JobDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let job;
  try {
    job = await api.getJob(id);
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) notFound();
    throw err;
  }

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="font-mono text-[15px] font-semibold tracking-tight text-fg">{job.id}</h1>
          <p className="mt-1 text-[13px] text-fg-secondary">Created {formatDate(job.created_at)}</p>
        </div>
        <Link
          href={`/studio/${job.id}`}
          className="shrink-0 rounded-md bg-accent px-3 py-1.5 text-[13px] font-medium text-on-accent hover:bg-accent-hover"
        >
          Continue in chat
        </Link>
      </div>
      <JobDetail jobId={job.id} initialJob={job} />
    </div>
  );
}
