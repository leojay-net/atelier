import clsx from "clsx";
import type { Job } from "@/lib/types";
import { StatusBadge, statusColor } from "@/components/ui/StatusBadge";

function runningLabel(job: Job): string {
  if (job.status !== "running" && job.status !== "queued") return "";
  const events = job.events ?? [];
  const planEvent = [...events].reverse().find((e) => e.kind === "plan");
  const total = planEvent ? Number(planEvent.data.steps ?? 0) : undefined;
  const doneCount = events.filter((e) => e.kind === "step_done").length;
  if (total) return `Step ${Math.min(doneCount + 1, total)} of ${total}`;
  return job.status === "queued" ? "Waiting to start" : "Running";
}

export function JobStatusBanner({ job }: { job: Job }) {
  const color = statusColor(job.status);
  const sub = runningLabel(job);

  return (
    <div
      className="flex items-center gap-3 rounded-md border px-4 py-3"
      style={{ borderColor: `color-mix(in srgb, ${color} 30%, transparent)`, backgroundColor: `color-mix(in srgb, ${color} 7%, transparent)` }}
    >
      <StatusBadge status={job.status} />
      {sub ? <p className="text-[13px] text-fg-secondary">{sub}</p> : null}
      {job.status === "needs_input" ? (
        <p className="text-[13px] font-medium text-fg">The assistant needs more information.</p>
      ) : null}
      {job.kind === "prompt" && job.result?.summary && job.status !== "needs_input" ? (
        <p className={clsx("truncate text-[13px] text-fg-secondary")}>{job.result.summary}</p>
      ) : null}
    </div>
  );
}
