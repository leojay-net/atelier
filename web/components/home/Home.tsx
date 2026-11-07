"use client";

import { useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import clsx from "clsx";
import {
  Clapperboard,
  Film,
  CircleHelp,
  GraduationCap,
  ImagePlay,
  Loader2,
  MoreHorizontal,
  Paperclip,
  Pencil,
  Presentation,
  SendHorizonal,
  Sparkles,
  Trash2,
  UploadCloud,
  type LucideIcon,
} from "lucide-react";
import type { Conversation } from "@/lib/types";
import { useUpload } from "@/hooks/useUpload";
import { contentUrl } from "@/components/media/MediaPreview";
import { readApiError } from "@/lib/errors";
import { RETRY_STORAGE_KEY } from "@/components/jobs/JobDetail";
import { CHATS_CHANGED_EVENT } from "@/components/layout/Sidebar";
import { DeleteChatChoice, deleteChat, renameChat } from "@/components/chats/chatActions";
import { Attachment, VoicePicker } from "@/components/studio/Studio";
import { Tour, TOUR_EVENT } from "@/components/tour/Tour";
import { HOME_TOUR } from "@/components/tour/steps";

const QUICK: { label: string; icon: LucideIcon; prompt: string }[] = [
  { label: "Reel from photos", icon: ImagePlay, prompt: "Make a 15-second reel from my photos with upbeat music" },
  { label: "Birthday video", icon: Sparkles, prompt: "Make a birthday video for " },
  { label: "Tutorial", icon: GraduationCap, prompt: "Make a short tutorial video that explains " },
  { label: "Animated story", icon: Clapperboard, prompt: "Make episode 1 of my animated series introducing " },
  { label: "Slides", icon: Presentation, prompt: "Make a presentation about " },
  { label: "Vertical captions", icon: Film, prompt: "Make my video vertical for TikTok and add captions" },
];

function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function ProjectCard({ chat, onChanged }: { chat: Conversation; onChanged: () => void }) {
  const [mode, setMode] = useState<"idle" | "menu" | "rename" | "confirm">("idle");
  const [name, setName] = useState(chat.title);
  const [error, setError] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const busy = chat.status === "running" || chat.status === "queued";

  useEffect(() => {
    if (mode === "idle") return;
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setMode("idle");
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [mode]);

  async function rename() {
    const title = name.trim();
    if (!title || title === chat.title) return setMode("idle");
    const err = await renameChat(chat.id, title);
    if (err) return setError(err);
    setMode("idle");
    onChanged();
  }
  async function remove(files: boolean): Promise<string> {
    const err = await deleteChat(chat.id, files);
    if (err) return err;
    setMode("idle");
    onChanged();
    return "";
  }

  const p = chat.preview;
  return (
    <div ref={box} className="group relative">
      <Link
        href={`/studio/${chat.id}`}
        className="lift block overflow-hidden rounded-xl border border-border bg-bg shadow-[var(--shadow-card)]"
      >
        <div className="relative aspect-video overflow-hidden bg-bg-inset">
          {p?.media === "video" ? (
            <video src={contentUrl(p.handle)} muted preload="metadata" className="h-full w-full object-cover transition-transform duration-700 ease-[var(--ease-out)] group-hover:scale-[1.04]" />
          ) : p?.media === "image" ? (
            // eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin URL
            <img src={contentUrl(p.handle)} alt="" className="h-full w-full object-cover" />
          ) : (
            <div className="flex h-full items-center justify-center text-fg-tertiary">
              {p?.media === "pptx" ? <Presentation size={26} /> : <Sparkles size={26} />}
            </div>
          )}
          {busy ? (
            <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-black/70 px-2 py-0.5 text-[11px] text-white">
              <Loader2 size={11} className="animate-spin" /> Working…
            </span>
          ) : null}
        </div>
        <div className="px-3.5 py-3">
          <p className="truncate text-[14px] font-medium tracking-[-0.01em] text-fg" title={chat.title}>
            {chat.title}
          </p>
          <p className="text-[12px] text-fg-tertiary">{ago(chat.updated_at)}</p>
        </div>
      </Link>

      <button
        type="button"
        aria-label={`Options for ${chat.title}`}
        onClick={() => {
          setError("");
          setMode(mode === "menu" ? "idle" : "menu");
        }}
        className={clsx(
          "absolute right-2 top-2 rounded-md bg-black/60 p-1 text-white hover:bg-black/80",
          mode === "menu" ? "block" : "hidden group-hover:block focus:block",
        )}
      >
        <MoreHorizontal size={15} />
      </button>

      {mode === "menu" ? (
        <div className="absolute right-2 top-10 z-20 w-40 rounded-md border border-border bg-bg p-1 shadow-dialog">
          <button
            type="button"
            onClick={() => {
              setName(chat.title);
              setMode("rename");
            }}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-[13px] text-fg-secondary hover:bg-bg-inset hover:text-fg"
          >
            <Pencil size={13} /> Rename
          </button>
          <button
            type="button"
            onClick={() => setMode("confirm")}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-[13px] text-status-failed hover:bg-bg-inset"
          >
            <Trash2 size={13} /> Delete
          </button>
        </div>
      ) : null}
      {mode === "rename" ? (
        <div className="absolute inset-x-2 bottom-2 z-20 rounded-md border border-accent bg-bg p-1.5 shadow-dialog">
          <input
            autoFocus
            aria-label="Project name"
            value={name}
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") rename();
              if (e.key === "Escape") setMode("idle");
            }}
            onBlur={rename}
            className="w-full bg-transparent px-1.5 py-1 text-[13px] text-fg outline-none"
          />
          {error ? <p className="px-1.5 text-[11px] text-status-failed">{error}</p> : null}
        </div>
      ) : null}
      {mode === "confirm" ? (
        <div className="absolute inset-x-0 top-10 z-20 rounded-md border border-border bg-bg p-3 shadow-dialog">
          <DeleteChatChoice onDelete={remove} onCancel={() => setMode("idle")} />
        </div>
      ) : null}
    </div>
  );
}

