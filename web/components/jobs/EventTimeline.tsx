"use client";

import { useState } from "react";
import { Check, ChevronRight, Loader2 } from "lucide-react";
import clsx from "clsx";
import type { JobEvent } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { formatDuration } from "@/lib/format";

interface EventTimelineProps {
  events: JobEvent[];
}

interface StepRow {
  id: string;
  op: string;
  state: "running" | "done";
  seconds?: number;
  cached?: boolean;
  fused?: string[];
}

function buildRows(events: JobEvent[]): {
  planLine: string | null;
  steps: StepRow[];
  sub: { label: string; raw: JobEvent }[];
  finishedStatus: string | null;
} {
  const steps = new Map<string, StepRow>();
  const order: string[] = [];
  let planLine: string | null = null;
  const sub: { label: string; raw: JobEvent }[] = [];
  let finishedStatus: string | null = null;

  for (const event of events) {
    const data = event.data as Record<string, unknown>;
    if (event.kind === "plan") {
      const stepCount = typeof data.steps === "number" ? data.steps : undefined;
      planLine = stepCount !== undefined ? `Plan: ${stepCount} step${stepCount === 1 ? "" : "s"}` : "Plan";
    } else if (event.kind === "step_start") {
      const id = String(data.step ?? order.length);
      if (!steps.has(id)) order.push(id);
      steps.set(id, { id, op: String(data.op ?? "step"), state: "running" });
    } else if (event.kind === "step_done") {
      const id = String(data.step ?? order.length);
      if (!steps.has(id)) order.push(id);
      steps.set(id, {
        id,
        op: String(data.op ?? "step"),
        state: "done",
        seconds: typeof data.seconds === "number" ? data.seconds : undefined,
        cached: Boolean(data.cached),
        fused: Array.isArray(data.fused) ? (data.fused as string[]) : undefined,
      });
    } else if (event.kind === "turn") {
      sub.push({ label: "Thinking...", raw: event });
    } else if (event.kind === "tools") {
      const names = Array.isArray(data.tools) ? (data.tools as string[]).join(", ") : "a tool";
      sub.push({ label: `Using ${names}`, raw: event });
    } else if (event.kind === "finished") {
      finishedStatus = typeof data.status === "string" ? data.status : null;
    }
  }

  return { planLine, steps: order.map((id) => steps.get(id)!), sub, finishedStatus };
}

function RawEventDisclosure({ event }: { event: JobEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-0.5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-[11px] text-fg-tertiary hover:text-fg-secondary"
      >
        <ChevronRight size={11} className={clsx("transition-transform", open && "rotate-90")} />
        Show raw event
      </button>
      {open ? (
        <pre className="mt-1 overflow-x-auto rounded-sm bg-bg-inset p-2 font-mono text-[11px] text-fg-secondary">
          {JSON.stringify(event.data, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}

export function EventTimeline({ events }: EventTimelineProps) {
  const { planLine, steps, sub } = buildRows(events);

  if (events.length === 0) {
    return <p className="text-[13px] text-fg-tertiary">Waiting to start...</p>;
  }

  return (
    <div className="space-y-3">
      {planLine ? <p className="text-[13px] font-medium text-fg">{planLine}</p> : null}

      <ol className="space-y-0">
        {steps.map((step) => (
          <li key={step.id} className="flex gap-3 border-l border-border pb-4 pl-4 last:pb-0">
            <span
              className={clsx(
                "-ml-[21px] mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full",
                step.state === "running" ? "animate-pulse-dot bg-status-running" : "bg-status-succeeded",
              )}
            >
              {step.state === "done" ? <Check size={9} strokeWidth={3} className="text-white" /> : null}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <code className="font-mono text-[13px] text-fg">{step.op}</code>
                {step.state === "running" ? (
                  <span className="inline-flex items-center gap-1 text-[12px] text-fg-tertiary">
                    <Loader2 size={11} className="animate-spin" />
                    running
                  </span>
                ) : (
                  <>
                    {typeof step.seconds === "number" ? (
                      <span className="text-[12px] text-fg-tertiary">
                        {formatDuration(step.seconds)}
                      </span>
                    ) : null}
                    {step.cached ? <Badge>cached</Badge> : null}
                  </>
                )}
              </div>
              {step.fused && step.fused.length > 0 ? (
                <p className="mt-0.5 text-[12px] text-fg-tertiary">
                  fused with {step.fused.join(", ")}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>

      {sub.length > 0 ? (
        <div className="space-y-1.5 border-l border-border pb-1 pl-4">
          {sub.map((row, i) => (
            <div key={i} className="-ml-4 pl-4 text-[12px] text-fg-secondary">
              {row.label}
              <RawEventDisclosure event={row.raw} />
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
