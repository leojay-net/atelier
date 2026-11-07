"use client";

/* eslint-disable react-hooks/refs -- `edit` is one object from useEdit; its functions read refs only in event handlers, which the rule can't see through */

import { useRef } from "react";
import { ArrowDown, ArrowUp, AudioLines, Captions, Film, Gauge, ImagePlus, Loader2, Mic, Music, RotateCcw, Sparkles, Trash2, Type } from "lucide-react";
import type { Job } from "@/lib/types";
import { Btn, Slider, field } from "./EditUi";
import {
  CURVES,
  FILTERS,
  NEUTRAL,
  SOUNDS,
  TRANSITIONS,
  fmt,
  type Anchor,
  type Edit,
  type Position,
  type Shape,
  type TextItem,
} from "./useEdit";

const SWATCHES = ["#ffffff", "#ffe14d", "#ff5c5c", "#4cc38a", "#5aa2ff", "#c084fc", "#000000"];
const ANCHORS: Anchor[] = [
  "top_left", "top_center", "top_right",
  "center_left", "center", "center_right",
  "bottom_left", "bottom_center", "bottom_right",
];

const del = "text-fg-tertiary hover:text-status-failed";
const EMOJI = ["😀", "😂", "😍", "😎", "🔥", "❤️", "👍", "🎉", "✨", "⭐", "💯", "🎂", "🎁", "🌈", "👑", "🚀", "💥", "👏", "😱", "🤔", "🙌", "🎵", "📍", "✅"];

/** The hidden picker behind "Or use a recording" and "My song". */
export function EditFileInput({ edit }: { edit: Edit }) {
  return (
    <input
      ref={edit.fileRef}
      type="file"
      accept="audio/*,video/*,image/*"
      className="hidden"
      onChange={(e) => {
        if (e.target.files?.length) edit.upload(e.target.files);
        e.target.value = "";
      }}
    />
  );
}

