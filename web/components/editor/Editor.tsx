"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import clsx from "clsx";
import {
  ArrowLeft,
  AudioLines,
  Crop,
  Gauge,
  Loader2,
  Mic,
  Music,
  Pause,
  Play,
  Scissors,
  Sparkles,
  Trash2,
  Type,
  Volume2,
} from "lucide-react";
import type { VoiceAccent } from "@/lib/types";
import { contentUrl } from "@/components/media/MediaPreview";
import { readApiError } from "@/lib/errors";
import { useUpload } from "@/hooks/useUpload";

type Part = { start: number; end: number };
type Shape = "original" | "portrait" | "square" | "landscape";
type Position = "top" | "upper_safe" | "middle" | "lower_safe" | "bottom";
type TextItem = {
  id: number;
  text: string;
  start: number;
  end: number;
  position: Position;
  style: "bold_pop" | "clean";
};
type Sound = {
  id: number;
  at: number;
  kind: "whoosh" | "pop" | "riser" | "impact";
};
type Tab = "trim" | "shape" | "sound" | "text" | "voice" | "music" | "effects";

const TABS: { key: Tab; label: string; icon: typeof Scissors }[] = [
  { key: "trim", label: "Trim & cut", icon: Scissors },
  { key: "shape", label: "Shape", icon: Crop },
  { key: "sound", label: "Volume & speed", icon: Volume2 },
  { key: "text", label: "Text", icon: Type },
  { key: "voice", label: "Voiceover", icon: Mic },
  { key: "music", label: "Music", icon: Music },
  { key: "effects", label: "Sound effects", icon: AudioLines },
];
const ASPECT: Record<Exclude<Shape, "original">, string> = {
  portrait: "9 / 16",
  square: "1 / 1",
  landscape: "16 / 9",
};
const TOP: Record<Position, string> = {
  top: "6%",
  upper_safe: "18%",
  middle: "44%",
  lower_safe: "62%",
  bottom: "80%",
};
const SOUNDS: Sound["kind"][] = ["whoosh", "pop", "riser", "impact"];

