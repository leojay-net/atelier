"use client";

/* eslint-disable react-hooks/refs -- `edit` is one object from useEdit; its functions read refs only in event handlers, which the rule can't see through */

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { Maximize2 } from "lucide-react";
import { contentUrl } from "@/components/media/MediaPreview";
import { RATIO, TOP, type Edit } from "./useEdit";

/** The picture: the video fitted into the space it has (keeping its shape, or the chosen one),
 * with the hand-made texts drawn on top. Hand edits show here live. */
export function EditPlayer({ edit }: { edit: Edit }) {
  const root = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const [area, setArea] = useState<[number, number]>([0, 0]);
  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const watch = new ResizeObserver(([entry]) => {
      if (entry) setArea([entry.contentRect.width, entry.contentRect.height]);
    });
    watch.observe(el);
    return () => watch.disconnect();
  }, []);

  // space plays and pauses, unless you are typing
  const toggle = edit.toggle;
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.code !== "Space" || t?.closest("input, textarea, select, button, [contenteditable]")) return;
      e.preventDefault();
      toggle();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [toggle]);

  const { natural, shape, focus, now, texts } = edit;
  const ratio = shape === "original" ? natural[0] / natural[1] : RATIO[shape];
  const fit: [number, number] =
    area[0] / Math.max(1, area[1]) > ratio
      ? [area[1] * ratio, area[1]]
      : [area[0], area[0] / ratio];
  const shown = (edit.muted.text ? [] : texts).filter((t) => now >= t.start && now < t.end);

  return (
    <div ref={root} className="group/player relative flex min-h-0 min-w-0 flex-1 bg-[var(--ws-canvas)] [&:fullscreen]:bg-black">
    <button
      type="button"
      title="Full screen (Esc to leave)"
      aria-label="Full screen"
      onClick={() => (document.fullscreenElement ? document.exitFullscreen() : root.current?.requestFullscreen())}
      className="absolute right-2 top-2 z-10 grid h-8 w-8 place-items-center rounded-md bg-black/60 text-white opacity-0 transition-opacity hover:bg-black/80 focus:opacity-100 group-hover/player:opacity-100"
    >
      <Maximize2 size={15} />
    </button>
    <div ref={stage} className="flex min-h-0 min-w-0 flex-1 items-center justify-center">
      <div
        className="relative overflow-hidden rounded-md shadow-[0_0_0_1px_rgba(255,255,255,0.06)]"
        style={{ width: fit[0], height: fit[1], containerType: "size" }}
      >
        <video
          key={edit.source}
          ref={edit.videoRef}
          src={contentUrl(edit.source)}
          preload="auto"
          playsInline
          onLoadedMetadata={(e) => {
            edit.setDuration(e.currentTarget.duration);
            edit.setNatural([e.currentTarget.videoWidth || 16, e.currentTarget.videoHeight || 9]);
          }}
          onTimeUpdate={edit.onTime}
          onEnded={() => edit.setPlaying(false)}
          onClick={edit.toggle}
          className="h-full w-full cursor-pointer object-cover"
          style={{ objectPosition: `${focus[0] * 100}% ${focus[1] * 100}%` }}
        />
        {shown.map((t) => (
          <p
            key={t.id}
            className={clsx(
              "pointer-events-none absolute inset-x-[8%] text-center font-bold leading-tight",
              t.style === "bold_pop" ? "text-[#ffe14d]" : "text-white",
            )}
            style={{
              top: TOP[t.position],
              fontSize: "clamp(14px, 4.5cqh, 42px)",
              textShadow: "0 0 3px #000, 0 2px 6px #000",
            }}
          >
            {t.text}
          </p>
        ))}
      </div>
    </div>
    </div>
  );
}
