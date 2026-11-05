import { Loader2 } from "lucide-react";

/** Shown the moment a chat is clicked (and prefetched with the sidebar links), while the
 * conversation loads: the studio's shape, so the page doesn't sit still or jump. */
export default function OpeningChat() {
  return (
    <div className="flex h-full min-h-[70vh] gap-0 overflow-hidden rounded-xl" aria-busy="true" aria-label="Opening the chat">
      <section className="flex flex-1 flex-col items-center justify-center gap-3 p-6">
        <div className="flex aspect-video w-full max-w-3xl animate-pulse items-center justify-center rounded-xl bg-bg-inset">
          <Loader2 size={26} className="animate-spin text-fg-tertiary" />
        </div>
        <p className="text-[13px] text-fg-secondary">Opening your chat…</p>
      </section>
      <aside className="hidden w-[400px] shrink-0 flex-col gap-3 border-l border-border p-4 lg:flex">
        {[70, 90, 55].map((w, i) => (
          <div
            key={i}
            className={`h-14 animate-pulse rounded-2xl bg-bg-inset ${i % 2 ? "self-start" : "self-end"}`}
            style={{ width: `${w}%` }}
          />
        ))}
      </aside>
    </div>
  );
}
