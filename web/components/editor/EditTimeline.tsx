"use client";

/* eslint-disable react-hooks/refs -- `edit` is one object from useEdit; its functions read refs only in event handlers, which the rule can't see through */

import { useRef } from "react";
import clsx from "clsx";
import {
  ChevronsLeft,
  ChevronsRight,
  Pause,
  Play,
  Scissors,
  Trash2,
  Undo2,
} from "lucide-react";
import { fmt, type Edit } from "./useEdit";

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
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1.5 text-[12px] text-fg-secondary transition-colors hover:bg-bg-inset hover:text-fg disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-transparent disabled:hover:text-fg-secondary"
    >
      {children}
      <span className="hidden whitespace-nowrap @[760px]:inline">{label}</span>
    </button>
  );
}

/** The toolbar (play, time, split, delete, start, end, undo) and the tracks under it. */
export function EditTimeline({ edit, locked }: { edit: Edit; locked: boolean }) {
  const { duration, now, kept, parts, texts, voice, music, sounds } = edit;
  const pct = (t: number) => (duration ? `${(t / duration) * 100}%` : "0%");
  const dragging = useRef(false);
  const voiceLen = voice.seconds || Math.min(4, duration * 0.2);

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
  const handle =
    "absolute inset-y-0 z-[5] w-2 cursor-ew-resize bg-white/0 transition-colors hover:bg-white/50";

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

  const tracks: { key: string; label: string; show: boolean }[] = [
    { key: "video", label: "Video", show: true },
    { key: "text", label: "Text", show: true },
    { key: "voice", label: "Voice", show: true },
    { key: "music", label: "Music", show: true },
  ];

  return (
    <div data-tour="timeline" className="@container shrink-0 border-t border-border bg-bg">
      <div className="flex items-center gap-1 border-b border-border px-3 py-1.5">
        <button
          type="button"
          onClick={edit.toggle}
          disabled={!duration}
          aria-label={edit.playing ? "Pause" : "Play"}
          className="mr-1 grid h-8 w-8 place-items-center rounded-full bg-fg text-bg transition-transform hover:scale-105 disabled:opacity-40"
        >
          {edit.playing ? <Pause size={15} /> : <Play size={15} className="translate-x-px" />}
        </button>
        <span className="mr-3 whitespace-nowrap font-mono text-[12px] tabular-nums text-fg">
          {fmt(now)} <span className="text-fg-tertiary">/ {fmt(duration)}</span>
        </span>
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
        <Tool label="Undo cuts" onClick={() => edit.setParts([])} disabled={locked || !parts.length}>
          <Undo2 size={14} />
        </Tool>
        <span className="ml-auto hidden whitespace-nowrap text-[12px] text-fg-tertiary @[560px]:inline">New length {fmt(edit.length)}</span>
      </div>

      <div className="flex">
        <div className="w-16 shrink-0 space-y-1.5 py-2 pl-3 pr-1">
          {tracks.map((t) => (
            <div key={t.key} className="flex h-8 items-center text-[11px] text-fg-tertiary">
              {t.label}
            </div>
          ))}
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
            "relative mr-3 flex-1 touch-none select-none space-y-1.5 py-2",
            locked ? "cursor-not-allowed opacity-60" : "cursor-pointer",
          )}
        >
          {/* video: the kept parts, with the cut gaps between them */}
          <div className="relative h-8 rounded bg-[var(--ws-track)]">
            {kept.map((p, i) => (
              <div
                key={i}
                className="absolute inset-y-0 overflow-hidden rounded bg-[var(--ws-video)] px-1.5 text-[11px] leading-8 text-white/90 ring-1 ring-inset ring-white/15"
                style={{ left: pct(p.start), width: pct(p.end - p.start) }}
              >
                {i === 0 ? "Video" : ""}
                <span
                  title="Drag to trim"
                  className={`${handle} left-0`}
                  onPointerDown={(e) => drag(e, (at) => (edit.trimEdge(i, "start", at), edit.seekTo(at)))}
                />
                <span
                  title="Drag to trim"
                  className={`${handle} right-0`}
                  onPointerDown={(e) => drag(e, (at) => (edit.trimEdge(i, "end", at), edit.seekTo(at)))}
                />
              </div>
            ))}
          </div>
          {/* text */}
          <div className="relative h-8 rounded bg-[var(--ws-track)]">
            {texts.map((t) => (
              <div
                key={t.id}
                title={t.text}
                className="absolute inset-y-1 overflow-hidden whitespace-nowrap rounded bg-[var(--ws-text)] px-1.5 text-[11px] leading-6 text-black/80"
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
                  className={`${handle} left-0`}
                  onPointerDown={(e) => drag(e, (at) => edit.patchText(t.id, { start: Math.min(at, t.end - 0.3) }))}
                />
                <span
                  className={`${handle} right-0`}
                  onPointerDown={(e) => drag(e, (at) => edit.patchText(t.id, { end: Math.max(at, t.start + 0.3) }))}
                />
              </div>
            ))}
            {sounds.map((s) => (
              <div
                key={s.id}
                title={s.kind}
                className="absolute bottom-1 h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-[var(--ws-sfx)] ring-2 ring-[var(--ws-track)]"
                style={{ left: pct(s.at) }}
              />
            ))}
          </div>
          {/* voiceover */}
          <div className="relative h-8 rounded bg-[var(--ws-track)]">
            {voice.on && (voice.handle || voice.text) ? (
              <div
                className="absolute inset-y-1 overflow-hidden whitespace-nowrap rounded bg-[var(--ws-voice)] px-1.5 text-[11px] leading-6 text-white/90"
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
          <div className="relative h-8 rounded bg-[var(--ws-track)]">
            {music.on && (music.handle || music.compose) ? (
              <div className="absolute inset-y-1 left-0 right-0 overflow-hidden whitespace-nowrap rounded bg-[var(--ws-music)] px-1.5 text-[11px] leading-6 text-white/90">
                ♪ {music.mode === "compose" ? music.compose || "Music" : music.name || "My song"}
              </div>
            ) : null}
          </div>
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
  );
}
