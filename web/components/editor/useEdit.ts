"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Job, VoiceAccent } from "@/lib/types";
import { contentUrl } from "@/components/media/MediaPreview";
import { readApiError } from "@/lib/errors";
import { useUpload } from "@/hooks/useUpload";
import { PreviewAudio } from "./previewAudio";

export type Part = { start: number; end: number };
export type Shape = "original" | "portrait" | "square" | "landscape";
export type Position = "top" | "upper_safe" | "middle" | "lower_safe" | "bottom";
export type TextItem = {
  id: number;
  text: string;
  start: number;
  end: number;
  position: Position;
  style: "bold_pop" | "clean";
};
export type Sound = { id: number; at: number; kind: "whoosh" | "pop" | "riser" | "impact" };

export const SOUNDS: Sound["kind"][] = ["whoosh", "pop", "riser", "impact"];
export const RATIO: Record<Exclude<Shape, "original">, number> = {
  portrait: 9 / 16,
  square: 1,
  landscape: 16 / 9,
};
export const TOP: Record<Position, string> = {
  top: "6%",
  upper_safe: "18%",
  middle: "44%",
  lower_safe: "62%",
  bottom: "80%",
};

export const fmt = (t: number) =>
  `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;

let nextId = 1;

const VOICE0 = {
  ai: false, // made by "Make voiceover" from the text (redo when the text changes)
  on: false,
  text: "",
  voice: "",
  handle: "",
  name: "",
  seconds: 0,
  start: 0,
  volume: 1,
};
const MUSIC0 = {
  ai: false,
  on: false,
  mode: "compose" as "compose" | "upload",
  compose: "",
  handle: "",
  name: "",
  volume: 0.35,
};

/**
 * Everything the hand editor knows about one video: the cuts, shape, volume, speed, texts,
 * voiceover, music and sound effects, the playhead and the live preview sound. The editor's
 * pieces (player, timeline, panels) all read this one object, so they can sit anywhere in the
 * layout. It starts over by itself when `source` (the video being edited) changes.
 */
export function useEdit(jobId: string, source: string) {
  const video = useRef<HTMLVideoElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const audio = useRef<PreviewAudio | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const [seen, setSeen] = useState(source);
  const [duration, setDuration] = useState(0);
  const [natural, setNatural] = useState<[number, number]>([16, 9]);
  const [now, setNow] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [parts, setParts] = useState<Part[]>([]);
  const [shape, setShape] = useState<Shape>("original");
  const [focus, setFocus] = useState<[number, number]>([0.5, 0.5]);
  const [volume, setVolume] = useState(1);
  const [speed, setSpeed] = useState(1);
  const [texts, setTexts] = useState<TextItem[]>([]);
  const [voice, setVoice] = useState(VOICE0);
  const [music, setMusic] = useState(MUSIC0);
  const [sounds, setSounds] = useState<Sound[]>([]);
  const [accents, setAccents] = useState<VoiceAccent[]>([]);
  const [mine, setMine] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const { items: uploads, upload } = useUpload();
  const [uploadFor, setUploadFor] = useState<"voice" | "music" | null>(null);
  const [making, setMaking] = useState<"" | "voice" | "music">("");

  // another video was picked: start from a clean slate (set during render, the React way)
  if (seen !== source) {
    setSeen(source);
    setDuration(0);
    setNow(0);
    setPlaying(false);
    setParts([]);
    setShape("original");
    setFocus([0.5, 0.5]);
    setVolume(1);
    setSpeed(1);
    setTexts([]);
    setVoice(VOICE0);
    setMusic(MUSIC0);
    setSounds([]);
    setError("");
  }

  useEffect(() => {
    // the sound graph is tied to the old <video> element: drop it, the next play builds a new one
    audio.current?.stop();
    audio.current = null;
  }, [source]);

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
    const done = uploads.find((u) => u.status === "done" && u.asset && !used.current.has(u.id));
    if (!done?.asset || !uploadFor) return;
    used.current.add(done.id);
    const handle = done.asset.handle;
    const name = done.file.name;
    if (uploadFor === "voice") setVoice((v) => ({ ...v, on: true, ai: false, handle, name, text: "" }));
    else setMusic((m) => ({ ...m, on: true, ai: false, mode: "upload", handle, name }));
    setUploadFor(null);
  }, [uploads, uploadFor]);
  const failed = uploads.find((u) => u.status === "error");
  const sending = uploads.some((u) => u.status === "uploading");

  const kept = useMemo(
    () => (parts.length ? parts : [{ start: 0, end: duration }]),
    [parts, duration],
  );
  const length = kept.reduce((s, p) => s + (p.end - p.start), 0) / speed;

  // source time → edited time (cuts removed, speed applied), like the server's Clock
  const clock = useCallback(
    (t: number): number | null => {
      let before = 0;
      for (const p of kept) {
        if (t < p.end) return (before + Math.max(0, t - p.start)) / speed;
        before += p.end - p.start;
      }
      return null;
    },
    [kept, speed],
  );

  // the sound of the preview: voiceover, music and effects in step with the picture
  const startSound = useCallback(() => {
    const v = video.current;
    if (!v || !audio.current) return;
    const layers = [];
    if (voice.on && voice.handle)
      layers.push({
        url: contentUrl(voice.handle),
        layer: { handle: voice.handle, start: clock(voice.start) ?? 0, volume: voice.volume, loop: false },
      });
    if (music.on && music.handle)
      layers.push({
        url: contentUrl(music.handle),
        layer: { handle: music.handle, start: 0, volume: music.volume, loop: true },
      });
    const hits = sounds.flatMap((s) => {
      const at = clock(s.at);
      return at === null ? [] : [{ at, kind: s.kind }];
    });
    void audio.current.start(clock(v.currentTime) ?? 0, layers, hits);
  }, [voice, music, sounds, clock]);

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
        audio.current?.stop();
        setPlaying(false);
      }
    }
  }

  function toggle() {
    const v = video.current;
    if (!v) return;
    if (playing) {
      v.pause();
      audio.current?.stop();
      setPlaying(false);
      return;
    }
    if (!kept.some((p) => v.currentTime >= p.start && v.currentTime < p.end - 0.05))
      v.currentTime = kept[0]?.start ?? 0;
    v.playbackRate = speed;
    if (!audio.current) {
      try {
        audio.current = new PreviewAudio(v); // needs a click to start: this is one
      } catch {
        audio.current = null; // no Web Audio: the picture still plays
      }
    }
    audio.current?.setVideoVolume(volume);
    void v.play();
    setPlaying(true);
    startSound();
  }

  function seekTo(t: number) {
    if (!video.current || !duration) return;
    const at = Math.max(0, Math.min(duration, t));
    video.current.currentTime = at;
    setNow(at);
    if (playing) setTimeout(startSound, 0); // the sound follows the jump
  }
  /** The video time under a screen x position on the timeline. */
  function timeAt(clientX: number): number {
    const rect = bar.current?.getBoundingClientRect();
    if (!rect || !duration) return 0;
    return Math.max(0, Math.min(duration, ((clientX - rect.left) / rect.width) * duration));
  }
  function seek(clientX: number) {
    const rect = bar.current?.getBoundingClientRect();
    if (!rect || !duration) return;
    seekTo(((clientX - rect.left) / rect.width) * duration);
  }

  useEffect(() => {
    if (!video.current) return;
    video.current.playbackRate = speed;
    if (audio.current) audio.current.setVideoVolume(volume);
    else video.current.volume = Math.min(1, volume);
  }, [speed, volume]);

  // a change while playing is heard at once
  useEffect(() => {
    if (playing) startSound();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the sound changes
  }, [startSound]);

  useEffect(() => () => audio.current?.stop(), []);

  async function makeSound(kind: "voice" | "music") {
    setMaking(kind);
    setError("");
    try {
      const body =
        kind === "voice"
          ? { text: voice.text.trim(), voice: voice.voice }
          : { style: music.compose.trim(), seconds: Math.max(10, Math.ceil(length)) };
      const r = await fetch(`/api/proxy/jobs/${jobId}/edit/${kind === "voice" ? "voiceover" : "music"}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error((await readApiError(r)).message);
      const made: { handle: string; seconds: number | null } = await r.json();
      const name = `${made.seconds ? `${made.seconds.toFixed(1)}s` : ""}`;
      if (kind === "voice")
        setVoice((v) => ({ ...v, ai: true, handle: made.handle, name, seconds: made.seconds ?? 0 }));
      else setMusic((m) => ({ ...m, ai: true, handle: made.handle, name }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work. Try again.");
    } finally {
      setMaking("");
    }
  }

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
    if (kept.length === 1) return setError("Keep at least one part of the video.");
    setParts(kept.filter((_, j) => j !== i));
  }
  /** Drag a part's left or right edge on the timeline (the neighbours and 0.2 s stay clear). */
  function trimEdge(i: number, edge: "start" | "end", t: number) {
    setParts((prev) => {
      const ps = (prev.length ? prev : [{ start: 0, end: duration }]).map((p) => ({ ...p }));
      const p = ps[i];
      if (!p) return prev;
      if (edge === "start") p.start = Math.max(i > 0 ? ps[i - 1]!.end : 0, Math.min(t, p.end - 0.2));
      else p.end = Math.min(i < ps.length - 1 ? ps[i + 1]!.start : duration, Math.max(t, p.start + 0.2));
      return ps;
    });
  }
  function patchText(id: number, p: Partial<TextItem>) {
    setTexts((cur) => cur.map((x) => (x.id === id ? { ...x, ...p } : x)));
  }
  const partAtNow = kept.findIndex((p) => now >= p.start && now < p.end);

  function addText() {
    setTexts((cur) => [
      ...cur,
      {
        id: nextId++,
        text: "Your text",
        start: now,
        end: Math.min(duration, now + 3),
        position: "lower_safe",
        style: "bold_pop",
      },
    ]);
  }
  function addSound(kind: Sound["kind"]) {
    setSounds((cur) => [...cur, { id: nextId++, at: now, kind }].sort((a, b) => a.at - b.at));
  }

  const dirty =
    parts.length > 0 ||
    shape !== "original" ||
    volume !== 1 ||
    speed !== 1 ||
    texts.some((t) => t.text.trim()) ||
    (voice.on && Boolean(voice.handle || voice.text.trim())) ||
    (music.on && Boolean(music.handle || (music.mode === "compose" && music.compose.trim()))) ||
    sounds.length > 0;

  const makingOnSave =
    (voice.on && voice.text && !voice.handle) || (music.on && music.mode === "compose" && !music.handle);

  // -- save ----------------------------------------------------------------------------------
  async function save(onSaved: (job: Job) => void) {
    setSaving(true);
    setError("");
    const body = {
      source,
      parts: kept.map((p) => ({ start: +p.start.toFixed(3), end: +p.end.toFixed(3) })),
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
        music.on && (music.handle || (music.mode === "compose" && music.compose.trim()))
          ? {
              handle: music.handle,
              compose: music.handle ? "" : music.compose.trim(),
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
      const job: Job = await r.json();
      onSaved(job);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't save. Try again.");
    } finally {
      setSaving(false);
    }
  }

  // callback refs, so the pieces of the editor can attach to the elements without holding refs
  const videoRef = useCallback((el: HTMLVideoElement | null) => {
    video.current = el;
    // the page can render (and the video learn its length) before React attaches the listeners
    if (el && el.readyState >= 1 && el.duration > 0) {
      setDuration(el.duration);
      setNatural([el.videoWidth || 16, el.videoHeight || 9]);
    }
  }, []);
  const barRef = useCallback((el: HTMLDivElement | null) => {
    bar.current = el;
  }, []);
  const fileRef = useCallback((el: HTMLInputElement | null) => {
    fileInput.current = el;
  }, []);

  function pickFile(for_: "voice" | "music") {
    setUploadFor(for_);
    fileInput.current?.click();
  }

  return {
    source,
    videoRef,
    barRef,
    fileRef,
    duration,
    setDuration,
    natural,
    setNatural,
    now,
    playing,
    setPlaying,
    parts,
    setParts,
    kept,
    length,
    clock,
    shape,
    setShape,
    focus,
    setFocus,
    volume,
    setVolume,
    speed,
    setSpeed,
    texts,
    setTexts,
    addText,
    voice,
    setVoice,
    music,
    setMusic,
    sounds,
    setSounds,
    addSound,
    accents,
    mine,
    saving,
    error,
    setError,
    failed,
    sending,
    upload,
    pickFile,
    making,
    makeSound,
    onTime,
    toggle,
    seek,
    seekTo,
    timeAt,
    trimEdge,
    patchText,
    setStart,
    setEnd,
    split,
    removePart,
    partAtNow,
    dirty,
    makingOnSave,
    save,
  };
}

export type Edit = ReturnType<typeof useEdit>;
