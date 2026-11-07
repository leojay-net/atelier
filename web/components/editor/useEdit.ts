"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Job, VoiceAccent } from "@/lib/types";
import { contentUrl } from "@/components/media/MediaPreview";
import { readApiError } from "@/lib/errors";
import { useUpload } from "@/hooks/useUpload";
import { PreviewAudio } from "./previewAudio";

export type Part = { start: number; end: number; speed?: number };
export type Shape = "original" | "portrait" | "square" | "landscape";
export type Position = "top" | "upper_safe" | "middle" | "lower_safe" | "bottom";
export type TextItem = {
  id: number;
  text: string;
  start: number;
  end: number;
  position: Position;
  style: "bold_pop" | "clean";
  color?: string;
  animate?: "auto" | "none" | "rise" | "pop";
};
export type Adjust = { brightness: number; contrast: number; saturation: number; gamma: number };
export const NEUTRAL: Adjust = { brightness: 0, contrast: 1, saturation: 1, gamma: 1 };
/** Named looks: just sets of the four numbers, so the AI and the editor share them. */
export const FILTERS: { key: string; label: string; adjust: Adjust }[] = [
  { key: "none", label: "Original", adjust: NEUTRAL },
  { key: "vivid", label: "Vivid", adjust: { brightness: 0.03, contrast: 1.15, saturation: 1.45, gamma: 1 } },
  { key: "warm", label: "Warm", adjust: { brightness: 0.05, contrast: 1.05, saturation: 1.15, gamma: 1.08 } },
  { key: "cool", label: "Cool", adjust: { brightness: -0.02, contrast: 1.08, saturation: 0.95, gamma: 0.94 } },
  { key: "bw", label: "Black & white", adjust: { brightness: 0, contrast: 1.2, saturation: 0, gamma: 1 } },
  { key: "fade", label: "Faded", adjust: { brightness: 0.08, contrast: 0.82, saturation: 0.8, gamma: 1.1 } },
  { key: "drama", label: "Dramatic", adjust: { brightness: -0.04, contrast: 1.45, saturation: 1.1, gamma: 0.9 } },
  { key: "bright", label: "Bright", adjust: { brightness: 0.12, contrast: 1.05, saturation: 1.1, gamma: 1.1 } },
];
export type Anchor =
  | "top_left" | "top_center" | "top_right"
  | "center_left" | "center" | "center_right"
  | "bottom_left" | "bottom_center" | "bottom_right";
export type Overlay = {
  id: number;
  handle: string;
  name: string;
  anchor: Anchor;
  scale: number;
  opacity: number;
  start: number;
  end: number | null;
};
export const TRANSITIONS = ["fade", "dissolve", "wipeleft", "wiperight", "slideleft", "slideright", "circlecrop", "fadeblack", "fadewhite"] as const;
export type Transition = (typeof TRANSITIONS)[number];
export const TRANSITION_SECONDS = 0.5;
export type TextItemExtra = { color?: string; animate?: "auto" | "none" | "rise" | "pop" };
export type Sound = { id: number; at: number; kind: "whoosh" | "pop" | "riser" | "impact" };

