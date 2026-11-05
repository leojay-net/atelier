"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";
import clsx from "clsx";
import {
  CheckCircle2,
  ChevronDown,
  Circle,
  Download,
  Loader2,
  Mic,
  Paperclip,
  Plus,
  Presentation,
  SendHorizonal,
  Sparkles,
  UploadCloud,
  X,
  XCircle,
} from "lucide-react";
import type { Job, JobEvent, JobOutput, MediaType, VoiceAccent } from "@/lib/types";
import { isTerminal } from "@/lib/types";
import { useJobLive } from "@/hooks/useJobLive";
import { useUpload, type UploadItem } from "@/hooks/useUpload";
import { contentUrl } from "@/components/media/MediaPreview";
import { activityLines, friendlyGroup } from "@/lib/activity";
import { readApiError } from "@/lib/errors";
import { RETRY_STORAGE_KEY } from "@/components/jobs/JobDetail";
import { CHATS_CHANGED_EVENT, NEW_CHAT_EVENT } from "@/components/layout/Sidebar";

const EXAMPLES = [
  "Make a short reel from my photos with this music",
  "Turn my pictures into a video with a voiceover that sounds human",
  "Make this video vertical for TikTok and add captions",
  "Join my clips together with smooth transitions",
];

interface AssetInfo {
  handle: string;
  media: MediaType;
  filename: string;
}

// -- small helpers ------------------------------------------------------------------------------

const assetCache = new Map<string, Promise<AssetInfo | null>>();