const fmt = (t: number) =>
  `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
let nextId = 1;

export function Editor({ jobId, source }: { jobId: string; source: string }) {
  const router = useRouter();
  const video = useRef<HTMLVideoElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const [duration, setDuration] = useState(0);
  const [natural, setNatural] = useState<[number, number]>([16, 9]);
  const [now, setNow] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [tab, setTab] = useState<Tab>("trim");
  const [parts, setParts] = useState<Part[]>([]);
  const [shape, setShape] = useState<Shape>("original");
  const [focus, setFocus] = useState<[number, number]>([0.5, 0.5]);
  const [volume, setVolume] = useState(1);
  const [speed, setSpeed] = useState(1);
  const [texts, setTexts] = useState<TextItem[]>([]);
  const [voice, setVoice] = useState({
    on: false,
    text: "",
    voice: "",
    handle: "",
    name: "",
    start: 0,
    volume: 1,
  });
  const [music, setMusic] = useState({
    on: false,
    mode: "compose" as "compose" | "upload",
    compose: "",
    handle: "",
    name: "",
    volume: 0.35,
  });
  const [sounds, setSounds] = useState<Sound[]>([]);
  const [accents, setAccents] = useState<VoiceAccent[]>([]);
  const [mine, setMine] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const { items: uploads, upload } = useUpload();
  const [uploadFor, setUploadFor] = useState<"voice" | "music" | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch("/api/proxy/voices")
      .then((r) => (r.ok ? r.json() : { accents: [], mine: false }))
      .then((d: { accents: VoiceAccent[]; mine: boolean }) => {
        setAccents(d.accents);
        setMine(d.mine);
      })
      .catch(() => undefined);
  }, []);

  // a finished upload becomes the voiceover or the music it was picked for
  const used = useRef(new Set<string>());
  useEffect(() => {
    const done = uploads.find(
      (u) => u.status === "done" && u.asset && !used.current.has(u.id),
    );
    if (!done?.asset || !uploadFor) return;
    used.current.add(done.id);
    const handle = done.asset.handle;
    const name = done.file.name;
    if (uploadFor === "voice")
      setVoice((v) => ({ ...v, on: true, handle, name, text: "" }));
    else setMusic((m) => ({ ...m, on: true, mode: "upload", handle, name }));
    setUploadFor(null);
  }, [uploads, uploadFor]);
  const failed = uploads.find((u) => u.status === "error");
  const sending = uploads.some((u) => u.status === "uploading");

  const kept = useMemo(
    () => (parts.length ? parts : [{ start: 0, end: duration }]),
    [parts, duration],
  );
  const length = kept.reduce((s, p) => s + (p.end - p.start), 0) / speed;

  // play only the kept parts
  function onTime() {
    const v = video.current;
    if (!v) return;
    const t = v.currentTime;
    setNow(t);
    if (!playing) return;
    const inside = kept.find((p) => t >= p.start && t < p.end);
    if (!inside) {
      const next = kept.find((p) => p.start > t);
      if (next) v.currentTime = next.start;
      else {
        v.pause();
        setPlaying(false);
      }
    }
  }

  function toggle() {
    const v = video.current;
    if (!v) return;
    if (playing) {
      v.pause();
      setPlaying(false);
      return;
    }
    if (
      !kept.some(
        (p) => v.currentTime >= p.start && v.currentTime < p.end - 0.05,
      )
    )
      v.currentTime = kept[0]?.start ?? 0;
    v.playbackRate = speed;
    v.volume = Math.min(1, volume);
    void v.play();
    setPlaying(true);
  }

  function seek(clientX: number) {
    const rect = bar.current?.getBoundingClientRect();
    if (!rect || !video.current || !duration) return;
    const t = Math.max(
      0,
      Math.min(duration, ((clientX - rect.left) / rect.width) * duration),
    );
    video.current.currentTime = t;
    setNow(t);
  }

  useEffect(() => {
    if (video.current) {
      video.current.playbackRate = speed;
      video.current.volume = Math.min(1, volume);
    }
  }, [speed, volume]);

  // -- trim & cut ----------------------------------------------------------------------------
  function setStart() {
    setParts(() => {
      const ps = kept.filter((p) => p.end > now + 0.2);
      const first = ps[0];
      if (!first) return kept;
      ps[0] = { start: Math.max(first.start, now), end: first.end };
      return ps;
    });
  }
  function setEnd() {
    setParts(() => {
      const ps = kept.filter((p) => p.start < now - 0.2);
      const last = ps[ps.length - 1];
      if (!last) return kept;
      ps[ps.length - 1] = { start: last.start, end: Math.min(last.end, now) };
      return ps;
    });
  }
  function split() {
    setParts(() =>
      kept.flatMap((p) =>
        now > p.start + 0.2 && now < p.end - 0.2
          ? [
              { start: p.start, end: now },
              { start: now, end: p.end },
            ]
          : [p],
      ),
    );
  }
  function removePart(i: number) {
    if (kept.length === 1)
      return setError("Keep at least one part of the video.");
    setParts(kept.filter((_, j) => j !== i));
  }
  const partAtNow = kept.findIndex((p) => now >= p.start && now < p.end);

  // -- save ----------------------------------------------------------------------------------
  async function save() {
    setSaving(true);
    setError("");
    const body = {
      source,
      parts: kept.map((p) => ({
        start: +p.start.toFixed(3),
        end: +p.end.toFixed(3),
      })),
      speed,
      shape,
      focus_x: focus[0],
      focus_y: focus[1],
      volume,
      texts: texts
        .filter((t) => t.text.trim())
        .map((t) => ({
          text: t.text.trim(),
          start: t.start,
          end: t.end,
          position: t.position,
          style: t.style,
        })),
      voiceover:
        voice.on && (voice.handle || voice.text.trim())
          ? {
              handle: voice.handle,
              text: voice.handle ? "" : voice.text.trim(),
              voice: voice.voice,
              start: voice.start,
              volume: voice.volume,
            }
          : null,
      music:
        music.on &&
        (music.mode === "upload" ? music.handle : music.compose.trim())
          ? {
              handle: music.mode === "upload" ? music.handle : "",
              compose: music.mode === "compose" ? music.compose.trim() : "",
              volume: music.volume,
              loop: true,
            }
          : null,
      sounds: sounds.map((s) => ({ at: s.at, kind: s.kind })),
    };
    try {
      const r = await fetch(`/api/proxy/jobs/${jobId}/edit`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error((await readApiError(r)).message);
      const job: { id: string } = await r.json();
      router.push(`/studio/${job.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't save. Try again.");
      setSaving(false);
    }
  }

  if (!source)
    return (
      <p className="p-8 text-fg-secondary">Pick a video to edit from a chat.</p>
    );
  const boxAspect =
    shape === "original" ? `${natural[0]} / ${natural[1]}` : ASPECT[shape];
  const shownTexts = texts.filter((t) => now >= t.start && now < t.end);
  const pct = (t: number) => (duration ? `${(t / duration) * 100}%` : "0%");

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col gap-3">
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => router.back()}
          className="flex items-center gap-1.5 text-[13px] text-fg-secondary hover:text-fg"
        >
          <ArrowLeft size={15} /> Back to the chat
        </button>
        <div className="flex items-center gap-3">
          <span className="text-[12px] text-fg-tertiary">
            New length {fmt(length)}
          </span>
          <button
            type="button"
            onClick={save}
            disabled={saving || !duration || sending}
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-[14px] font-medium text-on-accent disabled:opacity-50"
          >
            {saving ? <Loader2 size={15} className="animate-spin" /> : null}
            {saving
              ? (voice.on && voice.text && !voice.handle) ||
                (music.on && music.mode === "compose")
                ? "Making the sound…"
                : "Saving…"
              : "Save as new version"}
          </button>
        </div>
      </div>
      {error ? (
        <p className="text-[13px] text-red-600 dark:text-red-400">{error}</p>
      ) : null}
      {failed ? (
        <p className="text-[13px] text-red-600 dark:text-red-400">
          {failed.file.name}: {failed.error}
        </p>
      ) : null}
      {sending ? (
        <p className="text-[13px] text-fg-secondary">Uploading your file…</p>
      ) : null}

      <div className="flex min-h-0 flex-1 gap-4">
        {/* tools */}
        <div className="flex w-[340px] shrink-0 flex-col overflow-hidden rounded-xl border border-border bg-bg">
          <div className="grid grid-cols-4 gap-1 border-b border-border p-2">
            {TABS.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                type="button"
                onClick={() => setTab(key)}
                className={clsx(
                  "flex flex-col items-center gap-1 rounded-md px-1 py-2 text-[10.5px]",
                  tab === key
                    ? "bg-accent-subtle-bg text-accent"
                    : "text-fg-secondary hover:bg-bg-inset",
                )}
              >
                <Icon size={16} />
                {label}
              </button>
            ))}
          </div>
          <div className="flex-1 space-y-3 overflow-y-auto p-4 text-[13px] text-fg">
            {tab === "trim" ? (
              <>
                <p className="text-fg-secondary">
                  Move the playhead on the timeline (click it), then:
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <Btn onClick={setStart}>Start here</Btn>
                  <Btn onClick={setEnd}>End here</Btn>
                  <Btn onClick={split}>
                    <Scissors size={13} /> Split here
                  </Btn>
                  <Btn
                    onClick={() => partAtNow >= 0 && removePart(partAtNow)}
                    disabled={partAtNow < 0}
                  >
                    <Trash2 size={13} /> Delete this part
                  </Btn>
                </div>
                <div className="space-y-1">
                  {kept.map((p, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between rounded-md bg-bg-subtle px-2 py-1.5"
                    >
                      <span>
                        Part {i + 1}: {fmt(p.start)} – {fmt(p.end)}
                      </span>
                      <button
                        type="button"
                        aria-label={`Delete part ${i + 1}`}
                        onClick={() => removePart(i)}
                        className="text-fg-tertiary hover:text-red-600"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  ))}
                </div>
                {parts.length ? (
                  <button
                    type="button"
                    onClick={() => setParts([])}
                    className="text-[12px] text-fg-secondary underline"
                  >
                    Undo all cuts
                  </button>
                ) : null}
              </>
            ) : null}

            {tab === "shape" ? (
              <>
                <div className="grid grid-cols-2 gap-2">
                  {(
                    ["original", "portrait", "square", "landscape"] as Shape[]
                  ).map((s) => (
                    <Btn
                      key={s}
                      active={shape === s}
                      onClick={() => setShape(s)}
                    >
                      {s === "original"
                        ? "As it is"
                        : s === "portrait"
                          ? "9:16 Reels/TikTok"
                          : s === "square"
                            ? "1:1 Square"
                            : "16:9 YouTube"}
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
              </>
            ) : null}

            {tab === "sound" ? (
              <>
                <Slider
                  label={`Video sound ${Math.round(volume * 100)}%`}
                  value={volume}
                  min={0}
                  max={3}
                  step={0.05}
                  onChange={setVolume}
                />
                {volume > 1 ? (
                  <p className="text-[12px] text-fg-tertiary">
                    Louder than 100% is applied when you save.
                  </p>
                ) : null}
                <p className="pt-2 font-medium">Speed</p>
                <div className="grid grid-cols-4 gap-2">
                  {[0.5, 1, 1.5, 2].map((s) => (
                    <Btn
                      key={s}
                      active={speed === s}
                      onClick={() => setSpeed(s)}
                    >
                      <Gauge size={13} /> {s}x
                    </Btn>
                  ))}
                </div>
              </>
            ) : null}

            {tab === "text" ? (
              <>
                <Btn
                  onClick={() =>
                    setTexts([
                      ...texts,
                      {
                        id: nextId++,
                        text: "Your text",
                        start: now,
                        end: Math.min(duration, now + 3),
                        position: "lower_safe",
                        style: "bold_pop",
                      },
                    ])
                  }
                >
                  <Type size={13} /> Add text at {fmt(now)}
                </Btn>
                {texts.map((t) => (
                  <div
                    key={t.id}
                    className="space-y-2 rounded-md border border-border p-2.5"
                  >
                    <input
                      aria-label="Text"
                      value={t.text}
                      onChange={(e) =>
                        setTexts(
                          texts.map((x) =>
                            x.id === t.id ? { ...x, text: e.target.value } : x,
                          ),
                        )
                      }
                      className="w-full rounded-md border border-border bg-bg px-2 py-1 outline-none focus:border-accent"
                    />
                    <div className="flex flex-wrap gap-1.5 text-[12px]">
                      <span className="text-fg-tertiary">
                        {fmt(t.start)}–{fmt(t.end)}
                      </span>
                      <button
                        type="button"
                        className="underline"
                        onClick={() =>
                          setTexts(
                            texts.map((x) =>
                              x.id === t.id ? { ...x, start: now } : x,
                            ),
                          )
                        }
                      >
                        start here
                      </button>
                      <button
                        type="button"
                        className="underline"
                        onClick={() =>
                          setTexts(
                            texts.map((x) =>
                              x.id === t.id
                                ? { ...x, end: Math.max(x.start + 0.3, now) }
                                : x,
                            ),
                          )
                        }
                      >
                        end here
                      </button>
                    </div>
                    <div className="flex items-center gap-2">
                      <select
                        aria-label="Where"
                        value={t.position}
                        onChange={(e) =>
                          setTexts(
                            texts.map((x) =>
                              x.id === t.id
                                ? { ...x, position: e.target.value as Position }
                                : x,
                            ),
                          )
                        }
                        className="rounded-md border border-border bg-bg px-1.5 py-1 text-[12px]"
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
                        onChange={(e) =>
                          setTexts(
                            texts.map((x) =>
                              x.id === t.id
                                ? {
                                    ...x,
                                    style: e.target.value as TextItem["style"],
                                  }
                                : x,
                            ),
                          )
                        }
                        className="rounded-md border border-border bg-bg px-1.5 py-1 text-[12px]"
                      >
                        <option value="bold_pop">Bold yellow</option>
                        <option value="clean">Clean white</option>
                      </select>
                      <button
                        type="button"
                        aria-label="Remove text"
                        onClick={() =>
                          setTexts(texts.filter((x) => x.id !== t.id))
                        }
                        className="ml-auto text-fg-tertiary hover:text-red-600"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                ))}
              </>
            ) : null}

            {tab === "voice" ? (
              <>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={voice.on}
                    onChange={(e) =>
                      setVoice({ ...voice, on: e.target.checked })
                    }
                  />{" "}
                  Add a voiceover
                </label>
                {voice.on ? (
                  <>
                    {voice.handle ? (
                      <div className="flex items-center justify-between rounded-md bg-bg-subtle px-2 py-1.5">
                        <span className="truncate">
                          🎙 {voice.name || "Your recording"}
                        </span>
                        <button
                          type="button"
                          className="text-[12px] underline"
                          onClick={() =>
                            setVoice({ ...voice, handle: "", name: "" })
                          }
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
                            setVoice({ ...voice, text: e.target.value })
                          }
                          className="w-full resize-none rounded-md border border-border bg-bg px-2 py-1.5 outline-none focus:border-accent"
                        />
                        <select
                          aria-label="Voice"
                          value={voice.voice}
                          onChange={(e) =>
                            setVoice({ ...voice, voice: e.target.value })
                          }
                          className="w-full rounded-md border border-border bg-bg px-2 py-1.5"
                        >
                          <option value="">American woman (default)</option>
                          {mine ? (
                            <option value="my_voice">My voice</option>
                          ) : null}
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
                        <Btn
                          onClick={() => {
                            setUploadFor("voice");
                            fileInput.current?.click();
                          }}
                        >
                          <Mic size={13} /> Or use a recording
                        </Btn>
                      </>
                    )}
                    <div className="flex items-center gap-2 text-[12px]">
                      Starts at {fmt(voice.start)}
                      <button
                        type="button"
                        className="underline"
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
              </>
            ) : null}

            {tab === "music" ? (
              <>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={music.on}
                    onChange={(e) =>
                      setMusic({ ...music, on: e.target.checked })
                    }
                  />{" "}
                  Add music
                </label>
                {music.on ? (
                  <>
                    <div className="grid grid-cols-2 gap-2">
                      <Btn
                        active={music.mode === "compose"}
                        onClick={() => setMusic({ ...music, mode: "compose" })}
                      >
                        <Sparkles size={13} /> AI makes it
                      </Btn>
                      <Btn
                        active={music.mode === "upload"}
                        onClick={() => {
                          setUploadFor("music");
                          fileInput.current?.click();
                        }}
                      >
                        <Music size={13} /> My song
                      </Btn>
                    </div>
                    {music.mode === "compose" ? (
                      <input
                        aria-label="What music"
                        placeholder="e.g. upbeat afrobeats, calm piano, epic cinematic"
                        value={music.compose}
                        onChange={(e) =>
                          setMusic({ ...music, compose: e.target.value })
                        }
                        className="w-full rounded-md border border-border bg-bg px-2 py-1.5 outline-none focus:border-accent"
                      />
                    ) : music.handle ? (
                      <p className="rounded-md bg-bg-subtle px-2 py-1.5">
                        ♪ {music.name}
                      </p>
                    ) : null}
                    <Slider
                      label={`Music volume ${Math.round(music.volume * 100)}%`}
                      value={music.volume}
                      min={0}
                      max={1.5}
                      step={0.05}
                      onChange={(x) => setMusic({ ...music, volume: x })}
                    />
                    <p className="text-[12px] text-fg-tertiary">
                      It plays from the start and repeats to the end.
                    </p>
                  </>
                ) : null}
              </>
            ) : null}

            {tab === "effects" ? (
              <>
                <p className="text-fg-secondary">
                  Add a sound at the playhead ({fmt(now)}):
                </p>
                <div className="grid grid-cols-2 gap-2">
                  {SOUNDS.map((k) => (
                    <Btn
                      key={k}
                      onClick={() =>
                        setSounds(
                          [...sounds, { id: nextId++, at: now, kind: k }].sort(
                            (a, b) => a.at - b.at,
                          ),
                        )
                      }
                    >
                      <AudioLines size={13} />{" "}
                      {k.charAt(0).toUpperCase() + k.slice(1)}
                    </Btn>
                  ))}
                </div>
                {sounds.map((s) => (
                  <div
                    key={s.id}
                    className="flex items-center justify-between rounded-md bg-bg-subtle px-2 py-1.5"
                  >
                    <span>
                      {s.kind} at {fmt(s.at)}
                    </span>
                    <button
                      type="button"
                      aria-label="Remove sound"
                      onClick={() =>
                        setSounds(sounds.filter((x) => x.id !== s.id))
                      }
                      className="text-fg-tertiary hover:text-red-600"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                ))}
              </>
            ) : null}
          </div>
        </div>

        {/* preview + timeline */}
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <div className="flex min-h-0 flex-1 items-center justify-center rounded-xl bg-black p-4">
            <div
              className="relative max-h-full overflow-hidden rounded-md"
              style={{
                aspectRatio: boxAspect,
                height: "100%",
                maxWidth: "100%",
              }}
            >
              <video
                ref={video}
                src={contentUrl(source)}
                preload="auto"
                playsInline
                onLoadedMetadata={(e) => {
                  setDuration(e.currentTarget.duration);
                  setNatural([
                    e.currentTarget.videoWidth || 16,
                    e.currentTarget.videoHeight || 9,
                  ]);
                }}
                onTimeUpdate={onTime}
                onEnded={() => setPlaying(false)}
                onClick={toggle}
                className="h-full w-full object-cover"
                style={{
                  objectPosition: `${focus[0] * 100}% ${focus[1] * 100}%`,
                }}
              />
              {shownTexts.map((t) => (
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

          <div className="rounded-xl border border-border bg-bg p-3">
            <div className="mb-2 flex items-center gap-3">
              <button
                type="button"
                onClick={toggle}
                aria-label={playing ? "Pause" : "Play"}
                className="rounded-full bg-accent p-2 text-on-accent"
              >
                {playing ? <Pause size={15} /> : <Play size={15} />}
              </button>
              <span className="font-mono text-[12px] text-fg-secondary">
                {fmt(now)} / {fmt(duration)}
              </span>
              <span className="text-[12px] text-fg-tertiary">
                Click the timeline to move the playhead. Grey parts are cut.
              </span>
            </div>
            <div
              ref={bar}
              role="slider"
              aria-label="Timeline"
              aria-valuenow={now}
              tabIndex={0}
              onClick={(e) => seek(e.clientX)}
              className="relative h-16 cursor-pointer select-none rounded-md bg-bg-inset"
            >
              {kept.map((p, i) => (
                <div
                  key={i}
                  className="absolute inset-y-2 rounded-sm border border-accent bg-accent/30"
                  style={{ left: pct(p.start), width: pct(p.end - p.start) }}
                />
              ))}
              {texts.map((t) => (
                <div
                  key={t.id}
                  title={t.text}
                  className="absolute top-0 h-1.5 rounded-full bg-yellow-400"
                  style={{ left: pct(t.start), width: pct(t.end - t.start) }}
                />
              ))}
              {voice.on ? (
                <div
                  title="Voiceover starts"
                  className="absolute bottom-0 h-1.5 w-6 rounded-full bg-green-500"
                  style={{ left: pct(voice.start) }}
                />
              ) : null}
              {sounds.map((s) => (
                <div
                  key={s.id}
                  title={s.kind}
                  className="absolute bottom-1 h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-pink-500"
                  style={{ left: pct(s.at) }}
                />
              ))}
              <div
                className="pointer-events-none absolute inset-y-0 w-0.5 bg-red-500"
                style={{ left: pct(now) }}
              />
            </div>
          </div>
        </div>
      </div>
      <input
        ref={fileInput}
        type="file"
        accept="audio/*,video/*"
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) upload(e.target.files);
          e.target.value = "";
        }}
      />
    </div>
  );
}

function Btn({
  children,
  onClick,
  active,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-md border px-2 py-1.5 text-[12.5px] disabled:opacity-40",
        active
          ? "border-accent bg-accent-subtle-bg text-accent"
          : "border-border text-fg hover:bg-bg-inset",
      )}
    >
      {children}
    </button>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-[12.5px] text-fg-secondary">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[var(--color-accent)]"
      />
    </label>
  );
}
