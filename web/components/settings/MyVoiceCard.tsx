"use client";

import { useEffect, useRef, useState } from "react";
import { Mic, Square, Trash2, Upload } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { readApiError } from "@/lib/errors";

interface MyVoice {
  recorded: boolean;
  created_at: string | null;
  seconds: number | null;
  passage: string;
}

const MAX_SECONDS = 30;

/** Record (or upload) the user's own voice, so voiceovers can be read in it ("My voice"). */
export function MyVoiceCard() {
  const [info, setInfo] = useState<MyVoice | null>(null);
  const [state, setState] = useState<"idle" | "recording" | "review">("idle");
  const [take, setTake] = useState<{ blob: Blob; url: string; read: boolean } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0); // refreshes the saved recording's player
  const recorder = useRef<MediaRecorder | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch("/api/proxy/my-voice")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: MyVoice | null) => d && setInfo(d))
      .catch(() => undefined);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  async function start() {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        if (timer.current) clearInterval(timer.current);
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        setTake({ blob, url: URL.createObjectURL(blob), read: true });
        setState("review");
      };
      recorder.current = rec;
      rec.start();
      setElapsed(0);
      setState("recording");
      const t0 = Date.now();
      timer.current = setInterval(() => {
        const s = Math.floor((Date.now() - t0) / 1000);
        setElapsed(s);
        if (s >= MAX_SECONDS) rec.state === "recording" && rec.stop();
      }, 250);
    } catch {
      setError("We couldn't use your microphone. Allow microphone access in your browser, or upload a recording instead.");
    }
  }

  function stop() {
    if (recorder.current?.state === "recording") recorder.current.stop();
  }

  function pickFile(f: File) {
    setError("");
    setTake({ blob: f, url: URL.createObjectURL(f), read: false });
    setState("review");
  }

  async function save() {
    if (!take) return;
    setBusy(true);
    setError("");
    const form = new FormData();
    const ext = take.blob.type.includes("mp4") ? "m4a" : take.blob.type.includes("ogg") ? "ogg" : "webm";
    form.append("file", take.blob, take.blob instanceof File ? take.blob.name : `my-voice.${ext}`);
    form.append("consent", String(consent));
    form.append("read_passage", String(take.read));
    const r = await fetch("/api/proxy/my-voice", { method: "POST", body: form }).catch(() => null);
    setBusy(false);
    if (!r) return setError("That didn't upload. Check your connection and try again.");
    if (!r.ok) return setError((await readApiError(r)).message);
    setInfo(await r.json());
    setTake(null);
    setConsent(false);
    setState("idle");
    setVersion((v) => v + 1);
  }

  async function remove() {
    setBusy(true);
    await fetch("/api/proxy/my-voice", { method: "DELETE" }).catch(() => null);
    setBusy(false);
    setInfo((cur) => (cur ? { ...cur, recorded: false, created_at: null, seconds: null } : cur));
  }

  if (!info) return null;
  return (
    <Card className="space-y-4 p-5">
      <div>
        <h2 className="text-[14px] font-semibold text-fg">Your voice</h2>
        <p className="mt-1 text-[13px] text-fg-secondary">
          Record yourself once and your voiceovers can be read in your own voice. Choose
          &ldquo;My voice&rdquo; under the chat box, or just ask for it.
        </p>
      </div>

      {info.recorded && state === "idle" ? (
        <div className="space-y-2 rounded-md bg-bg-subtle p-3">
          <p className="text-[13px] text-fg">
            Your voice is saved{info.seconds ? ` (${info.seconds} seconds)` : ""}.
          </p>
          <audio key={version} controls src="/api/proxy/my-voice/audio" className="w-full" />
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" onClick={start}>
              <Mic size={14} /> Record again
            </Button>
            <Button size="sm" variant="destructive" loading={busy} onClick={remove}>
              <Trash2 size={14} /> Delete my voice
            </Button>
          </div>
        </div>
      ) : null}

      {!info.recorded || state !== "idle" ? (
        <div className="space-y-3">
          <div className="rounded-md border border-border bg-bg-subtle p-3">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-fg-tertiary">
              Read this aloud, in your normal voice
            </p>
            <p className="mt-1.5 text-[14px] leading-relaxed text-fg">{info.passage}</p>
          </div>

          {state === "recording" ? (
            <div className="flex items-center gap-3">
              <span className="flex items-center gap-1.5 text-[13px] text-red-600 dark:text-red-400">
                <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-600" /> Recording {elapsed}s
              </span>
              <Button size="sm" onClick={stop}>
                <Square size={13} /> Stop
              </Button>
            </div>
          ) : state === "review" && take ? (
            <div className="space-y-3">
              <audio controls src={take.url} className="w-full" />
              <label className="flex items-start gap-2 text-[13px] text-fg">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(e) => setConsent(e.target.checked)}
                  className="mt-0.5"
                />
                <span>This is my own voice, and I agree to it being used for my voiceovers.</span>
              </label>
              <div className="flex gap-2">
                <Button size="sm" disabled={!consent} loading={busy} onClick={save}>
                  Save my voice
                </Button>
                <Button size="sm" variant="secondary" onClick={start}>
                  <Mic size={14} /> Record again
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setState("idle")}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={start}>
                <Mic size={14} /> Start recording
              </Button>
              <Button size="sm" variant="secondary" onClick={() => fileInput.current?.click()}>
                <Upload size={14} /> Upload a recording
              </Button>
              <span className="text-[12px] text-fg-tertiary">10–30 seconds in a quiet room works best</span>
              <input
                ref={fileInput}
                type="file"
                accept="audio/*,video/*"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) pickFile(f);
                  e.target.value = "";
                }}
              />
            </div>
          )}
        </div>
      ) : null}
      {error ? <p className="text-[12px] text-red-600 dark:text-red-400">{error}</p> : null}
    </Card>
  );
}