function loadAsset(handle: string): Promise<AssetInfo | null> {
  if (!assetCache.has(handle)) {
    assetCache.set(
      handle,
      fetch(`/api/proxy/assets/${encodeURIComponent(handle)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((a) => (a ? { handle: a.handle, media: a.media, filename: a.filename } : null))
        .catch(() => null),
    );
  }
  return assetCache.get(handle)!;
}

function useAssets(handles: string[]): Record<string, AssetInfo> {
  const [found, setFound] = useState<Record<string, AssetInfo>>({});
  const key = handles.join(",");
  useEffect(() => {
    let alive = true;
    for (const h of key ? key.split(",") : []) {
      loadAsset(h).then((a) => {
        if (alive && a) setFound((cur) => (cur[h] ? cur : { ...cur, [h]: a }));
      });
    }
    return () => {
      alive = false;
    };
  }, [key]);
  return found;
}

function outputsOf(job: Job | undefined): JobOutput[] {
  return job?.result?.outputs ? Object.values(job.result.outputs) : [];
}

/** "Vertical video · 0:07" instead of the engine's technical summary line. */
function friendlyMedia(o: JobOutput): string {
  const m = o.summary.match(/(\d+)x(\d+)/);
  const dur = o.summary.match(/([\d.]+)s\b/);
  const shape = m
    ? Number(m[1]) > Number(m[2]) * 1.1
      ? "Landscape"
      : Number(m[2]) > Number(m[1]) * 1.1
        ? "Vertical"
        : "Square"
    : "";
  const kind =
    o.media === "audio"
      ? "audio"
      : o.media === "image"
        ? "picture"
        : o.media === "pptx"
          ? "presentation"
          : o.media === "pdf"
            ? "PDF"
            : o.media;
  const parts = [shape ? `${shape} ${kind}` : kind.charAt(0).toUpperCase() + kind.slice(1)];
  if (dur) {
    const total = Math.round(Number(dur[1]));
    parts.push(`${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`);
  }
  parts.push(o.size > 1e6 ? `${(o.size / 1e6).toFixed(1)} MB` : `${Math.round(o.size / 1e3)} KB`);
  return parts.join(" · ");
}

function seconds(job: Job): string {
  if (!job.started_at || !job.finished_at) return "";
  const s = (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000;
  return s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

// -- the live job bridge ------------------------------------------------------------------------

/** Follows one running job (SSE, or polling) and reports every change up to the studio. */
function LiveJob({
  job,
  onChange,
}: {
  job: Job;
  onChange: (job: Job, events: JobEvent[]) => void;
}) {
  const live = useJobLive(job.id, job);
  useEffect(() => {
    onChange(live.job, live.events);
  }, [live.job, live.events, onChange]);
  return null;
}

// -- the stage (left): what you are making ------------------------------------------------------

function Stage({
  thread,
  working,
  activity,
  assets,
  pick,
  picked,
}: {
  thread: Job[];
  working: boolean;
  activity: ReturnType<typeof activityLines>;
  assets: Record<string, AssetInfo>;
  pick: (handle: string) => void;
  picked: string | null;
}) {
  const allOutputs = thread.flatMap((j) => outputsOf(j));
  const latest = [...thread].reverse().find((j) => outputsOf(j).length > 0);
  const shown =
    allOutputs.find((o) => o.handle === picked) ?? (latest ? outputsOf(latest)[0] : undefined);
  const inputs = Array.from(new Set(thread.flatMap((j) => j.assets)));
  const current = activity.filter((l) => l.state === "doing").at(-1);

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto bg-[#0e0f12] p-4 md:p-6">
      <div className="relative flex min-h-[260px] flex-1 items-center justify-center overflow-hidden rounded-lg border border-white/10 bg-black">
        {shown ? (
          shown.media === "video" ? (
            <video key={shown.handle} src={contentUrl(shown.handle)} controls className="max-h-full max-w-full" />
          ) : shown.media === "image" ? (
            // eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin URL
            <img src={contentUrl(shown.handle)} alt="Your result" className="max-h-full max-w-full object-contain" />
          ) : shown.media === "audio" ? (
            <audio src={contentUrl(shown.handle)} controls className="w-3/4" />
          ) : (
            <div className="flex flex-col items-center gap-3 text-white/80">
              {shown.media === "pptx" ? <Presentation size={44} /> : <Sparkles size={40} />}
              <p className="text-[15px] font-medium">
                {shown.media === "pptx" ? "Your presentation is ready" : "Your file is ready"}
              </p>
              <p className="text-[12px] text-white/50">Download it below to open it.</p>
            </div>
          )
        ) : (
          <div className="flex flex-col items-center gap-3 px-6 text-center text-white/60">
            <Sparkles size={30} />
            <p className="text-[15px] font-medium text-white/80">Your video will appear here</p>
            <p className="max-w-sm text-[13px]">
              Add your photos, videos or music, then tell me what you want in the chat.
            </p>
          </div>
        )}
        {working ? (
          <div className="absolute inset-x-0 bottom-0 flex items-center gap-3 bg-black/75 px-4 py-3 text-[13px] text-white backdrop-blur">
            <Loader2 size={16} className="animate-spin" />
            <span className="truncate">{current?.text ?? "Working on it"}…</span>
          </div>
        ) : null}
      </div>

      {shown ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[13px] text-white/70">{friendlyMedia(shown)}</p>
          <a
            href={contentUrl(shown.handle)}
            download={`atelier-${shown.handle.slice(6, 14)}${shown.ext}`}
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-on-accent hover:bg-accent-hover"
          >
            <Download size={16} />
            Download
          </a>
        </div>
      ) : null}

      {inputs.length + allOutputs.length > 0 ? (
        <div>
          <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-white/40">
            In this project
          </p>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {[...allOutputs.map((o, n) => ({ handle: o.handle, media: o.media, label: "Result", tag: `V${n + 1}` })),
              ...inputs.map((h) => ({ handle: h, media: assets[h]?.media, label: assets[h]?.filename ?? "file", tag: "" }))].map(
              (f) => (
                <button
                  key={f.handle + f.label}
                  type="button"
                  onClick={() => f.label === "Result" && pick(f.handle)}
                  title={f.label}
                  className={clsx(
                    "relative h-16 w-24 shrink-0 overflow-hidden rounded-md border bg-white/5",
                    f.handle === shown?.handle ? "border-accent" : "border-white/10",
                    f.label === "Result" ? "cursor-pointer" : "cursor-default",
                  )}
                >
                  {f.media === "image" ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={contentUrl(f.handle)} alt="" className="h-full w-full object-cover" />
                  ) : f.media === "video" ? (
                    <video src={contentUrl(f.handle)} muted preload="metadata" className="h-full w-full object-cover" />
                  ) : (
                    <span className="flex h-full items-center justify-center px-1 text-[10px] text-white/60">
                      {f.media === "audio" ? "♪ " : ""}
                      {f.label}
                    </span>
                  )}
                  {f.label === "Result" ? (
                    <span className="absolute left-1 top-1 rounded bg-accent px-1 text-[9px] font-semibold text-on-accent">
                      {f.tag}
                    </span>
                  ) : null}
                </button>
              ),
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}

// -- the chat (right) --------------------------------------------------------------------------

function Activity({ lines }: { lines: ReturnType<typeof activityLines> }) {
  const [open, setOpen] = useState(false);
  if (!lines.length) return null;
  return (
    <div className="space-y-1">
      {lines.map((l) => (
        <div key={l.key} className="flex items-start gap-2 text-[13px]">
          {l.state === "doing" ? (
            <Loader2 size={14} className="mt-0.5 shrink-0 animate-spin text-accent" />
          ) : l.state === "failed" ? (
            <XCircle size={14} className="mt-0.5 shrink-0 text-status-failed" />
          ) : (
            <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-status-succeeded" />
          )}
          <span className="min-w-0">
            <span className="text-fg">{l.text}</span>
            {open && l.detail ? (
              <span className="block truncate font-mono text-[11px] text-fg-tertiary">{l.detail}</span>
            ) : null}
          </span>
        </div>
      ))}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-[11px] text-fg-tertiary hover:text-fg-secondary"
      >
        <ChevronDown size={12} className={clsx("transition-transform", open && "rotate-180")} />
        {open ? "Hide details" : "Show details"}
      </button>
    </div>
  );
}

function Bubble({ who, children }: { who: "you" | "ai"; children: React.ReactNode }) {
  return (
    <div className={clsx("flex", who === "you" ? "justify-end" : "justify-start")}>
      <div
        className={clsx(
          "max-w-[88%] space-y-2 rounded-2xl px-3.5 py-2.5 text-[14px] leading-relaxed",
          who === "you" ? "rounded-br-sm bg-accent text-on-accent" : "rounded-bl-sm bg-bg-inset text-fg",
        )}
      >
        {children}
      </div>
    </div>
  );
}

function Turn({
  job,
  events,
  live,
  assets,
  onShow,
  onRetry,
}: {
  job: Job;
  events: JobEvent[];
  live: boolean;
  assets: Record<string, AssetInfo>;
  onShow: (handle: string) => void;
  onRetry: (prompt: string) => void;
}) {
  const done = isTerminal(job.status);
  const outputs = outputsOf(job);
  const steps = job.result?.steps ?? [];
  const lines = live ? activityLines(events, done) : [];
  return (
    <div className="space-y-3">
      <Bubble who="you">
        <p className="whitespace-pre-wrap break-words">{job.prompt}</p>
        {job.assets.length ? (
          <div className="flex flex-wrap gap-1">
            {job.assets.map((h) => (
              <span key={h} className="inline-flex max-w-[180px] items-center gap-1 rounded-full bg-white/20 px-2 py-0.5 text-[11px]">
                <Paperclip size={10} />
                <span className="truncate">{assets[h]?.filename ?? "file"}</span>
              </span>
            ))}
          </div>
        ) : null}
      </Bubble>

      <Bubble who="ai">
        {job.status === "queued" ? (
          <p className="flex items-center gap-2 text-fg-secondary">
            <Loader2 size={14} className="animate-spin" /> Getting ready…
          </p>
        ) : null}
        {!done || (live && lines.length) ? <Activity lines={lines} /> : null}

        {job.status === "needs_input" ? (
          <p className="whitespace-pre-wrap">{job.result?.question}</p>
        ) : null}

        {job.status === "succeeded" || job.status === "checks_failed" ? (
          <>
            <p className="whitespace-pre-wrap">{job.result?.summary || "Done! Here it is."}</p>
            {outputs.map((o) => (
              <button
                key={o.handle}
                type="button"
                onClick={() => onShow(o.handle)}
                className="flex w-full items-center gap-2 rounded-lg border border-border bg-bg px-3 py-2 text-left text-[13px] hover:border-accent"
              >
                <Sparkles size={14} className="text-accent" />
                <span className="flex-1">
                  Your {o.media === "pptx" ? "presentation" : o.media === "image" ? "picture" : o.media === "pdf" ? "PDF" : o.media}{" "}
                  is ready — show it
                </span>
              </button>
            ))}
            {job.result?.warnings?.length ? (
              <details className="text-[12px] text-fg-secondary">
                <summary className="cursor-pointer">Notes ({job.result.warnings.length})</summary>
                <ul className="mt-1 list-disc space-y-1 pl-4">
                  {job.result.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </details>
            ) : null}
            {job.result?.credits?.length ? (
              <details className="text-[12px] text-fg-secondary">
                <summary className="cursor-pointer">Photo credits ({job.result.credits.length})</summary>
                <ul className="mt-1 list-disc space-y-1 pl-4">
                  {job.result.credits.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </details>
            ) : null}
            {!live && steps.length ? (
              <p className="text-[11px] text-fg-tertiary">
                {[...new Set(steps.map((s) => friendlyGroup([...(s.fused_ops ?? []), s.op])))].join(" · ")}
                {seconds(job) ? ` · ${seconds(job)}` : ""}
              </p>
            ) : null}
          </>
        ) : null}

        {job.status === "failed" || job.status === "cancelled" ? (
          <div className="space-y-2">
            <p>
              Sorry, that didn&apos;t work.{" "}
              <span className="text-fg-secondary">{job.error || "Something went wrong on my side."}</span>
            </p>
            {job.prompt ? (
              <button
                type="button"
                onClick={() => onRetry(job.prompt)}
                className="text-[13px] font-medium text-accent hover:underline"
              >
                Try again
              </button>
            ) : null}
          </div>
        ) : null}
      </Bubble>
    </div>
  );
}

function Attachment({ item, onRemove }: { item: UploadItem; onRemove: () => void }) {
  return (
    <span
      className={clsx(
        "inline-flex max-w-[170px] items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px]",
        item.status === "error" ? "border-status-failed text-status-failed" : "border-border-strong text-fg",
      )}
      title={item.error ?? item.file.name}
    >
      {item.status === "uploading" ? <Loader2 size={12} className="animate-spin" /> : <Paperclip size={12} />}
      <span className="truncate">
        {item.status === "error" ? `${item.file.name}: ${item.error}` : item.file.name}
      </span>
      {item.status === "uploading" ? (
        <span className="text-fg-tertiary">{Math.round(item.progress * 100)}%</span>
      ) : null}
      <button type="button" onClick={onRemove} aria-label={`Remove ${item.file.name}`} className="text-fg-tertiary hover:text-fg">
        <X size={12} />
      </button>
    </span>
  );
}

// -- the studio --------------------------------------------------------------------------------


/** The voiceover voice for this chat: an accent, as a woman's or a man's voice. "" lets the AI
 * choose (it matches the request, else an American woman). Kept for the rest of the chat. */
function VoicePicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [accents, setAccents] = useState<VoiceAccent[]>([]);
  useEffect(() => {
    let alive = true;
    fetch("/api/proxy/voices")
      .then((r) => (r.ok ? r.json() : { accents: [] }))
      .then((d: { accents: VoiceAccent[] }) => alive && setAccents(d.accents))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  if (!accents.length) return null;
  return (
    <label className="flex items-center gap-1.5 text-[12px] text-fg-secondary">
      <Mic size={13} className="opacity-70" />
      <span>Voice</span>
      <select
        aria-label="Voice for voiceovers"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-border bg-bg px-1.5 py-0.5 text-[12px] text-fg outline-none focus:border-accent"
      >
        <option value="">Let the AI choose</option>
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
    </label>
  );
}

export function Studio({ initialThread }: { initialThread: Job[] }) {
  const [thread, setThread] = useState<Job[]>(initialThread);
  const [voice, setVoice] = useState<string>(initialThread.at(-1)?.voice ?? "");
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const { items, upload, remove } = useUpload();
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const last = thread.at(-1);
  const working = Boolean(last && !isTerminal(last.status));
  const uploading = items.some((i) => i.status === "uploading");
  const ready = items.filter((i) => i.asset).map((i) => i.asset!.handle);
  const asking = last?.status === "needs_input";

  const handles = useMemo(() => Array.from(new Set(thread.flatMap((j) => j.assets))), [thread]);
  const title =
    [...thread].reverse().find((j) => j.result?.title)?.result?.title ??
    (thread[0]?.prompt ? thread[0].prompt.split(/\s+/).slice(0, 7).join(" ") : "New chat");
  const assets = useAssets(handles);

  const onLive = useCallback((job: Job, evs: JobEvent[]) => {
    setThread((cur) => cur.map((j) => (j.id === job.id ? job : j)));
    setEvents(evs);
    if (isTerminal(job.status)) window.dispatchEvent(new Event(CHATS_CHANGED_EVENT));
  }, []);

  useEffect(() => {
    // "Try again" on a job page lands here with that request to resend (one time).
    const raw = sessionStorage.getItem(RETRY_STORAGE_KEY);
    if (!raw) return;
    sessionStorage.removeItem(RETRY_STORAGE_KEY);
    try {
      const retry = JSON.parse(raw) as { prompt?: string };
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time prefill on mount
      if (retry.prompt) setText(retry.prompt);
    } catch {
      // ignore a corrupted payload
    }
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [thread.length, events.length, last?.status]);

  async function send(message?: string) {
    const prompt = (message ?? text).trim();
    if (!prompt || sending || working || uploading) return;
    setSending(true);
    setError(null);
    try {
      const res = await fetch("/api/proxy/jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          prompt,
          assets: ready,
          ...(last ? { parent: last.id } : {}),
          ...(voice ? { voice } : {}),
        }),
      });
      if (!res.ok) throw new Error((await readApiError(res)).message);
      const job: Job = await res.json();
      setThread((cur) => [...cur, job]);
      setEvents([]);
      window.dispatchEvent(new Event(CHATS_CHANGED_EVENT));
      setPicked(null);
      setText("");
      items.forEach((i) => remove(i.id));
      window.history.replaceState(null, "", `/studio/${job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't send. Please try again.");
    } finally {
      setSending(false);
    }
  }

  function startOver() {
    setThread([]);
    setVoice("");
    setEvents([]);
    setPicked(null);
    setText("");
    setError(null);
    items.forEach((i) => remove(i.id));
    window.history.replaceState(null, "", "/");
  }

  useEffect(() => {
    window.addEventListener(NEW_CHAT_EVENT, startOver);
    return () => window.removeEventListener(NEW_CHAT_EVENT, startOver);
  });

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) upload(e.dataTransfer.files);
  }

  const placeholder = asking
    ? "Type your answer…"
    : thread.length
      ? "Ask for a change…"
      : "What should I make?";

  return (
    <div
      className="fixed inset-y-0 left-56 right-0 z-10 flex flex-col md:flex-row"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target) setDragging(false);
      }}
      onDrop={onDrop}
    >
      {last && !isTerminal(last.status) ? <LiveJob key={last.id} job={last} onChange={onLive} /> : null}

      <Stage
        thread={thread}
        working={working}
        activity={activityLines(events, !working)}
        assets={assets}
        pick={setPicked}
        picked={picked}
      />

      <aside className="flex h-[55vh] w-full shrink-0 flex-col border-l border-border bg-bg md:h-full md:w-[420px]">
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <p className="max-w-[260px] truncate text-[15px] font-semibold text-fg" title={title}>
              {title}
            </p>
            <p className="text-[12px] text-fg-tertiary">Your AI video editor</p>
          </div>
          <button
            type="button"
            onClick={startOver}
            className="inline-flex items-center gap-1 rounded-md border border-border-strong px-2.5 py-1.5 text-[12px] text-fg hover:bg-bg-subtle"
          >
            <Plus size={13} /> New video
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          {thread.length === 0 ? (
            <div className="space-y-4">
              <Bubble who="ai">
                <p>
                  Hi! 👋 Add your photos, videos or music (drag them here or tap the 📎), then tell
                  me what you&apos;d like. You can also paste a link to a song.
                </p>
              </Bubble>
              <div className="flex flex-wrap gap-1.5">
                {EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    onClick={() => setText(ex)}
                    className="rounded-full border border-border-strong px-3 py-1.5 text-left text-[12px] text-fg-secondary hover:border-accent hover:text-fg"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {thread.map((job) => (
            <Turn
              key={job.id}
              job={job}
              events={job.id === last?.id ? events : []}
              live={job.id === last?.id && events.length > 0}
              assets={assets}
              onShow={setPicked}
              onRetry={(p) => send(p)}
            />
          ))}
          <div ref={bottom} />
        </div>

        <div className="space-y-2 border-t border-border p-3">
          {items.length ? (
            <div className="flex flex-wrap gap-1.5">
              {items.map((i) => (
                <Attachment key={i.id} item={i} onRemove={() => remove(i.id)} />
              ))}
            </div>
          ) : null}
          {error ? <p className="text-[12px] text-status-failed">{error}</p> : null}
          {working ? (
            <p className="flex items-center gap-1.5 text-[12px] text-fg-tertiary">
              <Circle size={8} className="animate-pulse fill-accent text-accent" />
              I&apos;m working on it. You can type your next idea while you wait.
            </p>
          ) : null}
          <div className="flex items-end gap-2 rounded-xl border border-border-strong bg-bg px-2 py-1.5 focus-within:border-accent">
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              aria-label="Add photos, videos or music"
              className="mb-0.5 rounded-md p-1.5 text-fg-secondary hover:bg-bg-subtle hover:text-fg"
            >
              <Paperclip size={18} />
            </button>
            <input
              ref={fileInput}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files?.length) upload(e.target.files);
                e.target.value = "";
              }}
            />
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              rows={Math.min(5, Math.max(1, text.split("\n").length))}
              placeholder={placeholder}
              aria-label="Message"
              className="max-h-40 flex-1 resize-none bg-transparent py-1.5 text-[14px] text-fg outline-none placeholder:text-fg-tertiary"
            />
            <button
              type="button"
              onClick={() => send()}
              disabled={!text.trim() || sending || working || uploading}
              aria-label="Send"
              className="mb-0.5 rounded-lg bg-accent p-2 text-on-accent transition-opacity disabled:opacity-30"
            >
              {sending ? <Loader2 size={16} className="animate-spin" /> : <SendHorizonal size={16} />}
            </button>
          </div>
          <VoicePicker value={voice} onChange={setVoice} />
          {uploading ? <p className="text-[11px] text-fg-tertiary">Uploading… you can send once it finishes.</p> : null}
        </div>
      </aside>

      {dragging ? (
        <div className="pointer-events-none absolute inset-3 z-20 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-accent bg-accent/10 text-accent backdrop-blur-sm">
          <UploadCloud size={36} />
          <p className="text-lg font-semibold">Drop your files here</p>
        </div>
      ) : null}
    </div>
  );
}
