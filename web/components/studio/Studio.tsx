"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import {
  ArrowLeft,
  AudioLines,
  CheckCircle2,
  ChevronDown,
  Circle,
  CircleHelp,
  Download,
  FolderOpen,
  Loader2,
  Mic,
  Music,
  Paperclip,
  PanelLeft,
  Maximize2,
  Minimize2,
  PanelRight,
  Pencil,
  Save,
  SendHorizonal,
  SlidersHorizontal,
  Smile,
  Sparkles,
  Type,
  Wand2,
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
import { CHATS_CHANGED_EVENT } from "@/components/layout/Sidebar";
import { renameChat } from "@/components/chats/chatActions";
import { useEdit } from "@/components/editor/useEdit";
import { EditPlayer } from "@/components/editor/EditPlayer";
import { EditTimeline } from "@/components/editor/EditTimeline";
import {
  AudioPanel,
  EditFileInput,
  EffectsPanel,
  FiltersPanel,
  PropertiesPanel,
  StickersPanel,
  TextPanel,
} from "@/components/editor/EditPanels";
import { History } from "@/components/workspace/History";
import { MediaPanel } from "@/components/workspace/MediaPanel";
import { Viewer, WorkingBar } from "@/components/workspace/Viewer";
import { Splitter, usePanelWidth } from "@/components/workspace/Panels";
import { Tour, TOUR_EVENT } from "@/components/tour/Tour";
import { STUDIO_TOUR } from "@/components/tour/steps";

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
          who === "you" ? "rounded-br-sm border border-accent/30 bg-accent-subtle-bg text-fg" : "rounded-bl-sm bg-bg-subtle text-fg",
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

