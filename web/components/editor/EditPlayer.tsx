"use client";

/* eslint-disable react-hooks/refs -- `edit` is one object from useEdit; its functions read refs only in event handlers, which the rule can't see through */

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { Film, Loader2, Maximize2, X } from "lucide-react";
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
  const a = edit.adjust;
  const lookCss =
    edit.filter === "none"
      ? undefined
      : `brightness(${1 + a.brightness}) contrast(${a.contrast}) saturate(${a.saturation})`;
  const stickers = edit.muted.stickers ? [] : edit.overlays.filter((o) => now >= o.start && (o.end === null || now < o.end));
  const at = edit.clock(now) ?? 0;
  const fade = Math.min(
    edit.fadeIn > 0 ? at / edit.fadeIn : 1,
    edit.fadeOut > 0 ? (edit.length - at) / edit.fadeOut : 1,
    1,
  );
  const m = edit.mask;
  const inset = m ? `${m.inset * 50}%` : "0%";
  const maskPath = !m
    ? undefined
    : m.shape === "circle"
      ? `circle(${(1 - m.inset) * 50}cqmin at 50% 50%)`
      : m.shape === "rounded"
        ? `inset(${inset} round 15%)`
        : `polygon(50% ${m.inset * 50}%, ${100 - m.inset * 50}% 50%, 50% ${100 - m.inset * 50}%, ${m.inset * 50}% 50%)`;
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
    {edit.needsServer || edit.draft.phase !== "idle" ? (
      <div className="absolute bottom-2 left-2 z-10 flex max-w-[70%] items-center gap-2 rounded-md bg-black/70 px-2.5 py-1.5 text-[12px] text-white">
        {edit.draft.phase === "ready" ? (
          <button type="button" className="inline-flex items-center gap-1.5 font-medium hover:underline" onClick={() => edit.setShowDraft(true)}>
            <Film size={13} /> Play the full preview
          </button>
        ) : edit.draft.phase === "error" ? (
          <>
            <span className="line-clamp-2" title={edit.draft.note}>Preview failed: {edit.draft.note}</span>
            <button type="button" className="underline" onClick={() => void edit.renderDraft()}>
              retry
            </button>
          </>
        ) : (
          <>
            <Loader2 size={13} className="animate-spin" /> Making the full preview…
          </>
        )}
      </div>
    ) : (
      <button
        type="button"
        onClick={() => void edit.renderDraft()}
        className="absolute bottom-2 left-2 z-10 rounded-md bg-black/60 px-2.5 py-1.5 text-[12px] text-white opacity-0 transition-opacity hover:bg-black/80 focus:opacity-100 group-hover/player:opacity-100"
      >
        Check the whole edit
      </button>
    )}
    {edit.showDraft ? (
      <div className="absolute inset-0 z-20 flex flex-col bg-black">
        <div className="flex items-center justify-between px-3 py-1.5 text-[12px] text-white/80">
          <span>Full preview: the real render with every effect (a little lower quality). {edit.draft.note}</span>
          <button type="button" aria-label="Close the full preview" className="rounded p-1 hover:bg-white/15" onClick={() => edit.setShowDraft(false)}>
            <X size={15} />
          </button>
        </div>
        <video key={edit.draft.handle} src={contentUrl(edit.draft.handle)} controls autoPlay playsInline className="min-h-0 flex-1 bg-black object-contain" />
      </div>
    ) : null}
    <div ref={stage} className="flex min-h-0 min-w-0 flex-1 items-center justify-center">
      <div
        className="relative overflow-hidden rounded-md shadow-[0_0_0_1px_rgba(255,255,255,0.06)]"
        style={{ width: fit[0], height: fit[1], containerType: "size", background: m?.background === "color" ? m.color : undefined }}
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
          style={{
            objectPosition: `${focus[0] * 100}% ${focus[1] * 100}%`,
            filter: lookCss,
            opacity: Math.max(0, Math.min(1, fade)),
            clipPath: maskPath,
          }}
        />
        {stickers.map((o) => {
          const spot = (an: string): [number, number] => {
            const [v, h] = an.split("_") as [string, string | undefined];
            const row = an === "center" ? "center" : v;
            const col = an === "center" ? "center" : (h ?? "center");
            return [col === "left" ? 0 : col === "right" ? 1 : 0.5, row === "top" ? 0 : row === "bottom" ? 1 : 0.5];
          };
          const [x0, y0] = spot(o.anchor);
          const [x1, y1] = o.moveTo && o.end !== null ? spot(o.moveTo) : [x0, y0];
          const k = o.moveTo && o.end !== null ? Math.max(0, Math.min(1, (now - o.start) / Math.max(0.01, o.end - o.start))) : 0;
          const fx = x0 + (x1 - x0) * k;
          const fy = y0 + (y1 - y0) * k;
          const fadeK =
            o.fade && o.end !== null
              ? Math.max(0, Math.min(1, (now - o.start) / o.fade, (o.end - now) / o.fade))
              : 1;
          const prog = o.end !== null ? Math.max(0, Math.min(1, (now - o.start) / Math.max(0.01, o.end - o.start))) : 0;
          const size = o.scaleTo ? o.scale + (o.scaleTo - o.scale) * prog : o.scale;
          const style: React.CSSProperties = {
            width: `${(o.blend && o.blend !== "normal" ? 1 : size) * 100}%`,
            opacity: o.opacity * fadeK,
            left: `calc(${fx * 100}% + ${(1 - 2 * fx) * 4}%)`,
            top: `calc(${fy * 100}% + ${(1 - 2 * fy) * 4}%)`,
            transform: `translate(${-fx * 100}%, ${-fy * 100}%)${o.spin ? ` rotate(${o.spin * prog}deg)` : ""}`,
            borderRadius: o.shape === "circle" ? "50%" : o.shape === "rounded" ? "20%" : undefined,
            mixBlendMode: o.blend && o.blend !== "normal" ? (o.blend === "addition" ? "plus-lighter" : o.blend) : undefined,
            ...(o.blend && o.blend !== "normal" ? { left: 0, top: 0, transform: "none", height: "100%", objectFit: "cover" as const } : {}),
          };
          return o.video ? (
            <video key={o.id} src={contentUrl(o.handle)} muted loop autoPlay playsInline className="pointer-events-none absolute" style={style} />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin URL
            <img key={o.id} src={contentUrl(o.handle)} alt="" className="pointer-events-none absolute" style={style} />
          );
        })}
        {shown.map((t) => (
          <p
            key={t.id}
            className={clsx(
              "pointer-events-none absolute inset-x-[8%] text-center font-bold leading-tight",
              !t.color && (t.style === "bold_pop" ? "text-[#ffe14d]" : "text-white"),
            )}
            style={{
              color: t.color,
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