/** The layers (tracks) you can mute or delete one by one. */
export type Layer = "video" | "text" | "stickers" | "effects" | "voice" | "music";
const UNMUTED: Record<Layer, boolean> = { video: false, text: false, stickers: false, effects: false, voice: false, music: false };

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
  const [uploadFor, setUploadFor] = useState<"voice" | "music" | "sticker" | null>(null);
  const [muted, setMuted] = useState(UNMUTED);
  const [adjust, setAdjust] = useState<Adjust>(NEUTRAL);
  const [filter, setFilter] = useState("none");
  const [fadeIn, setFadeIn] = useState(0);
  const [fadeOut, setFadeOut] = useState(0);
  const [transition, setTransition] = useState<Transition | null>(null);
  const [captions, setCaptions] = useState<{ on: boolean; style: "bold_pop" | "clean" | "minimal_lower" }>({ on: false, style: "bold_pop" });
  const [overlays, setOverlays] = useState<Overlay[]>([]);
  const [denoise, setDenoise] = useState(false);
  const [loudnorm, setLoudnorm] = useState(false);
  const [stabilize, setStabilize] = useState(false);
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
    setMuted(UNMUTED);
    setAdjust(NEUTRAL);
    setFilter("none");
    setFadeIn(0);
    setFadeOut(0);
    setTransition(null);
    setCaptions({ on: false, style: "bold_pop" });
    setOverlays([]);
    setDenoise(false);
    setLoudnorm(false);
    setStabilize(false);
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
    if (uploadFor === "sticker")
      setOverlays((cur) => [
        ...cur,
        { id: nextId++, handle, name, anchor: "top_right", scale: 0.25, opacity: 1, start: 0, end: null },
      ]);
    else if (uploadFor === "voice") setVoice((v) => ({ ...v, on: true, ai: false, handle, name, text: "" }));
    else setMusic((m) => ({ ...m, on: true, ai: false, mode: "upload", handle, name }));
    setUploadFor(null);
  }, [uploads, uploadFor]);
  const failed = uploads.find((u) => u.status === "error");
  const sending = uploads.some((u) => u.status === "uploading");

  const kept = useMemo(
    () => (parts.length ? parts : [{ start: 0, end: duration }]),
    [parts, duration],
  );
  // a crossfade between cut parts, unless a part is too short for it (as on the server)
  const overlap =
    transition && kept.length > 1 && kept.every((p) => (p.end - p.start) / (p.speed ?? 1) >= 2 * TRANSITION_SECONDS + 0.1)
      ? TRANSITION_SECONDS
      : 0;
  const length =
    (kept.reduce((s, p) => s + (p.end - p.start) / (p.speed ?? 1), 0) - overlap * (kept.length - 1)) / speed;

  // source time → edited time (cuts removed, each part's speed, then the whole speed), like the
  // server's Clock. The preview does not draw the crossfade, so it leaves the overlap out.
  const clock = useCallback(
    (t: number): number | null => {
      let before = 0;
      for (const p of kept) {
        const sp = p.speed ?? 1;
        if (t < p.end) return (before + Math.max(0, t - p.start) / sp) / speed;
        before += (p.end - p.start) / sp;
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
    if (voice.on && voice.handle && !muted.voice)
      layers.push({
        url: contentUrl(voice.handle),
        layer: { handle: voice.handle, start: clock(voice.start) ?? 0, volume: voice.volume, loop: false },
      });
    if (music.on && music.handle && !muted.music)
      layers.push({
        url: contentUrl(music.handle),
        layer: { handle: music.handle, start: 0, volume: music.volume, loop: true },
      });
    const hits = (muted.effects ? [] : sounds).flatMap((s) => {
      const at = clock(s.at);
      return at === null ? [] : [{ at, kind: s.kind }];
    });
    void audio.current.start(clock(v.currentTime) ?? 0, layers, hits);
  }, [voice, music, sounds, muted, clock]);

  // play only the kept parts
  function onTime() {
    const v = video.current;
    if (!v) return;
    const t = v.currentTime;
    setNow(t);
    if (!playing) return;
    const inside = kept.find((p) => t >= p.start && t < p.end);
    if (inside) {
      const rate = speed * (inside.speed ?? 1);
      if (Math.abs(v.playbackRate - rate) > 0.001) v.playbackRate = rate;
    }
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
    audio.current?.setVideoVolume(muted.video ? 0 : volume);
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
    const level = muted.video ? 0 : volume;
    if (audio.current) audio.current.setVideoVolume(level);
    else video.current.volume = Math.min(1, level);
  }, [speed, volume, muted.video]);

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
              { start: p.start, end: now, speed: p.speed },
              { start: now, end: p.end, speed: p.speed },
            ]
          : [p],
      ),
    );
  }
  /** One kept part at its own speed (on top of the whole-video speed). */
  function setPartSpeed(i: number, sp: number) {
    setParts(() => kept.map((p, j) => (j === i ? { ...p, speed: sp } : p)));
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

  function toggleMute(k: Layer) {
    setMuted((m) => ({ ...m, [k]: !m[k] }));
  }
  /** Take one layer out of the video altogether (the rest stays as it is). */
  function removeLayer(k: Layer) {
    if (k === "text") setTexts([]);
    else if (k === "effects") setSounds([]);
    else if (k === "stickers") setOverlays([]);
    else if (k === "voice") setVoice(VOICE0);
    else if (k === "music") setMusic(MUSIC0);
    else setMuted((m) => ({ ...m, video: true }));
    if (k !== "video") setMuted((m) => ({ ...m, [k]: false }));
  }

  const looks = JSON.stringify(adjust) !== JSON.stringify(NEUTRAL);
  const dirty =
    looks ||
    fadeIn > 0 ||
    fadeOut > 0 ||
    (transition !== null && kept.length > 1) ||
    captions.on ||
    overlays.length > 0 ||
    denoise ||
    loudnorm ||
    stabilize ||
    kept.some((p) => (p.speed ?? 1) !== 1) ||
    muted.video ||
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
      parts: kept.map((p) => ({ start: +p.start.toFixed(3), end: +p.end.toFixed(3), speed: p.speed ?? 1 })),
      speed,
      shape,
      focus_x: focus[0],
      focus_y: focus[1],
      volume: muted.video ? 0 : volume,
      texts: (muted.text ? [] : texts)
        .filter((t) => t.text.trim())
        .map((t) => ({
          text: t.text.trim(),
          start: t.start,
          end: t.end,
          position: t.position,
          style: t.style,
          ...(t.color ? { color: t.color } : {}),
          ...(t.animate && t.animate !== "auto" ? { animate: t.animate } : {}),
        })),
      voiceover:
        voice.on && !muted.voice && (voice.handle || voice.text.trim())
          ? {
              handle: voice.handle,
              text: voice.handle ? "" : voice.text.trim(),
              voice: voice.voice,
              start: voice.start,
              volume: voice.volume,
            }
          : null,
      music:
        music.on && !muted.music && (music.handle || (music.mode === "compose" && music.compose.trim()))
          ? {
              handle: music.handle,
              compose: music.handle ? "" : music.compose.trim(),
              volume: music.volume,
              loop: true,
            }
          : null,
      sounds: (muted.effects ? [] : sounds).map((s) => ({ at: s.at, kind: s.kind })),
      adjust,
      fade_in: fadeIn,
      fade_out: fadeOut,
      transition: kept.length > 1 ? transition : null,
      captions: captions.on ? { style: captions.style } : null,
      overlays: (muted.stickers ? [] : overlays).map((o) => ({
        handle: o.handle,
        anchor: o.anchor,
        scale: o.scale,
        opacity: o.opacity,
        start: o.start,
        ...(o.end !== null ? { end: o.end } : {}),
      })),
      denoise,
      loudnorm,
      stabilize,
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

  // -- undo / redo: a snapshot of everything you can set, taken a moment after each change ----
  const doc = {
    parts, shape, focus, volume, speed, texts, voice, music, sounds, muted, adjust, filter,
    fadeIn, fadeOut, transition, captions, overlays, denoise, loudnorm, stabilize,
  };
  const sig = JSON.stringify(doc);
  const history = useRef<{ source: string; stack: string[]; at: number }>({ source: "", stack: [], at: -1 });
  const [, bump] = useState(0);
  useEffect(() => {
    const h = history.current;
    if (h.source !== source) {
      history.current = { source, stack: [sig], at: 0 };
      bump((n) => n + 1);
      return;
    }
    if (h.stack[h.at] === sig) return;
    const timer = window.setTimeout(() => {
      const cur = history.current;
      if (cur.stack[cur.at] === sig) return;
      cur.stack = [...cur.stack.slice(0, cur.at + 1), sig].slice(-60);
      cur.at = cur.stack.length - 1;
      bump((n) => n + 1);
    }, 350);
    return () => window.clearTimeout(timer);
  }, [sig, source]);

  function restore(raw: string) {
    const d = JSON.parse(raw) as typeof doc;
    setParts(d.parts);
    setShape(d.shape);
    setFocus(d.focus);
    setVolume(d.volume);
    setSpeed(d.speed);
    setTexts(d.texts);
    setVoice(d.voice);
    setMusic(d.music);
    setSounds(d.sounds);
    setMuted(d.muted);
    setAdjust(d.adjust);
    setFilter(d.filter);
    setFadeIn(d.fadeIn);
    setFadeOut(d.fadeOut);
    setTransition(d.transition);
    setCaptions(d.captions);
    setOverlays(d.overlays);
    setDenoise(d.denoise);
    setLoudnorm(d.loudnorm);
    setStabilize(d.stabilize);
  }
  function undo() {
    const h = history.current;
    // a change still waiting for its snapshot is undone first
    if (h.stack[h.at] !== sig) {
      h.stack = [...h.stack.slice(0, h.at + 1), sig].slice(-60);
      h.at = h.stack.length - 1;
    }
    if (h.at > 0) {
      h.at -= 1;
      restore(h.stack[h.at]!);
      bump((n) => n + 1);
    }
  }
  function redo() {
    const h = history.current;
    if (h.at < h.stack.length - 1) {
      h.at += 1;
      restore(h.stack[h.at]!);
      bump((n) => n + 1);
    }
  }
  /* eslint-disable react-hooks/refs -- the history lives in a ref; `bump` re-renders when it moves */
  const canUndo = history.current.at > 0 || history.current.stack[history.current.at] !== sig;
  const canRedo = history.current.at < history.current.stack.length - 1;
  /* eslint-enable react-hooks/refs */
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest("input, textarea, select, [contenteditable]")) return;
      if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
      if (e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if (e.key.toLowerCase() === "y") {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });

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

  function pickFile(for_: "voice" | "music" | "sticker") {
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
    adjust,
    setAdjust,
    filter,
    setFilter,
    fadeIn,
    setFadeIn,
    fadeOut,
    setFadeOut,
    transition,
    setTransition,
    captions,
    setCaptions,
    overlays,
    setOverlays,
    denoise,
    setDenoise,
    loudnorm,
    setLoudnorm,
    stabilize,
    setStabilize,
    setPartSpeed,
    undo,
    redo,
    canUndo,
    canRedo,
    muted,
    toggleMute,
    removeLayer,
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
