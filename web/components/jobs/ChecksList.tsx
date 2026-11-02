"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import clsx from "clsx";
import type { Check } from "@/lib/types";
import { StatusBadge } from "@/components/ui/StatusBadge";

interface ChecksListProps {
  checks: Record<string, Check[]>;
}

export function ChecksList({ checks }: ChecksListProps) {
  const entries = Object.entries(checks).filter(([, list]) => list.length > 0);
  const hasProblem = entries.some(([, list]) => list.some((c) => c.status !== "pass"));
  const [open, setOpen] = useState(hasProblem);

  if (entries.length === 0) return null;

  return (
    <div className="rounded-md border border-border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <span className="text-[13px] font-medium text-fg">
          Checks {hasProblem ? "(needs attention)" : "(all passing)"}
        </span>
        <ChevronDown
          size={15}
          className={clsx("text-fg-tertiary transition-transform", open && "rotate-180")}
        />
      </button>
      {open ? (
        <div className="space-y-3 border-t border-border px-4 py-3">
          {entries.map(([outputId, list]) => (
            <div key={outputId} className="space-y-1.5">
              <p className="text-[12px] font-medium text-fg-tertiary">{outputId}</p>
              {list.map((check, i) => (
                <div key={i} className="flex items-start gap-2 text-[13px]">
                  <StatusBadge status={check.status} kind="check" className="mt-0.5 shrink-0" />
                  <div>
                    <p className="text-fg">{check.name}</p>
                    {check.message ? (
                      <p className="text-fg-secondary">{check.message}</p>
                    ) : null}
                    {check.hint ? (
                      <p className="text-fg-tertiary">Hint: {check.hint}</p>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