export function Attachment({ item, onRemove }: { item: UploadItem; onRemove: () => void }) {
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
export function VoicePicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [accents, setAccents] = useState<VoiceAccent[]>([]);
  const [mine, setMine] = useState(false);
  useEffect(() => {
    let alive = true;
    fetch("/api/proxy/voices")
      .then((r) => (r.ok ? r.json() : { accents: [], mine: false }))
      .then((d: { accents: VoiceAccent[]; mine: boolean }) => {
        if (!alive) return;
        setAccents(d.accents);
        setMine(d.mine);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  if (!accents.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-fg-secondary">
      <label className="flex items-center gap-1.5">
        <Mic size={13} className="opacity-70" />
        <span>Voice</span>
        <select
          aria-label="Voice for voiceovers"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="rounded-md border border-border bg-bg px-1.5 py-0.5 text-[12px] text-fg outline-none focus:border-accent"
        >
          <option value="">Let the AI choose</option>
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
      </label>
      {!mine ? (
        <a href="/settings" className="text-fg-tertiary underline-offset-2 hover:text-fg hover:underline">
          Use your own voice
        </a>
      ) : null}
    </div>
  );
}

// -- the project name, editable in place -------------------------------------------------------

function ProjectName({ title, chatId, onRenamed }: { title: string; chatId: string; onRenamed: (t: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(title);
  async function commit() {
    const next = name.trim();
    setEditing(false);
    if (!next || next === title || !chatId) return;
    if (!(await renameChat(chatId, next))) {
      onRenamed(next);
      window.dispatchEvent(new Event(CHATS_CHANGED_EVENT));
    }
  }
  if (editing)
    return (
      <input
        autoFocus
        aria-label="Project name"
        value={name}
        maxLength={120}
        onChange={(e) => setName(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") setEditing(false);
        }}
        className="w-64 rounded-md border border-accent bg-bg-subtle px-2 py-1 text-[14px] font-semibold text-fg outline-none"
      />
    );
  return (
    <button
      type="button"
      onClick={() => {
        setName(title);
        setEditing(true);
      }}
      title="Rename"
      className="group flex max-w-[280px] items-center gap-1.5 rounded-md px-2 py-1 text-[14px] font-semibold text-fg hover:bg-bg-inset"
    >
      <span className="truncate">{title}</span>
      <Pencil size={12} className="shrink-0 text-fg-tertiary opacity-0 group-hover:opacity-100" />
    </button>
  );
}

function IconBtn({
  label,
  onClick,
  active,
  children,
}: {
  label: string;
  onClick: () => void;
  active?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-pressed={active}
      className={clsx(
        "grid h-8 w-8 place-items-center rounded-md transition-colors hover:bg-bg-inset hover:text-fg",
        active ? "text-fg" : "text-fg-tertiary",
      )}
    >
      {children}
    </button>
  );
}

type ToolTab = "media" | "audio" | "text" | "stickers" | "effects" | "filters";
const TOOL_TABS: { key: ToolTab; label: string; icon: typeof Music; needsVideo: boolean }[] = [
  { key: "media", label: "Media", icon: FolderOpen, needsVideo: false },
  { key: "audio", label: "Audio", icon: Music, needsVideo: true },
  { key: "text", label: "Text", icon: Type, needsVideo: true },
  { key: "stickers", label: "Stickers", icon: Smile, needsVideo: true },
  { key: "effects", label: "Effects", icon: AudioLines, needsVideo: true },
  { key: "filters", label: "Filters", icon: Wand2, needsVideo: true },
];

function TabBar<T extends string>({
  tabs,
  value,
  onChange,
  tour,
}: {
  tabs: { key: T; label: string; icon: typeof Music; disabled?: boolean; hint?: string }[];
  value: T;
  onChange: (k: T) => void;
  tour?: string;
}) {
  return (
    <div role="tablist" data-tour={tour} className="@container flex shrink-0 border-b border-border">
      {tabs.map(({ key, label, icon: Icon, disabled, hint }) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={value === key}
          disabled={disabled}
          title={disabled ? hint : label}
          onClick={() => onChange(key)}
          className={clsx(
            "flex flex-1 flex-col items-center justify-center gap-0.5 border-b-2 px-1 py-2 text-[11px] font-medium @[420px]:flex-row @[420px]:gap-1.5 @[420px]:px-2 @[420px]:py-2.5 @[420px]:text-[12.5px] transition-colors disabled:cursor-not-allowed disabled:opacity-35",
            value === key
              ? "border-accent text-fg"
              : "border-transparent text-fg-tertiary hover:text-fg-secondary disabled:hover:text-fg-tertiary",
          )}
        >
          <Icon size={14} />
          {label}
        </button>
      ))}
    </div>
  );
}

// -- the studio (the workspace of one project) -------------------------------------------------

export function Studio({ initialThread, initialAsset }: { initialThread: Job[]; initialAsset?: string }) {
  const [thread, setThread] = useState<Job[]>(initialThread);
  const [voice, setVoice] = useState<string>(initialThread.at(-1)?.voice ?? "");
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [picked, setPicked] = useState<string | null>(initialAsset ?? null);
  const [tool, setTool] = useState<ToolTab>("media");
  const [side, setSide] = useState<"ai" | "props">("ai");
  const [renamed, setRenamed] = useState<string | null>(null);
  const { items, upload, remove } = useUpload();
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const history = usePanelWidth("history", 232, 180, 480);
  const tools = usePanelWidth("tools", 300, 240, 720);
  const chat = usePanelWidth("chat", 380, 300, 960);
  const pathname = usePathname();

  const last = thread.at(-1);
  const working = Boolean(last && !isTerminal(last.status));
  const uploading = items.some((i) => i.status === "uploading");
  const ready = items.filter((i) => i.asset).map((i) => i.asset!.handle);
  const asking = last?.status === "needs_input";

  const handles = useMemo(() => Array.from(new Set(thread.flatMap((j) => j.assets))), [thread]);
  const autoTitle =
    [...thread].reverse().find((j) => j.result?.title)?.result?.title ??
    (thread[0]?.prompt ? thread[0].prompt.split(/\s+/).slice(0, 7).join(" ") : "New project");
  const title = renamed ?? autoTitle;
  const assets = useAssets(handles);

  // what the player shows: the picked version, else the newest result
  const allOutputs = thread.flatMap((j) => outputsOf(j));
  const latest = [...thread].reverse().find((j) => outputsOf(j).length > 0);
  const shown = allOutputs.find((o) => o.handle === picked) ?? (latest ? outputsOf(latest)[0] : undefined);
  const isVideo = shown?.media === "video";
  // a version split into layers has its sound next to the picture: open it as its own layer
  const sourceJob = thread.find((j) => outputsOf(j).some((o) => o.handle === shown?.handle));
  const soundOut = isVideo ? outputsOf(sourceJob).find((o) => o.media === "audio") : undefined;
  const edit = useEdit(
    last?.id ?? "",
    isVideo && shown ? shown.handle : "",
    soundOut ? { handle: soundOut.handle, name: "Soundtrack" } : null,
  );
  const current = activityLines(events, !working)
    .filter((l) => l.state === "doing")
    .at(-1);
  const files = handles.map((h) => ({
    handle: h,
    media: assets[h]?.media,
    filename: assets[h]?.filename ?? "file",
  }));
  const sideTab = side === "props" && !isVideo ? "ai" : side;

  const onLive = useCallback((job: Job, evs: JobEvent[]) => {
    setThread((cur) => cur.map((j) => (j.id === job.id ? job : j)));
    setEvents(evs);
    if (isTerminal(job.status)) window.dispatchEvent(new Event(CHATS_CHANGED_EVENT));
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [thread.length, events.length, last?.status, sideTab, chat.width]);

  /** A new job (a chat message, or a saved hand edit) joins the project. */
  function adopt(job: Job) {
    setThread((cur) => [...cur, job]);
    setEvents([]);
    setPicked(null);
    window.dispatchEvent(new Event(CHATS_CHANGED_EVENT));
    window.history.replaceState(null, "", `/studio/${job.id}`);
  }

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
      adopt(await res.json());
      setText("");
      items.forEach((i) => remove(i.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't send. Please try again.");
    } finally {
      setSending(false);
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) {
      upload(e.dataTransfer.files);
      setSide("ai");
    }
  }

  function addFiles() {
    setSide("ai");
    if (chat.width === 0) chat.toggle();
    // the picker lives in the assistant panel, which has to be on screen first
    setTimeout(() => fileInput.current?.click(), 0);
  }

  const placeholder = asking ? "Type your answer…" : "Ask for a change…";
  const activeId = pathname.startsWith("/studio/") ? (pathname.split("/")[2] ?? null) : null;
  const lockedHint = "Available for videos";
  const canSave = edit.dirty && isVideo && !working && !edit.saving && !edit.sending && edit.duration > 0;
  const toolTabs = TOOL_TABS.map((t) => ({
    ...t,
    disabled: t.needsVideo && !isVideo,
    hint: lockedHint,
  }));
  const sideTabs = [
    { key: "ai" as const, label: "AI assistant", icon: Sparkles },
    { key: "props" as const, label: "Properties", icon: SlidersHorizontal, disabled: !isVideo, hint: lockedHint },
  ];
  const sideError = edit.error || (edit.failed ? `${edit.failed.file.name}: ${edit.failed.error}` : "");

  return (
    <div
      className="ws fixed inset-0 z-20 flex flex-col"
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
      <EditFileInput edit={edit} />

      {/* top bar */}
      <header className="flex h-12 shrink-0 items-center gap-1 border-b border-border bg-bg px-2">
        <span className="hidden md:block">
          <IconBtn label={history.width ? "Hide history" : "Show history"} onClick={history.toggle} active={history.width > 0}>
            <PanelLeft size={17} />
          </IconBtn>
        </span>
        <Link
          href="/"
          className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-[13px] text-fg-secondary transition-colors hover:bg-bg-inset hover:text-fg"
        >
          <ArrowLeft size={14} /> Projects
        </Link>
        <span className="mx-1 h-5 w-px bg-border" />
        <ProjectName key={title} title={title} chatId={last?.id ?? ""} onRenamed={setRenamed} />
        <div className="ml-auto flex items-center gap-1.5">
          {edit.dirty && isVideo ? (
            <span className="mr-1 hidden text-[12px] text-fg-tertiary lg:inline">Unsaved changes</span>
          ) : null}
          <button
            type="button"
            data-tour="help"
            onClick={() => window.dispatchEvent(new Event(TOUR_EVENT))}
            title="Show the guide"
            className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-[13px] text-fg-secondary transition-colors hover:bg-bg-inset hover:text-fg"
          >
            <CircleHelp size={15} /> <span className="hidden sm:inline">Help</span>
          </button>
          {shown ? (
            <a
              href={contentUrl(shown.handle)}
              download={`atelier-${shown.handle.slice(6, 14)}${shown.ext}`}
              data-tour="download"
              className="flex items-center gap-1.5 rounded-md border border-border-strong px-3 py-1.5 text-[13px] font-medium text-fg transition-colors hover:bg-bg-inset"
            >
              <Download size={14} /> <span className="hidden sm:inline">Download</span>
            </a>
          ) : null}
          {isVideo ? (
            <button
              type="button"
              data-tour="save"
              disabled={!canSave}
              onClick={() => edit.save(adopt)}
              title={canSave ? "Save your edits as a new version" : "Make an edit to save a new version"}
              className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-1.5 text-[13px] font-semibold text-on-accent transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-accent"
            >
              {edit.saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
              {edit.saving ? (edit.makingOnSave ? "Making the sound…" : "Saving…") : "Save version"}
            </button>
          ) : null}
          <span className="hidden md:block">
            <IconBtn
              label={chat.width >= 640 ? "Make the assistant smaller" : "Make the assistant bigger"}
              onClick={() => chat.set(chat.width >= 640 ? 380 : 720)}
            >
              {chat.width >= 640 ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
            </IconBtn>
          </span>
          <span className="hidden md:block">
            <IconBtn label={chat.width ? "Hide assistant" : "Show assistant"} onClick={chat.toggle} active={chat.width > 0}>
              <PanelRight size={17} />
            </IconBtn>
          </span>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto md:flex-row md:overflow-hidden">
        {/* far left: history */}
        {history.width ? (
          <aside
            style={{ "--w": `${history.width}px` } as React.CSSProperties}
            className="hidden shrink-0 overflow-hidden bg-bg md:block md:w-[var(--w)]"
          >
            <History active={activeId} />
          </aside>
        ) : null}
        <Splitter side="left" width={history.width} onChange={history.set} onToggle={history.toggle} label="history" />

        {/* tools: media, audio, text, effects */}
        {tools.width ? (
          <aside
            data-tour="tools"
            style={{ "--w": `${tools.width}px` } as React.CSSProperties}
            className="hidden shrink-0 flex-col overflow-hidden bg-bg md:flex md:w-[var(--w)]"
          >
            <TabBar tabs={toolTabs} value={tool} onChange={setTool} tour="tool-tabs" />
            <div className="min-h-0 flex-1 overflow-y-auto p-3 text-[13px] text-fg">
              {tool === "media" ? (
                <MediaPanel
                  versions={allOutputs}
                  files={files}
                  shown={shown?.handle}
                  onPick={setPicked}
                  onAdd={addFiles}
                />
              ) : (
                <fieldset disabled={working} className="m-0 min-w-0 border-0 p-0">
                  {working ? <p className="mb-3 text-fg-tertiary">You can edit when I&apos;m done.</p> : null}
                  {tool === "audio" ? <AudioPanel edit={edit} onSplit={adopt} /> : null}
                  {tool === "text" ? <TextPanel edit={edit} /> : null}
                  {tool === "stickers" ? <StickersPanel edit={edit} /> : null}
                  {tool === "effects" ? <EffectsPanel edit={edit} /> : null}
                  {tool === "filters" ? <FiltersPanel edit={edit} /> : null}
                </fieldset>
              )}
            </div>
          </aside>
        ) : null}
        <Splitter side="left" width={tools.width} onChange={tools.set} onToggle={tools.toggle} label="tools" />

        {/* centre: the player and, for videos, the timeline */}
        <main data-tour="player" className="flex min-h-[60vh] min-w-0 shrink-0 flex-col bg-[var(--ws-canvas)] md:min-h-0 md:flex-1 md:shrink">
          {sideError || edit.sending ? (
            <p className={clsx("shrink-0 px-4 pt-2 text-[12.5px]", sideError ? "text-status-failed" : "text-fg-secondary")}>
              {sideError || "Uploading your file…"}
            </p>
          ) : null}
          <div className="relative flex min-h-0 flex-1 p-4">
            {isVideo ? <EditPlayer edit={edit} /> : <Viewer shown={shown} />}
            {working ? <WorkingBar text={current?.text ?? "Working on it"} /> : null}
          </div>
          {isVideo ? (
            <EditTimeline edit={edit} locked={working} />
          ) : shown ? (
            <p className="shrink-0 border-t border-border px-4 py-2 text-[12.5px] text-fg-tertiary">{friendlyMedia(shown)}</p>
          ) : null}
        </main>

        <Splitter side="right" width={chat.width} onChange={chat.set} onToggle={chat.toggle} label="assistant" />
        {/* far right: the AI assistant (and the selected clip's properties) */}
        {chat.width ? (
          <aside
            data-tour="assistant"
            style={{ "--w": `${chat.width}px` } as React.CSSProperties}
            className="flex h-[75vh] shrink-0 flex-col overflow-hidden border-t border-border bg-bg md:h-auto md:w-[var(--w)] md:border-t-0"
          >
            <TabBar tabs={sideTabs} value={sideTab} onChange={setSide} />
            {sideTab === "props" ? (
              <div className="min-h-0 flex-1 overflow-y-auto p-4 text-[13px] text-fg">
                <fieldset disabled={working} className="m-0 min-w-0 border-0 p-0">
                  <PropertiesPanel edit={edit} />
                </fieldset>
              </div>
            ) : (
              <>
                <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
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
                  <div className="flex items-end gap-2 rounded-xl border border-border-strong bg-bg-subtle px-2 py-1.5 focus-within:border-accent">
                    <button
                      type="button"
                      onClick={() => fileInput.current?.click()}
                      aria-label="Add photos, videos or music"
                      className="mb-0.5 rounded-md p-1.5 text-fg-secondary hover:bg-bg-inset hover:text-fg"
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
              </>
            )}
          </aside>
        ) : null}
      </div>

      {dragging ? (
        <div className="pointer-events-none absolute inset-3 z-30 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-accent bg-accent/10 text-accent backdrop-blur-sm">
          <UploadCloud size={36} />
          <p className="text-lg font-semibold">Drop your files here</p>
        </div>
      ) : null}

      <Tour id="studio" steps={STUDIO_TOUR} />
    </div>
  );
}
