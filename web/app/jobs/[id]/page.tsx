import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { ApiClientError } from "@/lib/errors";
import { JobDetail } from "@/components/jobs/JobDetail";
import { formatDate } from "@/lib/format";

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
      <div>
        <h1 className="font-mono text-[15px] font-semibold tracking-tight text-fg">{job.id}</h1>
        <p className="mt-1 text-[13px] text-fg-secondary">Created {formatDate(job.created_at)}</p>
      </div>
      <JobDetail jobId={job.id} initialJob={job} />
    </div>
  );
}
