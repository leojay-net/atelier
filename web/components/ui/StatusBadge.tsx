import type { CheckStatus, JobStatus } from "@/lib/types";

/**
 * The single table mapping a job status or a check status to a color and label. Every badge in
 * the app (job rows, the job detail banner, check rows) reads from this, so there is exactly one
 * place to get the mapping right and one place to change it. Job and check status values never
 * overlap, so one flat table serves both without any branching on the caller's "kind".
 */
const STATUS: Record<JobStatus | CheckStatus, { color: string; label: string }> = {
  queued: { color: "var(--color-status-queued)", label: "Queued" },
  running: { color: "var(--color-status-running)", label: "Running" },
  succeeded: { color: "var(--color-status-succeeded)", label: "Succeeded" },
  checks_failed: { color: "var(--color-status-warn)", label: "Checks failed" },
  needs_input: { color: "var(--color-status-needs-input)", label: "Needs input" },
  failed: { color: "var(--color-status-failed)", label: "Failed" },
  cancelled: { color: "var(--color-status-cancelled)", label: "Cancelled" },
  pass: { color: "var(--color-status-succeeded)", label: "Pass" },
  warn: { color: "var(--color-status-warn)", label: "Warn" },
  fail: { color: "var(--color-status-failed)", label: "Fail" },
};

interface StatusBadgeProps {
  status: JobStatus | CheckStatus;
  /** Unused; kept so call sites can note which kind of status they are passing for clarity. */
  kind?: "job" | "check";
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const entry = STATUS[status];
  if (!entry) return null;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-[11px] font-medium text-white ${className ?? ""}`}
      style={{ backgroundColor: entry.color }}
    >
      {entry.label}
    </span>
  );
}

export function statusColor(status: JobStatus | CheckStatus): string {
  return STATUS[status]?.color ?? "var(--color-fg-tertiary)";
}

export function statusLabel(status: JobStatus | CheckStatus): string {
  return STATUS[status]?.label ?? status;
}