export function TextPanel({ edit }: { edit: Edit }) {
  const { texts, setTexts, now } = edit;
  const patch = (id: number, p: Partial<TextItem>) =>
    setTexts(texts.map((x) => (x.id === id ? { ...x, ...p } : x)));
  const { captions, setCaptions } = edit;
  return (
    <div className="space-y-3">
      <section className="space-y-2 rounded-lg border border-border bg-bg-subtle p-2.5">
        <label className="flex items-center gap-2 font-medium">
          <input
            type="checkbox"
            checked={captions.on}
            onChange={(e) => setCaptions({ ...captions, on: e.target.checked })}
          />
          <Captions size={14} /> Auto captions
        </label>
        {captions.on ? (
          <>
            <select
              aria-label="Caption style"
              value={captions.style}
              onChange={(e) => setCaptions({ ...captions, style: e.target.value as typeof captions.style })}
              className={field}
            >
              <option value="bold_pop">Bold yellow, pops in</option>
              <option value="clean">Clean white</option>
              <option value="minimal_lower">Minimal, lower third</option>
            </select>
            <p className="text-[12px] text-fg-tertiary">
              Written from the speech in your video when you save. You can&apos;t preview them here.
            </p>
          </>
        ) : (
          <p className="text-[12px] text-fg-tertiary">Listens to the speech and burns in captions.</p>
        )}
      </section>
      <Btn onClick={edit.addText}>
        <Type size={13} /> Add text at {fmt(now)}
      </Btn>
      {!texts.length ? (
        <p className="text-fg-tertiary">Titles and captions show on the picture and the timeline.</p>
      ) : null}
      {texts.map((t) => (
        <div key={t.id} className="space-y-2 rounded-lg border border-border bg-bg-subtle p-2.5">
          <input
            aria-label="Text"
            value={t.text}
            onChange={(e) => patch(t.id, { text: e.target.value })}
            className={field}
          />
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            <span className="font-mono text-fg-tertiary">
              {fmt(t.start)}–{fmt(t.end)}
            </span>
            <button type="button" className="text-accent hover:underline" onClick={() => patch(t.id, { start: now })}>
              start here
            </button>
            <button
              type="button"
              className="text-accent hover:underline"
              onClick={() => patch(t.id, { end: Math.max(t.start + 0.3, now) })}
            >
              end here
            </button>
          </div>
          <div className="flex items-center gap-1.5" role="group" aria-label="Text colour">
            {SWATCHES.map((c) => (
              <button
                key={c}
                type="button"
                aria-label={`Colour ${c}`}
                onClick={() => patch(t.id, { color: c })}
                style={{ background: c }}
                className={`h-5 w-5 rounded-full border ${t.color === c ? "ring-2 ring-accent ring-offset-1 ring-offset-bg" : "border-border-strong"}`}
              />
            ))}
            {t.color ? (
              <button type="button" className="text-[11px] text-fg-tertiary hover:text-fg" onClick={() => patch(t.id, { color: undefined })}>
                style colour
              </button>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Where"
              value={t.position}
              onChange={(e) => patch(t.id, { position: e.target.value as Position })}
              className="rounded-md border border-border-strong bg-bg px-1.5 py-1 text-[12px]"
            >
              <option value="top">Top</option>
              <option value="upper_safe">Upper</option>
              <option value="middle">Middle</option>
              <option value="lower_safe">Lower</option>
              <option value="bottom">Bottom</option>
            </select>
            <select
              aria-label="Style"
              value={t.style}
              onChange={(e) => patch(t.id, { style: e.target.value as TextItem["style"] })}
              className="rounded-md border border-border-strong bg-bg px-1.5 py-1 text-[12px]"
            >
              <option value="bold_pop">Bold yellow</option>
              <option value="clean">Clean white</option>
            </select>
            <select
              aria-label="Animation"
              value={t.animate ?? "auto"}
              onChange={(e) => patch(t.id, { animate: e.target.value as TextItem["animate"] })}
              className="rounded-md border border-border-strong bg-bg px-1.5 py-1 text-[12px]"
            >
              <option value="auto">Animate: auto</option>
              <option value="rise">Rise</option>
              <option value="pop">Pop</option>
              <option value="none">None</option>
            </select>
            <button
              type="button"
              aria-label="Remove text"
              onClick={() => setTexts(texts.filter((x) => x.id !== t.id))}
              className={`ml-auto ${del}`}
            >
              <Trash2 size={13} />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

export function AudioPanel({ edit, onSplit }: { edit: Edit; onSplit: (job: Job) => void }) {
  const { voice, setVoice, music, setMusic, accents, mine, making, now } = edit;
  return (
    <div className="space-y-5">
      {!edit.hasSoundLayer ? (
        <section className="space-y-1.5">
          <Btn onClick={() => edit.splitLayers(onSplit)} disabled={edit.saving || edit.sending}>
            Put the picture and the sound on separate layers
          </Btn>
          <p className="text-[12px] text-fg-tertiary">
            Makes a new version where you can mute, replace or remove the sound without touching the picture.
          </p>
        </section>
      ) : null}
      <section className="space-y-1.5">
        <label className="flex items-center justify-between gap-2">
          <span className="font-medium">Beat grid</span>
          <span className="flex items-center gap-1.5 text-[12px]">
            <input
              type="number"
              min={0}
              max={240}
              className={`${field} w-16`}
              value={edit.bpm || ""}
              placeholder="BPM"
              onChange={(e) => edit.setBpm(Math.max(0, Math.min(240, Number(e.target.value) || 0)))}
            />
            BPM
          </span>
        </label>
        <Btn onClick={() => void edit.findBeats()} disabled={edit.finding}>
          {edit.finding ? <Loader2 size={13} className="animate-spin" /> : <Music size={13} />}
          {edit.finding ? "Listening…" : "Find the beat for me"}
        </Btn>
        <label className="flex items-center gap-2 text-[12px]">
          <input type="checkbox" checked={edit.snap} disabled={!edit.bpm} onChange={(e) => edit.setSnap(e.target.checked)} />
          Snap Split to the beat
        </label>
      </section>
      <section className="space-y-2.5">
        <label className="flex items-center gap-2 font-medium">
          <input
            type="checkbox"
            checked={voice.on}
            onChange={(e) => setVoice({ ...voice, on: e.target.checked })}
          />
          Voiceover
        </label>
        {voice.on ? (
          <>
            {voice.handle && !voice.ai ? (
              <div className="flex items-center justify-between rounded-md bg-bg-subtle px-2 py-1.5">
                <span className="truncate">🎙 {voice.name || "Your recording"}</span>
                <button
                  type="button"
                  className="text-[12px] text-accent hover:underline"
                  onClick={() => setVoice({ ...voice, handle: "", name: "" })}
                >
                  remove
                </button>
              </div>
            ) : (
              <>
                <textarea
                  aria-label="What the voice says"
                  placeholder="Type what the voice should say…"
                  rows={4}
                  value={voice.text}
                  onChange={(e) =>
                    setVoice({ ...voice, text: e.target.value, ...(voice.ai ? { handle: "", ai: false } : {}) })
                  }
                  className={`${field} resize-none`}
                />
                <select
                  aria-label="Voice"
                  value={voice.voice}
                  onChange={(e) =>
                    setVoice({ ...voice, voice: e.target.value, ...(voice.ai ? { handle: "", ai: false } : {}) })
                  }
                  className={field}
                >
                  <option value="">American woman (default)</option>
                  {mine ? <option value="my_voice">My voice</option> : null}
                  {accents.map((a) => (
                    <optgroup key={a.id} label={a.label}>
                      {a.voices.map((v) => (
                        <option key={v.id} value={v.id}>
                          {a.label} {v.label}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
                {voice.ai && voice.handle ? (
                  <p className="text-[12px] text-status-succeeded">
                    ✓ Voiceover ready ({voice.name}). Press play to hear it with the video.
                  </p>
                ) : (
                  <Btn onClick={() => edit.makeSound("voice")} disabled={!voice.text.trim() || making === "voice"}>
                    {making === "voice" ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                    {making === "voice" ? "Making it…" : "Make voiceover"}
                  </Btn>
                )}
                <Btn onClick={() => edit.pickFile("voice")}>
                  <Mic size={13} /> Or use a recording
                </Btn>
              </>
            )}
            <div className="flex items-center gap-2 text-[12px] text-fg-secondary">
              Starts at <span className="font-mono">{fmt(voice.start)}</span>
              <button
                type="button"
                className="text-accent hover:underline"
                onClick={() => setVoice({ ...voice, start: now })}
              >
                start here
              </button>
            </div>
            <label className="flex items-center justify-between gap-2 text-[12px]">
              <span>Voice changer</span>
              <select
                className={field}
                value={voice.effect}
                onChange={(e) => setVoice({ ...voice, effect: e.target.value as typeof voice.effect })}
              >
                <option value="">Natural</option>
                {(["deep", "high", "robot", "telephone", "echo", "whisper"] as const).map((v) => (
                  <option key={v} value={v}>
                    {v[0]!.toUpperCase() + v.slice(1)}
                  </option>
                ))}
              </select>
            </label>
            {voice.effect ? <p className="text-[12px] text-fg-tertiary">Heard in the full preview and when you save.</p> : null}
            <Slider
              label={`Voice volume ${Math.round(voice.volume * 100)}%`}
              value={voice.volume}
              min={0}
              max={3}
              step={0.05}
              onChange={(x) => setVoice({ ...voice, volume: x })}
            />
          </>
        ) : null}
      </section>

      <hr className="border-border" />

      <section className="space-y-2.5">
        <label className="flex items-center gap-2 font-medium">
          <input
            type="checkbox"
            checked={music.on}
            onChange={(e) => setMusic({ ...music, on: e.target.checked })}
          />
          Music
        </label>
        {music.on ? (
          <>
            <div className="grid grid-cols-2 gap-2">
              <Btn
                active={music.mode === "compose"}
                onClick={() =>
                  setMusic({ ...music, mode: "compose", ...(music.ai ? {} : { handle: "", name: "" }) })
                }
              >
                <Sparkles size={13} /> AI makes it
              </Btn>
              <Btn active={music.mode === "upload"} onClick={() => edit.pickFile("music")}>
                <Music size={13} /> My song
              </Btn>
            </div>
            {music.mode === "compose" ? (
              <>
                <input
                  aria-label="What music"
                  placeholder="e.g. upbeat afrobeats, calm piano, epic cinematic"
                  value={music.compose}
                  onChange={(e) =>
                    setMusic({ ...music, compose: e.target.value, ...(music.ai ? { handle: "", ai: false } : {}) })
                  }
                  className={field}
                />
                {music.ai && music.handle ? (
                  <p className="text-[12px] text-status-succeeded">
                    ✓ Music ready ({music.name}). Press play to hear it with the video.
                  </p>
                ) : (
                  <Btn onClick={() => edit.makeSound("music")} disabled={!music.compose.trim() || making === "music"}>
                    {making === "music" ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                    {making === "music" ? "Composing…" : "Make music"}
                  </Btn>
                )}
              </>
            ) : music.handle ? (
              <p className="rounded-md bg-bg-subtle px-2 py-1.5">♪ {music.name}</p>
            ) : null}
            <Slider
              label={`Music volume ${Math.round(music.volume * 100)}%`}
              value={music.volume}
              min={0}
              max={1.5}
              step={0.05}
              onChange={(x) => setMusic({ ...music, volume: x })}
            />
            <p className="text-[12px] text-fg-tertiary">It plays from the start and repeats to the end.</p>
          </>
        ) : null}
      </section>

      <hr className="border-border" />

      <section className="space-y-2.5">
        <p className="font-medium">Clean up the sound</p>
        <label className="flex items-start gap-2">
          <input type="checkbox" checked={edit.denoise} onChange={(e) => edit.setDenoise(e.target.checked)} className="mt-0.5" />
          <span>
            Reduce background noise
            <span className="block text-[12px] text-fg-tertiary">Hiss, hum and fans in the video&apos;s own sound.</span>
          </span>
        </label>
        <label className="flex items-start gap-2">
          <input type="checkbox" checked={edit.loudnorm} onChange={(e) => edit.setLoudnorm(e.target.checked)} className="mt-0.5" />
          <span>
            Even out the loudness
            <span className="block text-[12px] text-fg-tertiary">Sets the final mix to the level social apps expect.</span>
          </span>
        </label>
        <p className="text-[12px] text-fg-tertiary">Applied when you save; the preview doesn&apos;t include them.</p>
      </section>
    </div>
  );
}

export function EffectsPanel({ edit }: { edit: Edit }) {
  const { sounds, setSounds, now } = edit;
  return (
    <div className="space-y-3">
      <p className="text-fg-secondary">
        Add a sound at the playhead (<span className="font-mono">{fmt(now)}</span>):
      </p>
      <div className="grid grid-cols-2 gap-2">
        {SOUNDS.map((k) => (
          <Btn key={k} onClick={() => edit.addSound(k)}>
            <AudioLines size={13} /> {k.charAt(0).toUpperCase() + k.slice(1)}
          </Btn>
        ))}
      </div>
      <FadesAndMore edit={edit} />
      {sounds.map((s) => (
        <div key={s.id} className="flex items-center justify-between rounded-md bg-bg-subtle px-2 py-1.5">
          <span>
            {s.kind} <span className="font-mono text-fg-tertiary">at {fmt(s.at)}</span>
          </span>
          <button
            type="button"
            aria-label="Remove sound"
            onClick={() => setSounds(sounds.filter((x) => x.id !== s.id))}
            className={del}
          >
            <Trash2 size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}

const SHAPES: { key: Shape; label: string }[] = [
  { key: "original", label: "As it is" },
  { key: "portrait", label: "9:16 Reels/TikTok" },
  { key: "square", label: "1:1 Square" },
  { key: "landscape", label: "16:9 YouTube" },
];

/** CapCut keeps the selected clip's settings on the right: shape and framing, volume, speed. */
export function PropertiesPanel({ edit }: { edit: Edit }) {
  const { shape, setShape, focus, setFocus, volume, setVolume, speed, setSpeed } = edit;
  return (
    <div className="space-y-5">
      <section className="space-y-2">
        <p className="font-medium">Shape</p>
        <div className="grid grid-cols-2 gap-2">
          {SHAPES.map((s) => (
            <Btn key={s.key} active={shape === s.key} onClick={() => setShape(s.key)}>
              {s.label}
            </Btn>
          ))}
        </div>
        {shape !== "original" ? (
          <>
            <Slider
              label="Frame left ↔ right"
              value={focus[0]}
              min={0}
              max={1}
              step={0.01}
              onChange={(x) => setFocus([x, focus[1]])}
            />
            <Slider
              label="Frame up ↕ down"
              value={focus[1]}
              min={0}
              max={1}
              step={0.01}
              onChange={(y) => setFocus([focus[0], y])}
            />
          </>
        ) : null}
      </section>
      <section className="space-y-2">
        <p className="font-medium">Volume</p>
        <Slider
          label={`Video sound ${Math.round(volume * 100)}%`}
          value={volume}
          min={0}
          max={3}
          step={0.05}
          onChange={setVolume}
        />
        {volume > 1 ? <p className="text-[12px] text-fg-tertiary">You hear it louder in the preview too.</p> : null}
      </section>
      {edit.partAtNow >= 0 ? (
        <section className="space-y-2">
          <p className="font-medium">
            This clip <span className="font-normal text-fg-tertiary">(part {edit.partAtNow + 1} of {edit.kept.length})</span>
          </p>
          <div className="grid grid-cols-5 gap-2">
            {[0.5, 1, 1.5, 2, 3].map((s) => (
              <Btn key={s} active={(edit.kept[edit.partAtNow]?.speed ?? 1) === s} onClick={() => edit.setPartSpeed(edit.partAtNow, s)}>
                {s}x
              </Btn>
            ))}
          </div>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={Boolean(edit.kept[edit.partAtNow]?.reverse)}
              onChange={(e) => edit.patchPart(edit.partAtNow, { reverse: e.target.checked })}
            />
            Play it backwards
          </label>
          <label className="flex items-center justify-between gap-2">
            <span>Hold the last frame</span>
            <select
              className={field}
              value={edit.kept[edit.partAtNow]?.hold ?? 0}
              onChange={(e) => edit.patchPart(edit.partAtNow, { hold: Number(e.target.value) })}
            >
              {[0, 1, 2, 3, 5].map((h) => (
                <option key={h} value={h}>
                  {h ? `${h} seconds` : "No"}
                </option>
              ))}
            </select>
          </label>
          <div>
            <p className="mb-1.5 text-[12px] text-fg-tertiary">Speed curve (slow down and speed up inside this part)</p>
            <div className="grid grid-cols-3 gap-2">
              {CURVES.map((c) => (
                <Btn key={c.key} onClick={() => edit.applyCurve(edit.partAtNow, c.key)}>
                  {c.label}
                </Btn>
              ))}
            </div>
          </div>
          <p className="text-[12px] text-fg-tertiary">
            Move the playhead onto another part to change it. Backwards and held frames show in the saved version, not the preview.
          </p>
        </section>
      ) : null}
      <section className="space-y-2">
        <p className="font-medium">Whole-video speed</p>
        <div className="grid grid-cols-4 gap-2">
          {[0.5, 1, 1.5, 2].map((s) => (
            <Btn key={s} active={speed === s} onClick={() => setSpeed(s)}>
              <Gauge size={13} /> {s}x
            </Btn>
          ))}
        </div>
      </section>
      <MoreClips edit={edit} />
    </div>
  );
}

/** Other videos played after this one. */
function MoreClips({ edit }: { edit: Edit }) {
  const { clips, setClips } = edit;
  const patch = (id: number, p: Partial<(typeof clips)[number]>) =>
    setClips(clips.map((c) => (c.id === id ? { ...c, ...p } : c)));
  return (
    <section className="space-y-2">
      <p className="font-medium">More clips after this one</p>
      <Btn onClick={() => edit.pickFile("clip")}>
        <Film size={13} /> Add a video
      </Btn>
      {clips.map((c) => (
        <div key={c.id} className="space-y-1.5 rounded-lg border border-border bg-bg-subtle p-2">
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate">{c.name}</span>
            <button type="button" aria-label="Move clip earlier" disabled={clips[0]?.id === c.id} onClick={() => edit.moveClip(clips.findIndex((x) => x.id === c.id), -1)} className={`${del} disabled:opacity-30`}>
              <ArrowUp size={13} />
            </button>
            <button type="button" aria-label="Move clip later" disabled={clips[clips.length - 1]?.id === c.id} onClick={() => edit.moveClip(clips.findIndex((x) => x.id === c.id), 1)} className={`${del} disabled:opacity-30`}>
              <ArrowDown size={13} />
            </button>
            <button type="button" aria-label="Remove clip" onClick={() => setClips(clips.filter((x) => x.id !== c.id))} className={del}>
              <Trash2 size={13} />
            </button>
          </div>
          <div className="flex items-center gap-2 text-[12px]">
            <label className="flex items-center gap-1">
              from
              <input
                type="number"
                min={0}
                step={0.1}
                className={`${field} w-16`}
                value={c.start}
                onChange={(e) => patch(c.id, { start: Math.max(0, Number(e.target.value) || 0) })}
              />
            </label>
            <label className="flex items-center gap-1">
              to
              <input
                type="number"
                min={0}
                step={0.1}
                className={`${field} w-16`}
                value={c.end ?? c.seconds ?? 0}
                onChange={(e) => patch(c.id, { end: Math.max(c.start + 0.3, Number(e.target.value) || 0) })}
              />
            </label>
            <span className="text-fg-tertiary">s</span>
          </div>
        </div>
      ))}
      {clips.length > 0 ? (
        <label className="flex items-center justify-between gap-2 text-[12px]">
          This video plays
          <select aria-label="Where this video plays" className={field} value={Math.min(edit.mainAt, clips.length)} onChange={(e) => edit.setMainAt(Number(e.target.value))}>
            <option value={0}>first</option>
            {clips.map((c, i) => (
              <option key={c.id} value={i + 1}>
                {i + 1 === clips.length ? "last" : `after ${c.name}`}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <p className="text-[12px] text-fg-tertiary">Clips are joined in this order in the full preview and the saved version. Texts and stickers follow the joined video&apos;s time.</p>
    </section>
  );
}

/** Fades, transitions between cuts, and steadying shaky footage. */
function FadesAndMore({ edit }: { edit: Edit }) {
  const { fadeIn, setFadeIn, fadeOut, setFadeOut, transition, setTransition, kept } = edit;
  return (
    <div className="space-y-3 border-t border-border pt-3">
      <p className="font-medium">Fades</p>
      <Slider label={`Fade in from black ${fadeIn.toFixed(1)} s`} value={fadeIn} min={0} max={3} step={0.1} onChange={setFadeIn} />
      <Slider label={`Fade out to black ${fadeOut.toFixed(1)} s`} value={fadeOut} min={0} max={3} step={0.1} onChange={setFadeOut} />
      <p className="pt-1 font-medium">Transition between cuts</p>
      <select
        aria-label="Transition"
        value={transition ?? ""}
        onChange={(e) => setTransition((e.target.value || null) as typeof transition)}
        className={field}
      >
        <option value="">None (a straight cut)</option>
        {TRANSITIONS.map((t) => (
          <option key={t} value={t}>
            {t.replace(/([a-z])(left|right)/, "$1 $2").replace(/^./, (c) => c.toUpperCase())}
          </option>
        ))}
      </select>
      {transition && kept.length < 2 ? (
        <p className="text-[12px] text-fg-tertiary">Split the video first: a transition sits between two parts.</p>
      ) : null}
      <p className="pt-1 font-medium">Background</p>
      <select
        aria-label="Background"
        className={field}
        value={edit.background ? edit.background.mode : ""}
        onChange={(e) =>
          edit.setBackground(
            e.target.value
              ? { mode: e.target.value as "blur" | "color", color: edit.background?.color ?? "#00b140", method: edit.background?.method ?? "model" }
              : null,
          )
        }
      >
        <option value="">Keep the background</option>
        <option value="blur">Blur the background</option>
        <option value="color">Replace it with a colour</option>
      </select>
      {edit.background?.mode === "color" ? (
        <label className="flex items-center gap-2 text-[12px]">
          Colour
          <input
            type="color"
            value={edit.background.color}
            onChange={(e) => edit.setBackground({ mode: "color", color: e.target.value, method: edit.background?.method ?? "model" })}
            className="h-7 w-10 rounded border border-border bg-transparent"
          />
        </label>
      ) : null}
      {edit.background ? (
        <>
          <label className="flex items-start gap-2 text-[12px]">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={edit.background.method === "static"}
              onChange={(e) => edit.setBackground({ ...edit.background!, method: e.target.checked ? "static" : "model" })}
            />
            <span>
              The camera does not move
              <span className="block text-fg-tertiary">Faster and sharper: what stays the same all the time is the background.</span>
            </span>
          </label>
          <p className="text-[12px] text-fg-tertiary">
            Finds the subject in every frame. Up to {edit.background.method === "static" ? "2 minutes" : "30 seconds"}; shown in the full preview.
          </p>
        </>
      ) : null}
      <p className="pt-1 font-medium">Shape of the picture</p>
      <select
        aria-label="Picture shape mask"
        className={field}
        value={edit.mask?.shape ?? ""}
        onChange={(e) =>
          edit.setMask(
            e.target.value
              ? {
                  shape: e.target.value as "circle" | "rounded" | "diamond" | "polygon",
                  points: e.target.value === "polygon" ? (edit.mask?.points ?? []) : undefined,
                  inset: edit.mask?.inset ?? 0,
                  feather: edit.mask?.feather ?? 0.03,
                  background: edit.mask?.background ?? "color",
                  color: edit.mask?.color ?? "#000000",
                }
              : null,
          )
        }
      >
        <option value="">The whole picture</option>
        <option value="circle">Circle</option>
        <option value="rounded">Rounded box</option>
        <option value="diamond">Diamond</option>
        <option value="polygon">Draw my own</option>
      </select>
      {edit.mask?.shape === "polygon" ? <DrawPad edit={edit} /> : null}
      {edit.mask ? (
        <div className="space-y-2">
          {edit.mask.shape !== "polygon" ? <Slider label={`Smaller ${Math.round(edit.mask.inset * 100)}%`} value={edit.mask.inset} min={0} max={0.6} step={0.02} onChange={(v) => edit.setMask({ ...edit.mask!, inset: v })} /> : null}
          <Slider label={`Soft edge ${Math.round(edit.mask.feather * 100)}%`} value={edit.mask.feather} min={0} max={0.3} step={0.01} onChange={(v) => edit.setMask({ ...edit.mask!, feather: v })} />
          <label className="flex items-center justify-between gap-2 text-[12px]">
            Outside the shape
            <select className={field} value={edit.mask.background} onChange={(e) => edit.setMask({ ...edit.mask!, background: e.target.value as "color" | "blur" })}>
              <option value="color">A colour</option>
              <option value="blur">The picture, blurred</option>
            </select>
          </label>
          {edit.mask.background === "color" ? (
            <input type="color" value={edit.mask.color} onChange={(e) => edit.setMask({ ...edit.mask!, color: e.target.value })} className="h-7 w-10 rounded border border-border bg-transparent" />
          ) : null}
        </div>
      ) : null}
      <ZoomSection edit={edit} />
      <p className="pt-1 font-medium">Green screen</p>
      {edit.green ? (
        <div className="space-y-2 rounded-lg border border-border bg-bg-subtle p-2">
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate">New background: {edit.green.name}</span>
            <button type="button" aria-label="Remove green screen" className={del} onClick={() => edit.setGreen(null)}>
              <Trash2 size={13} />
            </button>
          </div>
          <label className="flex items-center gap-2 text-[12px]">
            Screen colour
            <input
              type="color"
              value={edit.green.color}
              onChange={(e) => edit.setGreen({ ...edit.green!, color: e.target.value })}
              className="h-7 w-10 rounded border border-border bg-transparent"
            />
          </label>
          <Slider
            label={`How much to remove ${Math.round(edit.green.similarity * 100)}%`}
            value={edit.green.similarity}
            min={0.05}
            max={0.8}
            step={0.05}
            onChange={(v) => edit.setGreen({ ...edit.green!, similarity: v })}
          />
        </div>
      ) : (
        <Btn onClick={() => edit.pickFile("green")}>
          <ImagePlus size={13} /> Swap a green screen for a picture
        </Btn>
      )}
      <p className="pt-1 font-medium">Footage</p>
      <label className="flex items-start gap-2">
        <input type="checkbox" checked={edit.stabilize} onChange={(e) => edit.setStabilize(e.target.checked)} className="mt-0.5" />
        <span>
          Steady shaky footage
          <span className="block text-[12px] text-fg-tertiary">Smooths handheld wobble (crops in a little). Applied when you save.</span>
        </span>
      </label>
    </div>
  );
}

/** CapCut-style filters (named looks) and the four sliders behind them. */
export function FiltersPanel({ edit }: { edit: Edit }) {
  const { adjust, setAdjust, filter, setFilter } = edit;
  const set = (patch: Partial<typeof adjust>) => {
    setAdjust({ ...adjust, ...patch });
    setFilter("custom");
  };
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => {
              setAdjust(f.adjust);
              setFilter(f.key);
            }}
            className={`group overflow-hidden rounded-md border text-[11.5px] transition-colors ${
              filter === f.key ? "border-accent text-fg" : "border-border text-fg-secondary hover:border-border-strong"
            }`}
          >
            <span
              className="block h-12 w-full"
              style={{
                background: "linear-gradient(135deg,#e2574c,#f2c14e 35%,#3fa796 65%,#4a6fd6)",
                filter: `brightness(${1 + f.adjust.brightness}) contrast(${f.adjust.contrast}) saturate(${f.adjust.saturation})`,
              }}
            />
            <span className="block truncate px-1 py-1">{f.label}</span>
          </button>
        ))}
      </div>
      <Slider label={`Brightness ${adjust.brightness.toFixed(2)}`} value={adjust.brightness} min={-0.5} max={0.5} step={0.01} onChange={(v) => set({ brightness: v })} />
      <Slider label={`Contrast ${adjust.contrast.toFixed(2)}`} value={adjust.contrast} min={0.5} max={2} step={0.01} onChange={(v) => set({ contrast: v })} />
      <Slider label={`Saturation ${adjust.saturation.toFixed(2)}`} value={adjust.saturation} min={0} max={2} step={0.01} onChange={(v) => set({ saturation: v })} />
      <Slider label={`Midtones ${adjust.gamma.toFixed(2)}`} value={adjust.gamma} min={0.5} max={1.8} step={0.01} onChange={(v) => set({ gamma: v })} />
      <AnimatedLook edit={edit} />
      <Btn
        onClick={() => {
          setAdjust(NEUTRAL);
          setFilter("none");
        }}
        disabled={filter === "none"}
      >
        <RotateCcw size={13} /> Reset the look
      </Btn>
    </div>
  );
}

/** Pictures laid over the video: stickers, logos, a photo in the corner. */
export function StickersPanel({ edit }: { edit: Edit }) {
  const { overlays, setOverlays, now } = edit;
  const patch = (id: number, p: Partial<(typeof overlays)[number]>) =>
    setOverlays(overlays.map((o) => (o.id === id ? { ...o, ...p } : o)));
  return (
    <div className="space-y-3">
      <Btn onClick={() => edit.pickFile("sticker")}>
        <ImagePlus size={13} /> Add a picture or video
      </Btn>
      <div className="grid grid-cols-8 gap-1" role="group" aria-label="Emoji stickers">
        {EMOJI.map((m) => (
          <button
            key={m}
            type="button"
            aria-label={`Add ${m}`}
            onClick={() => edit.addEmoji(m)}
            className="grid h-8 place-items-center rounded-md text-[18px] hover:bg-bg-inset"
          >
            {m}
          </button>
        ))}
      </div>
      {!overlays.length ? (
        <p className="text-fg-tertiary">A logo, a sticker or a photo in the corner (PNG with a clear background works best).</p>
      ) : null}
      {overlays.map((o) => (
        <div key={o.id} className="space-y-2 rounded-lg border border-border bg-bg-subtle p-2.5">
          <div className="flex items-center gap-2">
            {o.video ? (
              <video src={`/api/proxy/assets/${encodeURIComponent(o.handle)}/content`} muted className="h-9 w-9 rounded bg-bg-inset object-cover" />
            ) : (
              // eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin URL
              <img src={`/api/proxy/assets/${encodeURIComponent(o.handle)}/content`} alt="" className="h-9 w-9 rounded bg-bg-inset object-contain" />
            )}
            <span className="min-w-0 flex-1 truncate">{o.name || "Picture"}</span>
            {!o.video ? (
              <button
                type="button"
                disabled={edit.cutting !== null}
                onClick={() => void edit.cutOut(o.id)}
                className="inline-flex items-center gap-1 text-[12px] text-accent hover:underline disabled:opacity-50"
              >
                {edit.cutting === o.id ? <Loader2 size={12} className="animate-spin" /> : null}
                {edit.cutting === o.id ? "Working…" : "Remove background"}
              </button>
            ) : null}
            <button type="button" aria-label="Remove picture" onClick={() => setOverlays(overlays.filter((x) => x.id !== o.id))} className={del}>
              <Trash2 size={13} />
            </button>
          </div>
          <div className="grid w-24 grid-cols-3 gap-1" role="group" aria-label="Position">
            {ANCHORS.map((a) => (
              <button
                key={a}
                type="button"
                aria-label={a.replace("_", " ")}
                onClick={() => patch(o.id, { anchor: a })}
                className={`h-6 rounded-sm ${o.anchor === a ? "bg-accent" : "bg-bg-inset hover:bg-border-strong"}`}
              />
            ))}
          </div>
          <Slider label={`Size ${Math.round(o.scale * 100)}%`} value={o.scale} min={0.05} max={1} step={0.01} onChange={(v) => patch(o.id, { scale: v })} />
          <Slider label={`Opacity ${Math.round(o.opacity * 100)}%`} value={o.opacity} min={0.1} max={1} step={0.05} onChange={(v) => patch(o.id, { opacity: v })} />
          <div className="grid grid-cols-2 gap-2 text-[12px]">
            <label className="flex flex-col gap-1">
              Shape
              <select className={field} value={o.shape ?? "rect"} disabled={o.video} onChange={(e) => patch(o.id, { shape: e.target.value as "rect" | "circle" | "rounded" })}>
                <option value="rect">Square</option>
                <option value="rounded">Rounded</option>
                <option value="circle">Circle</option>
              </select>
            </label>
            <label className="flex flex-col gap-1">
              Mix
              <select className={field} value={o.blend ?? "normal"} disabled={o.video} onChange={(e) => patch(o.id, { blend: e.target.value as NonNullable<typeof o.blend> })}>
                <option value="normal">Normal</option>
                <option value="screen">Screen (glow)</option>
                <option value="multiply">Multiply (dark)</option>
                <option value="overlay">Overlay</option>
                <option value="addition">Add (bright)</option>
              </select>
            </label>
          </div>
          {o.blend && o.blend !== "normal" ? <p className="text-[12px] text-fg-tertiary">A mixed picture covers the whole frame.</p> : null}
          {o.video ? (
            <label className="flex items-center gap-2 text-[12px]">
              <input type="checkbox" checked={Boolean(o.sound)} onChange={(e) => patch(o.id, { sound: e.target.checked })} />
              Keep this video&apos;s sound
            </label>
          ) : (
            <Slider
              label={`Fade in and out ${(o.fade ?? 0).toFixed(1)}s`}
              value={o.fade ?? 0}
              min={0}
              max={2}
              step={0.1}
              onChange={(v) => patch(o.id, { fade: v })}
            />
          )}
          <label className="flex items-center justify-between gap-2 text-[12px]">
            <span>Glide to</span>
            <select
              className={field}
              value={o.moveTo ?? ""}
              disabled={o.end === null}
              title={o.end === null ? "Set an end time first" : undefined}
              onChange={(e) => patch(o.id, { moveTo: (e.target.value || null) as Anchor | null })}
            >
              <option value="">Stays put</option>
              {ANCHORS.map((a) => (
                <option key={a} value={a}>
                  {a.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
          {o.end !== null && !o.video ? (
            <>
              <Slider
                label={`Grows or shrinks to ${Math.round((o.scaleTo ?? o.scale) * 100)}%`}
                value={o.scaleTo ?? o.scale}
                min={0.05}
                max={1}
                step={0.01}
                onChange={(v) => patch(o.id, { scaleTo: Math.abs(v - o.scale) < 0.015 ? null : v })}
              />
              <Slider label={`Spins ${Math.round(o.spin ?? 0)}°`} value={o.spin ?? 0} min={-720} max={720} step={15} onChange={(v) => patch(o.id, { spin: v })} />
            </>
          ) : null}
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            <span className="font-mono text-fg-tertiary">
              {fmt(o.start)}–{o.end === null ? "end" : fmt(o.end)}
            </span>
            <button type="button" className="text-accent hover:underline" onClick={() => patch(o.id, { start: now })}>
              start here
            </button>
            <button type="button" className="text-accent hover:underline" onClick={() => patch(o.id, { end: Math.max(o.start + 0.3, now) })}>
              end here
            </button>
            {o.end !== null ? (
              <button type="button" className="text-fg-tertiary hover:text-fg" onClick={() => patch(o.id, { end: null })}>
                to the end
              </button>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Draw the part of the picture to keep: click to add corners, drag a corner to move it. */
function DrawPad({ edit }: { edit: Edit }) {
  const pad = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const drag = useRef<number | null>(null);
  const m = edit.mask!;
  const pts = m.points ?? [];
  const set = (points: [number, number][]) => edit.setMask({ ...m, points });
  const at = (e: React.PointerEvent): [number, number] => {
    const r = pad.current!.getBoundingClientRect();
    const f = (v: number) => Math.max(0, Math.min(1, Math.round(v * 1000) / 1000));
    return [f((e.clientX - r.left) / r.width), f((e.clientY - r.top) / r.height)];
  };
  const grabFrame = () => canvas.current && edit.drawFrame(canvas.current);
  return (
    <div className="space-y-2">
      <div
        ref={pad}
        data-testid="draw-pad"
        className="relative w-full cursor-crosshair touch-none overflow-hidden rounded-md border border-border bg-black"
        style={{ aspectRatio: `${edit.natural[0]} / ${edit.natural[1]}` }}
        onPointerDown={(e) => {
          if (drag.current !== null || pts.length >= 64) return;
          set([...pts, at(e)]);
        }}
        onPointerMove={(e) => {
          if (drag.current === null) return;
          const next = [...pts];
          next[drag.current] = at(e);
          set(next);
        }}
        onPointerUp={() => (drag.current = null)}
      >
        <canvas ref={canvas} className="absolute inset-0 h-full w-full" />
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full">
          {pts.length >= 3 ? <polygon points={pts.map(([x, y]) => `${x * 100},${y * 100}`).join(" ")} fill="rgba(90,162,255,0.28)" stroke="#5aa2ff" strokeWidth="0.6" vectorEffect="non-scaling-stroke" /> : null}
        </svg>
        {pts.map(([x, y], i) => (
          <span
            key={i}
            data-testid="draw-point"
            className="absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 cursor-grab rounded-full border-2 border-white bg-accent"
            style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
            onPointerDown={(e) => {
              e.stopPropagation();
              drag.current = i;
              pad.current?.setPointerCapture(e.pointerId);
            }}
          />
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Btn onClick={grabFrame}>Show this frame</Btn>
        <Btn onClick={() => set(pts.slice(0, -1))} disabled={pts.length === 0}>Undo a point</Btn>
        <Btn onClick={() => set([])} disabled={pts.length === 0}>Clear</Btn>
      </div>
      <p className="text-[12px] text-fg-tertiary">
        {pts.length < 3 ? `Click ${3 - pts.length} more point${pts.length === 2 ? "" : "s"} to outline what stays.` : "Drag a point to adjust. Everything outside the outline is hidden."}
      </p>
    </div>
  );
}

/** A zoom through keyframes: where on the timeline the picture is how close. */
function ZoomSection({ edit }: { edit: Edit }) {
  const z = edit.zoom;
  const len = Math.max(1, edit.length);
  const keys = z?.keys ?? [];
  const put = (next: [number, number][]) =>
    edit.setZoom(next.length >= 2 ? { keys: [...next].sort((a, b) => a[0] - b[0]), focus_x: z?.focus_x ?? 0.5, focus_y: z?.focus_y ?? 0.5 } : null);
  const preset = (zooms: number[]) =>
    edit.setZoom({ keys: zooms.map((v, i) => [Math.round(((len * i) / (zooms.length - 1)) * 10) / 10, v] as [number, number]), focus_x: z?.focus_x ?? 0.5, focus_y: z?.focus_y ?? 0.5 });
  return (
    <section className="space-y-2">
      <p className="pt-1 font-medium">Zoom</p>
      <div className="flex flex-wrap gap-2">
        <Btn onClick={() => preset([1, 1.4])}>Push in</Btn>
        <Btn onClick={() => preset([1.4, 1])}>Pull out</Btn>
        <Btn onClick={() => preset([1, 1.35, 1])}>Pulse</Btn>
        <Btn onClick={() => edit.setZoom(null)} disabled={!z}>No zoom</Btn>
      </div>
      {z ? (
        <div className="space-y-2">
          {keys.map(([t, v], i) => (
            <div key={i} className="flex items-center gap-2 text-[12px]" data-testid="zoom-key">
              <input aria-label="Keyframe time" type="number" min={0} step={0.1} className={`${field} w-16`} value={t}
                onChange={(e) => put(keys.map((k, j) => (j === i ? [Math.max(0, Number(e.target.value) || 0), k[1]] : k)))} />
              <span className="text-fg-tertiary">s</span>
              <input aria-label="Keyframe zoom" type="range" min={1} max={3} step={0.05} value={v} className="min-w-0 flex-1"
                onChange={(e) => put(keys.map((k, j) => (j === i ? [k[0], Number(e.target.value)] : k)))} />
              <span className="w-10 text-right tabular-nums">{v.toFixed(2)}×</span>
              <button type="button" aria-label="Remove keyframe" disabled={keys.length <= 2} className={`${del} disabled:opacity-30`} onClick={() => put(keys.filter((_, j) => j !== i))}>
                <Trash2 size={13} />
              </button>
            </div>
          ))}
          <Btn onClick={() => put([...keys, [Math.round((edit.clock(edit.now) ?? 0) * 10) / 10, 1.2]])}>Add a keyframe at the playhead</Btn>
          <Slider label={`Zooms towards: across ${Math.round(z.focus_x * 100)}%`} value={z.focus_x} min={0} max={1} step={0.01} onChange={(v) => edit.setZoom({ ...z, focus_x: v })} />
          <Slider label={`down ${Math.round(z.focus_y * 100)}%`} value={z.focus_y} min={0} max={1} step={0.01} onChange={(v) => edit.setZoom({ ...z, focus_y: v })} />
        </div>
      ) : null}
    </section>
  );
}

/** The look changes over time: it starts as set above and ends as set here. */
function AnimatedLook({ edit }: { edit: Edit }) {
  const { adjust, setAdjust, setFilter } = edit;
  const on = adjust.end !== undefined;
  const len = Math.max(1, Math.round(edit.length * 10) / 10);
  const to = (k: "brightness" | "contrast" | "saturation" | "gamma") => adjust[`to_${k}`] ?? adjust[k];
  const patch = (p: Partial<typeof adjust>) => {
    setAdjust({ ...adjust, ...p });
    setFilter("custom");
  };
  return (
    <section className="space-y-2 border-t border-border pt-3">
      <label className="flex items-center gap-2 font-medium">
        <input
          type="checkbox"
          checked={on}
          onChange={(e) =>
            e.target.checked
              ? patch({ start: 0, end: len, to_brightness: adjust.brightness, to_contrast: adjust.contrast, to_saturation: adjust.saturation, to_gamma: adjust.gamma })
              : setAdjust({ brightness: adjust.brightness, contrast: adjust.contrast, saturation: adjust.saturation, gamma: adjust.gamma })
          }
        />
        Change the look over time
      </label>
      {on ? (
        <div className="space-y-2">
          <p className="text-[12px] text-fg-tertiary">The sliders above are how it starts; these are how it ends.</p>
          <Slider label={`Ends: brightness ${to("brightness").toFixed(2)}`} value={to("brightness")} min={-0.5} max={0.5} step={0.01} onChange={(v) => patch({ to_brightness: v })} />
          <Slider label={`Ends: contrast ${to("contrast").toFixed(2)}`} value={to("contrast")} min={0.5} max={2} step={0.01} onChange={(v) => patch({ to_contrast: v })} />
          <Slider label={`Ends: saturation ${to("saturation").toFixed(2)}`} value={to("saturation")} min={0} max={2} step={0.01} onChange={(v) => patch({ to_saturation: v })} />
          <Slider label={`Starts changing at ${(adjust.start ?? 0).toFixed(1)} s`} value={adjust.start ?? 0} min={0} max={len} step={0.1} onChange={(v) => patch({ start: Math.min(v, (adjust.end ?? len) - 0.1) })} />
          <Slider label={`Finished by ${(adjust.end ?? len).toFixed(1)} s`} value={adjust.end ?? len} min={0.1} max={len} step={0.1} onChange={(v) => patch({ end: Math.max(v, (adjust.start ?? 0) + 0.1) })} />
        </div>
      ) : null}
    </section>
  );
}
