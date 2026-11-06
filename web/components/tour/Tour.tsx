"use client";

import { useCallback, useEffect, useLayoutEffect, useState } from "react";

/** Fired by the "Help" buttons to play the guide again. */
export const TOUR_EVENT = "atelier:tour";

export interface TourStep {
  /** The value of a `data-tour="…"` attribute on the thing to point at. */
  target: string;
  title: string;
  body: string;
}

const PAD = 6;
const CARD_W = 320;
const CARD_H = 190; // a close guess, only used to decide which side has room

const find = (target: string) => document.querySelector<HTMLElement>(`[data-tour="${target}"]`);
const onScreen = (target: string) => {
  const r = find(target)?.getBoundingClientRect();
  return Boolean(r && r.width > 4 && r.height > 4);
};

/**
 * A short guided tour: the page dims, one thing is lit up, and a small card says what it is.
 * It plays by itself the first time (remembered in localStorage, which is only a convenience),
 * and again whenever TOUR_EVENT fires. Steps pointing at something not on screen are skipped.
 */
export function Tour({ id, steps }: { id: string; steps: TourStep[] }) {
  const storeKey = `atelier:tour:${id}`;
  const [live, setLive] = useState<TourStep[]>([]);
  const [at, setAt] = useState<number | null>(null);
  const [rect, setRect] = useState<DOMRect | null>(null);

  const begin = useCallback(() => {
    const available = steps.filter((s) => onScreen(s.target));
    if (!available.length) return;
    setLive(available);
    setAt(0);
  }, [steps]);

  const end = useCallback(() => {
    setAt(null);
    try {
      localStorage.setItem(storeKey, "1");
    } catch {
      // not remembered: it will just show again next time
    }
  }, [storeKey]);

  useEffect(() => {
    let seen = false;
    try {
      seen = localStorage.getItem(storeKey) === "1";
    } catch {
      // treat as not seen
    }
    const timer = seen ? undefined : window.setTimeout(begin, 900); // let the page settle first
    window.addEventListener(TOUR_EVENT, begin);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener(TOUR_EVENT, begin);
    };
  }, [begin, storeKey]);

  const step = at === null ? undefined : live[at];

  useLayoutEffect(() => {
    if (!step) return;
    const measure = () => setRect(find(step.target)?.getBoundingClientRect() ?? null);
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [step]);

  useEffect(() => {
    if (at === null) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") end();
      if (e.key === "ArrowRight") setAt((n) => (n === null ? n : Math.min(live.length - 1, n + 1)));
      if (e.key === "ArrowLeft") setAt((n) => (n === null ? n : Math.max(0, n - 1)));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [at, live.length, end]);

  if (!step || at === null || !rect) return null;

  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const tall = rect.height > vh * 0.5;
  let left: number;
  let top: number;
  if (tall) {
    // a panel running the height of the screen: the card goes beside it
    left = rect.right + 14 + CARD_W < vw ? rect.right + 14 : rect.left - 14 - CARD_W;
    top = rect.top + rect.height / 2 - CARD_H / 2;
  } else {
    left = rect.left + rect.width / 2 - CARD_W / 2;
    top = rect.bottom + 14 + CARD_H < vh ? rect.bottom + 14 : rect.top - 14 - CARD_H;
  }
  left = Math.max(12, Math.min(vw - CARD_W - 12, left));
  top = Math.max(12, Math.min(vh - CARD_H - 12, top));
  const last = at === live.length - 1;

  return (
    <div className="fixed inset-0 z-[100]" role="dialog" aria-label="Guide">
      {/* blocks clicks on the page, and dims it everywhere except the lit area */}
      <div className="absolute inset-0" onClick={end} />
      <div
        className="pointer-events-none absolute rounded-xl transition-all duration-200"
        style={{
          left: rect.left - PAD,
          top: rect.top - PAD,
          width: rect.width + PAD * 2,
          height: rect.height + PAD * 2,
          boxShadow: "0 0 0 9999px rgba(5,7,10,0.72), 0 0 0 2px #5aa2ff",
        }}
      />
      <div
        className="absolute rounded-xl border border-[#333a46] bg-[#181c22] p-4 text-[#e6e9ee] shadow-2xl"
        style={{ left, top, width: CARD_W }}
      >
        <p className="text-[11px] font-semibold uppercase tracking-wider text-[#5aa2ff]">
          Step {at + 1} of {live.length}
        </p>
        <p className="mt-1 text-[15px] font-semibold">{step.title}</p>
        <p className="mt-1 text-[13px] leading-relaxed text-[#a2a9b5]">{step.body}</p>
        <div className="mt-4 flex items-center justify-between">
          <button type="button" onClick={end} className="text-[12.5px] text-[#727a88] hover:text-[#e6e9ee]">
            Skip
          </button>
          <div className="flex gap-2">
            {at > 0 ? (
              <button
                type="button"
                onClick={() => setAt(at - 1)}
                className="rounded-md border border-[#333a46] px-3 py-1.5 text-[13px] hover:bg-[#20252d]"
              >
                Back
              </button>
            ) : null}
            <button
              type="button"
              autoFocus
              onClick={() => (last ? end() : setAt(at + 1))}
              className="rounded-md bg-[#5aa2ff] px-3.5 py-1.5 text-[13px] font-semibold text-[#07101c] hover:bg-[#7cb4ff]"
            >
              {last ? "Done" : "Next"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
