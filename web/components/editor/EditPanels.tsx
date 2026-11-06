"use client";

/* eslint-disable react-hooks/refs -- `edit` is one object from useEdit; its functions read refs only in event handlers, which the rule can't see through */

import { AudioLines, Gauge, Loader2, Mic, Music, Sparkles, Trash2, Type } from "lucide-react";
import { Btn, Slider, field } from "./EditUi";
import { SOUNDS, fmt, type Edit, type Position, type Shape, type TextItem } from "./useEdit";

const del = "text-fg-tertiary hover:text-status-failed";

/** The hidden picker behind "Or use a recording" and "My song". */
export function EditFileInput({ edit }: { edit: Edit }) {
  return (
    <input
      ref={edit.fileRef}
      type="file"
      accept="audio/*,video/*"
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
  return (
    <div className="space-y-3">
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
          <div className="flex items-center gap-2">
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

export function AudioPanel({ edit }: { edit: Edit }) {
  const { voice, setVoice, music, setMusic, accents, mine, making, now } = edit;
  return (
    <div className="space-y-5">
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
      <section className="space-y-2">
        <p className="font-medium">Speed</p>
        <div className="grid grid-cols-4 gap-2">
          {[0.5, 1, 1.5, 2].map((s) => (
            <Btn key={s} active={speed === s} onClick={() => setSpeed(s)}>
              <Gauge size={13} /> {s}x
            </Btn>
          ))}
        </div>
      </section>
    </div>
  );
}
