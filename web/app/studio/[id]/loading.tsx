import { Loader2 } from "lucide-react";

/** Shown the moment a project is clicked (and prefetched with the links to it), while it loads:
 * the workspace's own shape, so the page doesn't sit still or jump. */
export default function OpeningProject() {
  return (
    <div className="ws fixed inset-0 z-20 flex flex-col" aria-busy="true" aria-label="Opening the project">
      <div className="h-12 shrink-0 border-b border-border bg-bg" />
      <div className="flex min-h-0 flex-1 gap-1 bg-[var(--ws-canvas)]">
        <div className="hidden w-[232px] shrink-0 bg-bg md:block" />
        <div className="hidden w-[300px] shrink-0 bg-bg md:block" />
        <div className="flex min-w-0 flex-1 flex-col items-center justify-center gap-3">
          <Loader2 size={26} className="animate-spin text-fg-tertiary" />
          <p className="text-[13px] text-fg-secondary">Opening your project…</p>
        </div>
        <div className="hidden w-[380px] shrink-0 flex-col gap-3 bg-bg p-4 md:flex">
          {[70, 90, 55].map((w, i) => (
            <div
              key={i}
              className={`h-14 animate-pulse rounded-2xl bg-bg-inset ${i % 2 ? "self-start" : "self-end"}`}
              style={{ width: `${w}%` }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