/** The start screen: say what you want to make, or open a project you already have. */
export function Home() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [voice, setVoice] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [projects, setProjects] = useState<Conversation[] | null>(null);
  const { items, upload, remove } = useUpload();
  const fileInput = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const uploading = items.some((i) => i.status === "uploading");
  const ready = items.filter((i) => i.asset).map((i) => i.asset!.handle);

  // "Animate" (Characters) and "Try again" leave a request to start from
  useEffect(() => {
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
    let alive = true;
    const load = () =>
      fetch("/api/proxy/conversations?limit=60")
        .then((r) => (r.ok ? r.json() : { items: [] }))
        .then((d: { items: Conversation[] }) => alive && setProjects(d.items))
        .catch(() => alive && setProjects([]));
    load();
    window.addEventListener(CHATS_CHANGED_EVENT, load);
    return () => {
      alive = false;
      window.removeEventListener(CHATS_CHANGED_EVENT, load);
    };
  }, []);

  async function create() {
    const prompt = text.trim();
    if (!prompt || creating || uploading) return;
    setCreating(true);
    setError(null);
    try {
      const res = await fetch("/api/proxy/jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt, assets: ready, ...(voice ? { voice } : {}) }),
      });
      if (!res.ok) throw new Error((await readApiError(res)).message);
      const job: { id: string } = await res.json();
      window.dispatchEvent(new Event(CHATS_CHANGED_EVENT));
      router.push(`/studio/${job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't start. Please try again.");
      setCreating(false);
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) upload(e.dataTransfer.files);
  }

  return (
    <div
      className="space-y-12"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target) setDragging(false);
      }}
      onDrop={onDrop}
    >
      <section>
        <div className="flex items-start justify-between">
          <h1 className="animate-rise text-[26px] font-semibold sm:text-[34px] leading-tight tracking-[-0.03em] text-fg">What do you want to make?</h1>
          <button
            type="button"
            onClick={() => window.dispatchEvent(new Event(TOUR_EVENT))}
            className="inline-flex items-center gap-1.5 rounded-full border border-border-strong px-3 py-1.5 text-[13px] text-fg-secondary hover:bg-bg-inset hover:text-fg"
          >
            <CircleHelp size={14} /> Guide
          </button>
        </div>

        <div
          data-tour="prompt"
          className={clsx(
            "animate-rise mt-6 rounded-2xl border bg-bg p-4 shadow-[var(--shadow-card)] transition-[border-color,box-shadow,background-color] duration-300 focus-within:border-accent focus-within:shadow-[0_0_0_4px_color-mix(in_srgb,var(--color-accent)_16%,transparent),var(--shadow-raised)]",
            dragging ? "border-accent bg-accent-subtle-bg" : "border-border-strong",
          )}
        >
          {items.length ? (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {items.map((i) => (
                <Attachment key={i.id} item={i} onRemove={() => remove(i.id)} />
              ))}
            </div>
          ) : null}
          <textarea
            ref={box}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                create();
              }
            }}
            rows={4}
            aria-label="What do you want to make?"
            placeholder="e.g. a 15-second reel from my photos with upbeat music"
            className="w-full resize-none bg-transparent px-2 py-1.5 text-[16px] leading-relaxed text-fg outline-none placeholder:text-fg-tertiary"
          />
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-tour="attach"
              onClick={() => fileInput.current?.click()}
              aria-label="Add photos, videos or music"
              className="rounded-full p-2 text-fg-secondary hover:bg-bg-inset hover:text-fg"
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
            <div data-tour="voice" className="min-w-0 flex-1 basis-40">
              <VoicePicker value={voice} onChange={setVoice} />
            </div>
            <button
              type="button"
              onClick={create}
              disabled={!text.trim() || creating || uploading}
              className="inline-flex items-center gap-2 rounded-full bg-accent px-5 py-2.5 text-[14px] font-semibold text-on-accent shadow-[0_1px_2px_rgba(0,0,0,0.16)] transition-[background-color,opacity,transform] hover:bg-accent-hover disabled:opacity-35"
            >
              {creating ? <Loader2 size={15} className="animate-spin" /> : <SendHorizonal size={15} />}
              {uploading ? "Uploading…" : "Create"}
            </button>
          </div>
        </div>
        {error ? <p className="mt-2 text-[13px] text-status-failed">{error}</p> : null}

        <div data-tour="quick" className="stagger mt-5 flex flex-wrap items-center gap-2">
          <span className="text-[13px] text-fg-secondary">Quick start:</span>
          {QUICK.map(({ label, icon: Icon, prompt }) => (
            <button
              key={label}
              type="button"
              onClick={() => {
                setText(prompt);
                box.current?.focus();
              }}
              className="inline-flex items-center gap-1.5 rounded-full border border-border-strong bg-bg px-3.5 py-1.5 text-[13px] text-fg-secondary hover:border-accent hover:bg-accent-subtle-bg hover:text-accent"
            >
              <Icon size={14} /> {label}
            </button>
          ))}
        </div>
      </section>

      <section data-tour="projects">
        <h2 className="mb-4 text-[20px] font-semibold tracking-[-0.02em] text-fg">Your projects</h2>
        {projects === null ? (
          <div className="grid grid-cols-2 gap-5 md:grid-cols-3 lg:grid-cols-4">
            {[0, 1, 2, 3].map((n) => (
              <div key={n} className="skeleton aspect-[4/3] rounded-xl" />
            ))}
          </div>
        ) : projects.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border-strong px-6 py-12 text-center text-[14px] text-fg-secondary">
            Nothing here yet. Describe a video above and your first project will appear.
          </div>
        ) : (
          <div className="stagger grid grid-cols-2 gap-5 md:grid-cols-3 lg:grid-cols-4">
            {projects.map((c) => (
              <ProjectCard
                key={c.id}
                chat={c}
                onChanged={() => window.dispatchEvent(new Event(CHATS_CHANGED_EVENT))}
              />
            ))}
          </div>
        )}
      </section>

      {dragging ? (
        <div className="pointer-events-none fixed inset-3 z-30 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-accent bg-accent/10 text-accent backdrop-blur-sm">
          <UploadCloud size={36} />
          <p className="text-lg font-semibold">Drop your files here</p>
        </div>
      ) : null}

      <Tour id="home" steps={HOME_TOUR} />
    </div>
  );
}
