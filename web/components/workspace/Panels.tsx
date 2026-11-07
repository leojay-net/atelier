"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { ChevronLeft, ChevronRight } from "lucide-react";

/** A panel width the user can drag, remembered between visits. 0 means collapsed. */
export function usePanelWidth(key: string, initial: number, min = 200, max = 560) {
  const [width, setWidth] = useState(initial);
  const [last, setLast] = useState(initial);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(`atelier:panel:${key}`);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- restoring a saved preference once
      if (raw !== null) setWidth(Math.max(0, Math.min(max, Number(raw) || 0)));
    } catch {
      // storage is a nicety
    }
  }, [key, max]);
  const set = useCallback(
    (w: number) => {
      const next = w < min / 2 ? 0 : Math.max(min, Math.min(max, w));
      setWidth(next);
      if (next) setLast(next);
      try {
        localStorage.setItem(`atelier:panel:${key}`, String(next));
      } catch {
        // ignore
      }
    },
    [key, min, max],
  );
  const toggle = useCallback(() => set(width ? 0 : last || initial), [set, width, last, initial]);
  return { width, set, toggle };
}

/** The thin bar between two panels: drag to resize (past the minimum it collapses), double-click
 * to collapse or bring the panel back. `side` is the side of the bar the resized panel is on. */
export function Splitter({
  side,
  width,
  onChange,
  onToggle,
  label,
}: {
  side: "left" | "right";
  width: number;
  onChange: (w: number) => void;
  onToggle: () => void;
  label: string;
}) {
  const [active, setActive] = useState(false);
  function down(e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    const x0 = e.clientX;
    const w0 = width;
    setActive(true);
    const move = (m: PointerEvent) => onChange(w0 + (side === "left" ? 1 : -1) * (m.clientX - x0));
    const up = () => {
      setActive(false);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={`Resize ${label}`}
      title="Drag to resize. Double-click to hide or show."
      onPointerDown={down}
      onDoubleClick={onToggle}
      className="group relative z-10 hidden w-px shrink-0 cursor-col-resize bg-border md:block"
    >
      <span className="absolute inset-y-0 -left-1.5 -right-1.5" />
      <span
        className={clsx(
          "absolute inset-y-0 -left-px w-[3px] rounded-full transition-all duration-200",
          active ? "bg-accent opacity-100" : "bg-accent opacity-0 group-hover:opacity-70",
        )}
      />
      {width === 0 ? (
        <button
          type="button"
          aria-label={`Show ${label}`}
          onClick={onToggle}
          onPointerDown={(e) => e.stopPropagation()}
          className={clsx(
            "absolute top-1/2 grid h-12 w-4 -translate-y-1/2 place-items-center bg-bg-inset text-fg-secondary hover:text-fg",
            side === "left" ? "left-1 rounded-r-md" : "right-1 rounded-l-md",
          )}
        >
          {side === "left" ? <ChevronRight size={12} /> : <ChevronLeft size={12} />}
        </button>
      ) : null}
    </div>
  );
}
