"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { isTerminal, type JobStatus } from "@/lib/types";

const INTERVAL_MS = 3000;

/** Invisible: refreshes the Server Component list behind it, but only while it rendered at
 * least one non-terminal job, and stops as soon as none remain. */
export function JobsAutoRefresh({ statuses }: { statuses: JobStatus[] }) {
  const router = useRouter();
  const hasActive = statuses.some((s) => !isTerminal(s));

  useEffect(() => {
    if (!hasActive) return;
    const id = setInterval(() => router.refresh(), INTERVAL_MS);
    return () => clearInterval(id);
  }, [hasActive, router]);

  return null;
}
