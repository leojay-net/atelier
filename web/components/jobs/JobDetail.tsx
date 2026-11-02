"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { RotateCcw, Send, Ban } from "lucide-react";
import type { Job } from "@/lib/types";
import { useJobLive } from "@/hooks/useJobLive";
import { JobStatusBanner } from "./JobStatusBanner";
import { EventTimeline } from "./EventTimeline";
import { ResultPreview } from "./ResultPreview";
import { ChecksList } from "./ChecksList";
import { SaveAsRecipeDialog } from "./SaveAsRecipeDialog";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/errors/ErrorBanner";
import { readApiError } from "@/lib/errors";

export const RETRY_STORAGE_KEY = "atelier:retry";

interface JobDetailProps {
  jobId: string;
  initialJob: Job;
}

function NeedsInputPanel({ job }: { job: Job }) {
  const router = useRouter();
  const [answer, setAnswer] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    if (!answer.trim()) return;
    setSending(true);
    setError(null);
    try {
      const res = await fetch("/api/proxy/jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt: answer, assets: job.assets }),
      });
      if (!res.ok) throw new Error((await readApiError(res)).message);
      const next: Job = await res.json();
      router.push(`/jobs/${next.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the answer.");
      setSending(false);
    }
  }

  return (
    <div className="space-y-3 rounded-md border border-status-needs-input/30 bg-status-needs-input/5 p-4">
      <p className="text-[14px] font-medium text-fg">{job.result?.question}</p>
      <div className="space-y-2">
        <textarea
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          placeholder="Your answer"
          rows={3}
          className="w-full resize-none rounded-sm border border-border-strong bg-bg px-3 py-2 text-[13px] text-fg outline-none focus:border-accent"
        />
        {error ? <ErrorBanner message={error} /> : null}
        <div className="flex justify-end">
          <Button size="sm" loading={sending} disabled={!answer.trim()} onClick={send}>
            <Send size={14} />
            Send
          </Button>
        </div>
      </div>
    </div>
  );
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

      {job.status === "needs_input" ? <NeedsInputPanel job={job} /> : null}

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
    </div>
  );
}
