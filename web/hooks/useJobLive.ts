"use client";

import { useEffect, useReducer, useRef } from "react";
import type { Job, JobEvent, JobStatus } from "@/lib/types";
import { isTerminal } from "@/lib/types";
import { readSseFrames } from "@/lib/sse";

const STALL_TIMEOUT_MS = 40_000; // comfortably over the server's ~15s keepalive cadence
const POLL_INTERVAL_MS = 1500;

export interface JobLiveState {
  job: Job;
  events: JobEvent[];
  transport: "sse" | "polling" | "none";
  connection: "connecting" | "open" | "closed" | "error";
}

type Action =
  | { type: "events"; events: JobEvent[] }
  | { type: "job"; job: Job }
  | { type: "transport"; transport: JobLiveState["transport"] }
  | { type: "connection"; connection: JobLiveState["connection"] };

/** Dedup by seq (keep the latest), sorted ascending. The same merge serves both transports, so
 * handing off from SSE to polling mid-job is invisible to anything reading this state. */
function mergeEvents(existing: JobEvent[], incoming: JobEvent[]): JobEvent[] {
  if (incoming.length === 0) return existing;
  const bySeq = new Map(existing.map((e) => [e.seq, e]));
  for (const e of incoming) bySeq.set(e.seq, e);
  return Array.from(bySeq.values()).sort((a, b) => a.seq - b.seq);
}

function reducer(state: JobLiveState, action: Action): JobLiveState {
  switch (action.type) {
    case "events":
      return { ...state, events: mergeEvents(state.events, action.events) };
    case "job":
      return { ...state, job: action.job };
    case "transport":
      return { ...state, transport: action.transport };
    case "connection":
      return { ...state, connection: action.connection };
    default:
      return state;
  }
}

function maxSeq(events: JobEvent[]): number {
  return events.reduce((max, e) => Math.max(max, e.seq), 0);
}

export function useJobLive(jobId: string, initialJob: Job): JobLiveState {
  const initialTerminal = isTerminal(initialJob.status);
  const [state, dispatch] = useReducer(reducer, {
    job: initialJob,
    events: initialJob.events ?? [],
    transport: "none",
    connection: initialTerminal ? "closed" : "connecting",
  });
  const lastSeqRef = useRef(maxSeq(initialJob.events ?? []));

  useEffect(() => {
    if (initialTerminal) return;
    const controller = new AbortController();
    let cancelled = false;

    async function finalize() {
      if (cancelled) return;
      try {
        const res = await fetch(`/api/proxy/jobs/${encodeURIComponent(jobId)}`, {
          signal: controller.signal,
        });
        if (res.ok) {
          const job: Job = await res.json();
          if (job.events) dispatch({ type: "events", events: job.events });
          dispatch({ type: "job", job });
        }
      } catch {
        // best effort: the UI keeps whatever state it already has
      } finally {
        if (!cancelled) dispatch({ type: "connection", connection: "closed" });
      }
    }

    async function runSse(): Promise<"ended" | "failed"> {
      dispatch({ type: "transport", transport: "sse" });
      dispatch({ type: "connection", connection: "connecting" });

      let res: Response;
      try {
        const after = lastSeqRef.current ? `?after=${lastSeqRef.current}` : "";
        res = await fetch(`/api/proxy/stream/${encodeURIComponent(jobId)}${after}`, {
          signal: controller.signal,
        });
      } catch {
        return "failed";
      }
      if (!res.ok || !res.body) return "failed";
      dispatch({ type: "connection", connection: "open" });

      let stallTimer: ReturnType<typeof setTimeout>;
      let resolveStall: (v: "stall") => void;
      const stalled = new Promise<"stall">((resolve) => {
        resolveStall = resolve;
      });
      const resetStallTimer = () => {
        clearTimeout(stallTimer);
        stallTimer = setTimeout(() => resolveStall("stall"), STALL_TIMEOUT_MS);
      };
      resetStallTimer();

      const frames = readSseFrames(res.body, resetStallTimer);
      try {
        for (;;) {
          const outcome = await Promise.race([frames.next(), stalled]);
          if (outcome === "stall") {
            await frames.return(undefined);
            return "failed";
          }
          const { done, value: frame } = outcome;
          if (done) return "failed"; // closed without a clean "end" frame
          if (frame.event === "end") return "ended";
          if (!frame.data) continue;
          try {
            const event: JobEvent = JSON.parse(frame.data);
            lastSeqRef.current = Math.max(lastSeqRef.current, event.seq);
            dispatch({ type: "events", events: [event] });
          } catch {
            // a malformed frame is skipped, not fatal to the whole stream
          }
        }
      } finally {
        clearTimeout(stallTimer!);
      }
    }

    async function runPolling() {
      dispatch({ type: "transport", transport: "polling" });
      for (;;) {
        if (cancelled) return;
        try {
          const res = await fetch(
            `/api/proxy/jobs/${encodeURIComponent(jobId)}/events.json?after=${lastSeqRef.current}`,
            { signal: controller.signal },
          );
          if (res.ok) {
            dispatch({ type: "connection", connection: "open" });
            const body: { status: JobStatus; finished: boolean; events: JobEvent[] } =
              await res.json();
            if (body.events.length) {
              lastSeqRef.current = Math.max(lastSeqRef.current, maxSeq(body.events));
              dispatch({ type: "events", events: body.events });
            }
            if (body.finished) return;
          } else {
            dispatch({ type: "connection", connection: "error" });
          }
        } catch {
          dispatch({ type: "connection", connection: "error" });
        }
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      }
    }

    (async () => {
      const outcome = await runSse();
      if (cancelled) return;
      if (outcome === "failed") await runPolling();
      await finalize();
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
    // initialTerminal/initialJob are a one-time seed for this effect, intentionally not deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  return state;
}
