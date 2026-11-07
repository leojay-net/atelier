"use client";

/* eslint-disable react-hooks/refs -- `edit` is one object from useEdit; its functions read refs only in event handlers, which the rule can't see through */

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import {
  ChevronsLeft,
  ChevronsRight,
  Eye,
  EyeOff,
  Pause,
  Play,
  Plus,
  Redo2,
  Scissors,
  Trash2,
  Undo2,
  Volume2,
  VolumeX,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { fmt, type Edit, type Layer } from "./useEdit";

function Tool({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1.5 text-[12px] text-fg-secondary transition-colors hover:bg-bg-inset hover:text-fg disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-transparent disabled:hover:text-fg-secondary"
    >
      {children}
      <span className="hidden whitespace-nowrap @[900px]:inline">{label}</span>
    </button>
  );
}

const HANDLE = "absolute inset-y-0 z-[5] w-2 cursor-ew-resize bg-white/0 transition-colors hover:bg-white/50";
const ROW = "relative h-7 rounded-md bg-[var(--ws-track)]";
const ZOOMS = [1, 2, 4, 8];

/** The toolbar (play, time, split, delete, start, end, undo and redo, zoom) and one row of
 * tracks per layer, each of which can be muted, hidden or removed on its own. */
export function EditTimeline({ edit, locked }: { edit: Edit; locked: boolean }) {
  const { duration, now, kept, parts, texts, voice, music, sounds, overlays, captions, clips, bpm } = edit;
  const beats =
    bpm > 0 && duration
      ? Array.from({ length: Math.min(400, Math.floor((duration - edit.beatOffset) / (60 / bpm)) + 1) }, (_, i) => edit.beatOffset + i * (60 / bpm))
      : [];
  const pct = (t: number) => (duration ? `${(t / duration) * 100}%` : "0%");
  const dragging = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const voiceLen = voice.seconds || Math.min(4, duration * 0.2);

  // keep the playhead in view when zoomed in
  useEffect(() => {
    const box = scroller.current;
    if (!box || zoom === 1 || !duration) return;
    const x = (now / duration) * (box.scrollWidth - 104) + 104;
    if (x < box.scrollLeft + 120 || x > box.scrollLeft + box.clientWidth - 40)
      box.scrollLeft = Math.max(0, x - box.clientWidth / 2);
  }, [now, zoom, duration]);

  /** Start dragging something on the timeline; `onMove` gets the time under the pointer and how
   * far it has moved from where it was grabbed. */
  function drag(e: React.PointerEvent, onMove: (t: number, dt: number) => void) {
    if (locked) return;
    e.stopPropagation();
    e.preventDefault();
    const t0 = edit.timeAt(e.clientX);
    const move = (m: PointerEvent) => onMove(edit.timeAt(m.clientX), edit.timeAt(m.clientX) - t0);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // click or drag anywhere on the tracks to move the playhead
  function down(e: React.PointerEvent) {
    if (locked) return;
    dragging.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    edit.seek(e.clientX);
  }
  function move(e: React.PointerEvent) {
    if (dragging.current) edit.seek(e.clientX);
  }
  function up() {
    dragging.current = false;
  }

  // one row per layer; each can be muted (or hidden) and taken out on its own
  const rows: { key: Layer; label: string; has: boolean; sound: boolean; add?: () => void }[] = [
    { key: "video", label: "Video", has: true, sound: true },
    { key: "text", label: "Text", has: texts.length > 0 || captions.on, sound: false },
    { key: "stickers", label: "Stickers", has: overlays.length > 0, sound: false, add: () => edit.pickFile("sticker") },
    { key: "effects", label: "Effects", has: sounds.length > 0, sound: true },
    { key: "voice", label: "Voice", has: voice.on && Boolean(voice.handle || voice.text), sound: true, add: () => edit.pickFile("voice") },
    { key: "music", label: "Music", has: music.on && Boolean(music.handle || music.compose), sound: true, add: () => edit.pickFile("music") },
  ];
  const off = (k: Layer) => edit.muted[k];
  const dim = (k: Layer) => (off(k) ? "opacity-40" : "");
  const zoomBy = (d: number) => setZoom(ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, ZOOMS.indexOf(zoom) + d))] ?? 1);

  return (
    <div data-tour="timeline" className="@container shrink-0 border-t border-border bg-bg">
      <div className="flex items-center gap-0.5 border-b border-border px-3 py-1.5">
        <button
          type="button"
          onClick={edit.toggle}
          disabled={!duration}
          aria-label={edit.playing ? "Pause" : "Play"}
          className="mr-1 grid h-8 w-8 place-items-center rounded-full bg-fg text-bg transition-transform hover:scale-105 disabled:opacity-40"
        >
          {edit.playing ? <Pause size={15} /> : <Play size={15} className="translate-x-px" />}
        </button>
        <span className="mr-2 whitespace-nowrap font-mono text-[12px] tabular-nums text-fg">
          {fmt(now)} <span className="text-fg-tertiary">/ {fmt(duration)}</span>
        </span>
        <Tool label="Undo" onClick={edit.undo} disabled={locked || !edit.canUndo}>
          <Undo2 size={14} />
        </Tool>
        <Tool label="Redo" onClick={edit.redo} disabled={locked || !edit.canRedo}>
          <Redo2 size={14} />
        </Tool>
        <span className="mx-1 h-4 w-px bg-border" />
        <Tool label="Split" onClick={edit.split} disabled={locked}>
          <Scissors size={14} />
        </Tool>
        <Tool
          label="Delete"
          onClick={() => edit.partAtNow >= 0 && edit.removePart(edit.partAtNow)}
          disabled={locked || edit.partAtNow < 0}
        >
          <Trash2 size={14} />
        </Tool>
        <Tool label="Start here" onClick={edit.setStart} disabled={locked}>
          <ChevronsLeft size={14} />
        </Tool>
        <Tool label="End here" onClick={edit.setEnd} disabled={locked}>
          <ChevronsRight size={14} />
        </Tool>
        <span className="ml-auto hidden whitespace-nowrap text-[12px] text-fg-tertiary @[620px]:inline">
          New length {fmt(edit.length)}
        </span>
        <span className="mx-1 h-4 w-px bg-border" />
        <Tool label="Zoom out" onClick={() => zoomBy(-1)} disabled={zoom === 1}>
          <ZoomOut size={14} />
        </Tool>
        <Tool label="Zoom in" onClick={() => zoomBy(1)} disabled={zoom === ZOOMS[ZOOMS.length - 1]}>
          <ZoomIn size={14} />
        </Tool>
      </div>

      <div ref={scroller} className="max-h-[236px] overflow-auto">
        <div className="flex min-w-full" style={{ width: zoom === 1 ? "100%" : `${zoom * 100}%` }}>
          {/* layer names and their controls stay in view while the tracks scroll */}
          <div className="sticky left-0 z-20 w-[104px] shrink-0 space-y-1 bg-bg py-2 pl-3 pr-1">
            {rows.map((r) => (
              <div key={r.key} className="group/row flex h-7 items-center gap-0.5 text-[11px] text-fg-tertiary">
                <span className={clsx("w-11 truncate", off(r.key) && "line-through opacity-60")}>{r.label}</span>
                {r.has || r.key === "video" ? (
                  <>
                    <button
                      type="button"
                      disabled={locked}
                      onClick={() => edit.toggleMute(r.key)}
                      title={
                        r.key === "video"
                          ? off(r.key) ? "Turn the video's own sound back on" : "Mute the video's own sound"
                          : r.sound
                            ? off(r.key) ? "Unmute" : "Mute"
                            : off(r.key) ? "Show" : "Hide"
                      }
                      aria-label={`${off(r.key) ? "Unmute" : "Mute"} ${r.label}`}
                      aria-pressed={off(r.key)}
                      className={clsx(
                        "grid h-6 w-6 place-items-center rounded transition-colors hover:bg-bg-inset hover:text-fg disabled:opacity-35",
                        off(r.key) && "text-status-failed",
                      )}
                    >
                      {r.sound ? off(r.key) ? <VolumeX size={13} /> : <Volume2 size={13} /> : off(r.key) ? <EyeOff size={13} /> : <Eye size={13} />}
                    </button>
                    {r.key !== "video" ? (
                      <button
                        type="button"
                        disabled={locked}
                        onClick={() => edit.removeLayer(r.key)}
                        title={`Remove the ${r.label.toLowerCase()} layer`}
                        aria-label={`Remove ${r.label}`}
                        className="grid h-6 w-6 place-items-center rounded opacity-0 transition hover:bg-bg-inset hover:text-status-failed focus:opacity-100 group-hover/row:opacity-100 disabled:opacity-0"
                      >
                        <Trash2 size={12} />
                      </button>
                    ) : null}
                  </>
                ) : r.add ? (
                  <button
                    type="button"
                    disabled={locked}
                    onClick={r.add}
                    title={r.key === "voice" ? "Add your own recording" : r.key === "music" ? "Add your own song" : "Add a picture"}
                    aria-label={`Add ${r.label}`}
                    className="grid h-6 w-6 place-items-center rounded hover:bg-bg-inset hover:text-fg disabled:opacity-35"
                  >
                    <Plus size={13} />
                  </button>
                ) : null}
              </div>
            ))}
            {clips.length ? <div className="flex h-7 items-center text-[11px] text-fg-tertiary">Clips</div> : null}
          </div>

          <div
            ref={edit.barRef}
            role="slider"
            aria-label="Timeline"
            aria-valuenow={now}
            tabIndex={0}
            onPointerDown={down}
            onPointerMove={move}
            onPointerUp={up}
            className={clsx(
              "relative mr-3 min-w-0 flex-1 touch-none select-none space-y-1 py-2",
              locked ? "cursor-not-allowed opacity-60" : "cursor-pointer",
            )}
          >
            {/* video: the kept parts, with the cut gaps between them */}
            <div className={clsx(ROW, off("video") && "opacity-60")}>
              {kept.map((p, i) => (
                <div
                  key={i}
                  className={clsx(
                    "absolute inset-y-0 overflow-hidden rounded px-1.5 text-[11px] leading-7 text-white/90 ring-1 ring-inset ring-white/15",
                    i === edit.partAtNow && kept.length > 1 ? "bg-[#4b7fc6]" : "bg-[var(--ws-video)]",
                  )}
                  style={{ left: pct(p.start), width: pct(p.end - p.start) }}
                >
                  {i === 0 ? "Video" : ""}
                  {(p.speed ?? 1) !== 1 ? <span className="ml-1 rounded bg-black/30 px-1">{p.speed}x</span> : null}
                  {p.reverse ? <span className="ml-1 rounded bg-black/30 px-1">◀ back</span> : null}
                  {(p.hold ?? 0) > 0 ? <span className="ml-1 rounded bg-black/30 px-1">hold {p.hold}s</span> : null}
                  <span
                    title="Drag to trim"
                    className={`${HANDLE} left-0`}
                    onPointerDown={(e) => drag(e, (at) => (edit.trimEdge(i, "start", at), edit.seekTo(at)))}
                  />
                  <span
                    title="Drag to trim"
                    className={`${HANDLE} right-0`}
                    onPointerDown={(e) => drag(e, (at) => (edit.trimEdge(i, "end", at), edit.seekTo(at)))}
                  />
                </div>
              ))}
            </div>
            {/* text and captions */}
            <div className={clsx(ROW, dim("text"))}>
              {captions.on ? (
                <div className="absolute inset-y-3 left-0 right-0 rounded-full bg-[var(--ws-text)]/35" title="Captions are written when you save" />
              ) : null}
              {texts.map((t) => (
                <div
                  key={t.id}
                  title={t.text}
                  className="absolute inset-y-1 overflow-hidden whitespace-nowrap rounded bg-[var(--ws-text)] px-1.5 text-[11px] leading-5 text-black/80"
                  style={{ left: pct(t.start), width: pct(t.end - t.start) }}
                >
                  <span
                    className="absolute inset-0 cursor-grab active:cursor-grabbing"
                    onPointerDown={(e) => {
                      const { start, end } = t;
                      drag(e, (_at, dt) => {
                        const shift = Math.max(-start, Math.min(duration - end, dt));
                        edit.patchText(t.id, { start: start + shift, end: end + shift });
                      });
                    }}
                  />
                  <span className="relative">{t.text}</span>
                  <span
                    className={`${HANDLE} left-0`}
                    onPointerDown={(e) => drag(e, (at) => edit.patchText(t.id, { start: Math.min(at, t.end - 0.3) }))}
                  />
                  <span
                    className={`${HANDLE} right-0`}
                    onPointerDown={(e) => drag(e, (at) => edit.patchText(t.id, { end: Math.max(at, t.start + 0.3) }))}
                  />
                </div>
              ))}
            </div>
            {/* stickers and pictures */}
            <div className={clsx(ROW, dim("stickers"))}>
              {overlays.map((o) => {
                const end = o.end ?? duration;
                return (
                  <div
                    key={o.id}
                    title={o.name}
                    className="absolute inset-y-1 overflow-hidden whitespace-nowrap rounded bg-[var(--ws-sticker)] px-1.5 text-[11px] leading-5 text-white/90"
                    style={{ left: pct(o.start), width: pct(Math.max(0.1, end - o.start)) }}
                  >
                    <span
                      className="absolute inset-0 cursor-grab active:cursor-grabbing"
                      onPointerDown={(e) => {
                        const { start } = o;
                        drag(e, (_at, dt) => {
                          const len = end - start;
                          const s = Math.max(0, Math.min(duration - len, start + dt));
                          edit.setOverlays(
                            overlays.map((x) => (x.id === o.id ? { ...x, start: s, end: o.end === null ? null : s + len } : x)),
                          );
                        });
                      }}
                    />
                    <span className="relative">{o.name || "Picture"}</span>
                  </div>
                );
              })}
            </div>
            {/* sound effects */}
            <div className={clsx(ROW, dim("effects"))}>
              {sounds.map((s) => (
                <div
                  key={s.id}
                  title={s.kind}
                  className="absolute bottom-1.5 h-3 w-3 -translate-x-1/2 rounded-full bg-[var(--ws-sfx)] ring-2 ring-[var(--ws-track)]"
                  style={{ left: pct(s.at) }}
                />
              ))}
            </div>
            {/* voiceover */}
            <div className={clsx(ROW, dim("voice"))}>
              {voice.on && (voice.handle || voice.text) ? (
                <div
                  className="absolute inset-y-1 overflow-hidden whitespace-nowrap rounded bg-[var(--ws-voice)] px-1.5 text-[11px] leading-5 text-white/90"
                  style={{ left: pct(voice.start), width: pct(voiceLen) }}
                >
                  <span
                    className="absolute inset-0 cursor-grab active:cursor-grabbing"
                    onPointerDown={(e) => {
                      const s0 = voice.start;
                      drag(e, (_at, dt) =>
                        edit.setVoice((v) => ({ ...v, start: Math.max(0, Math.min(duration - 0.5, s0 + dt)) })),
                      );
                    }}
                  />
                  <span className="relative">Voiceover</span>
                </div>
              ) : null}
            </div>
            {/* music */}
            <div className={clsx(ROW, dim("music"))}>
              {music.on && (music.handle || music.compose) ? (
                <div className="absolute inset-y-1 left-0 right-0 overflow-hidden whitespace-nowrap rounded bg-[var(--ws-music)] px-1.5 text-[11px] leading-5 text-white/90">
                  {music.mode === "compose" ? music.compose || "Music" : music.name || "My song"}
                </div>
              ) : null}
            </div>
            {clips.length ? (
              <div className={clsx(ROW, "flex gap-1 bg-transparent")} title="Joined after the video when you save">
                {clips.map((c) => (
                  <div key={c.id} className="min-w-0 flex-1 truncate rounded bg-[var(--ws-video)]/70 px-1.5 text-[11px] leading-7 text-white/90">
                    + {c.name}
                  </div>
                ))}
              </div>
            ) : null}
            {beats.length ? (
              <div className="pointer-events-none absolute inset-y-0 left-0 right-0 z-[1]">
                {beats.map((t, i) => (
                  <span
                    key={i}
                    className={clsx("absolute inset-y-0 w-px", i % 4 === 0 ? "bg-white/25" : "bg-white/10")}
                    style={{ left: pct(t) }}
                  />
                ))}
              </div>
            ) : null}
            {/* playhead */}
            <div
              className="pointer-events-none absolute inset-y-0 z-10 w-px bg-[var(--ws-playhead)]"
              style={{ left: pct(now) }}
            >
              <span className="absolute -left-[5px] -top-px h-2.5 w-[11px] rounded-b-sm bg-[var(--ws-playhead)] [clip-path:polygon(0_0,100%_0,50%_100%)]" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
