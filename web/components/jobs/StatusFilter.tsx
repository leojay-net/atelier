"use client";

import { useRouter, useSearchParams } from "next/navigation";
import clsx from "clsx";
import type { JobStatus } from "@/lib/types";

const OPTIONS: { value: JobStatus | ""; label: string }[] = [
  { value: "", label: "All" },
  { value: "queued", label: "Queued" },
  { value: "running", label: "Running" },
  { value: "succeeded", label: "Succeeded" },
  { value: "checks_failed", label: "Checks failed" },
  { value: "needs_input", label: "Needs input" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
];

export function StatusFilter() {
  const router = useRouter();
  const params = useSearchParams();
  const current = params.get("status") ?? "";

  return (
    <div className="flex flex-wrap gap-1.5">
      {OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => {
            const next = new URLSearchParams(params.toString());
            if (opt.value) next.set("status", opt.value);
            else next.delete("status");
            next.delete("offset");
            router.push(`/jobs?${next.toString()}`);
          }}
          className={clsx(
            "rounded-sm px-2.5 py-1 text-[12.5px] font-medium transition-colors",
            current === opt.value
              ? "bg-accent text-on-accent"
              : "bg-bg-inset text-fg-secondary hover:text-fg",
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
