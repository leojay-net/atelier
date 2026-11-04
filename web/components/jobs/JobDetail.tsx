"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { RotateCcw, Ban, CornerUpLeft } from "lucide-react";
import type { Job } from "@/lib/types";
import { useJobLive } from "@/hooks/useJobLive";
import { JobStatusBanner } from "./JobStatusBanner";
import { EventTimeline } from "./EventTimeline";
import { ResultPreview } from "./ResultPreview";
import { ChecksList } from "./ChecksList";
import { SaveAsRecipeDialog } from "./SaveAsRecipeDialog";
import { RefinePanel } from "./RefinePanel";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/errors/ErrorBanner";

export const RETRY_STORAGE_KEY = "atelier:retry";

interface JobDetailProps {
  jobId: string;
  initialJob: Job;
}

function TryAgainButton({ job }: { job: Job }) {
  const router = useRouter();
  return (
    <Button
      variant="secondary"
      size="sm"
      onClick={() => {
        sessionStorage.setItem(
          RETRY_STORAGE_KEY,
          JSON.stringify(
            job.kind === "spec"
              ? { mode: "spec", spec: job.spec }
              : { mode: "describe", prompt: job.prompt, assets: job.assets },
          ),
        );
        router.push("/");
      }}
    >
      <RotateCcw size={14} />
      Try again
    </Button>
  );
}

export function JobDetail({ jobId, initialJob }: JobDetailProps) {
  const { job, events } = useJobLive(jobId, initialJob);
  const [cancelling, setCancelling] = useState(false);
  const canCancel = job.status === "queued" || job.status === "running";

  async function cancel() {
    setCancelling(true);
    try {
      await fetch(`/api/proxy/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <JobStatusBanner job={job} />
        {canCancel ? (
          <Button variant="ghost" size="sm" loading={cancelling} onClick={cancel}>
            <Ban size={14} />
            Cancel
          </Button>
        ) : null}
      </div>

      {job.parent ? (
        <Link
          href={`/jobs/${job.parent}`}
          className="inline-flex items-center gap-1.5 text-[12px] text-fg-secondary hover:text-fg"
        >
          <CornerUpLeft size={13} />
          Follow-up to an earlier request
        </Link>
      ) : null}

      {job.prompt ? (
        <p className="rounded-md border border-border bg-bg-subtle px-4 py-3 text-[13px] text-fg">
          {job.prompt}
        </p>
      ) : null}

      {job.status === "needs_input" ? (
        <RefinePanel key={job.id} job={job} question={job.result?.question} />
      ) : null}

      {job.status === "failed" ? (
        <div className="space-y-3">
          <ErrorBanner message={job.error || "The job failed."} />
          <TryAgainButton job={job} />
        </div>
      ) : null}

      <EventTimeline events={events} />

      {job.result?.outputs ? <ResultPreview outputs={job.result.outputs} /> : null}
      {job.result?.checks ? <ChecksList checks={job.result.checks} /> : null}

      {job.result?.warnings && job.result.warnings.length > 0 ? (
        <div className="space-y-1 rounded-md border border-status-warn/30 bg-status-warn/5 px-4 py-3 text-[13px] text-fg">
          {job.result.warnings.map((w, i) => (
            <p key={i}>{w}</p>
          ))}
        </div>
      ) : null}

      {(job.status === "succeeded" || job.status === "checks_failed") && job.spec ? (
        <div className="flex justify-end">
          <SaveAsRecipeDialog jobId={jobId} />
        </div>
      ) : null}

      {job.status === "succeeded" || job.status === "checks_failed" || job.status === "failed" ? (
        <RefinePanel key={job.id} job={job} />
      ) : null}
    </div>
  );
}
