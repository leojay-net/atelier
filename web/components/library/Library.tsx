"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import {
  Download,
  FileSpreadsheet,
  FileText,
  Loader2,
  MessageSquare,
  Music,
  Presentation,
  Sparkles,
} from "lucide-react";
import type { LibraryItem, LibraryPage, LibrarySection, MediaType } from "@/lib/types";
import { contentUrl } from "@/components/media/MediaPreview";
import { formatDate } from "@/lib/format";
import { useUpload } from "@/hooks/useUpload";
import { Dropzone } from "@/components/upload/Dropzone";

const SECTIONS: { key: LibrarySection; label: string; hint: string }[] = [
  { key: "created", label: "Created", hint: "Videos, pictures and presentations the AI made for you" },
  { key: "uploaded", label: "Uploaded", hint: "Files you added" },
  { key: "extras", label: "Made along the way", hint: "Photos found online, AI pictures and voiceovers used in your creations" },
];

const FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "All" },
  { key: "video", label: "Videos" },
  { key: "image", label: "Images" },
  { key: "document", label: "Presentations & documents" },
  { key: "audio", label: "Audio" },
];

const KIND: Partial<Record<MediaType, string>> = {
  video: "Video",
  image: "Image",
  audio: "Audio",
  pptx: "Presentation",
  pdf: "PDF",
  docx: "Document",
  xlsx: "Spreadsheet",
  text: "Text",
};

function Preview({ item }: { item: LibraryItem }) {
  const src = contentUrl(item.handle);
  if (item.media === "image")
    // eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin URL
    return <img src={src} alt={item.title} loading="lazy" className="h-full w-full object-cover" />;
  if (item.media === "video")
    return (
      <video
        src={src}
        muted
        playsInline
        preload="metadata"
        onMouseEnter={(e) => e.currentTarget.play().catch(() => undefined)}
        onMouseLeave={(e) => {
          e.currentTarget.pause();
          e.currentTarget.currentTime = 0;
        }}
        className="h-full w-full object-cover"
      />
    );
  const Icon =
    item.media === "audio" ? Music : item.media === "pptx" ? Presentation : item.media === "xlsx" ? FileSpreadsheet : FileText;
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-white/70">
      <Icon size={30} />
      <span className="text-[11px] uppercase tracking-wide">{KIND[item.media] ?? item.media}</span>
    </div>
  );
}

function Card({ item, section }: { item: LibraryItem; section: LibrarySection }) {
  return (
    <div className="group flex flex-col overflow-hidden rounded-lg border border-border bg-bg shadow-[var(--shadow-card)]">
      <Link
        href={`/assets/${encodeURIComponent(item.handle)}`}
        className="relative block aspect-video overflow-hidden bg-[#0e0f12]"
      >
        <div className="absolute inset-0">
          <Preview item={item} />
        </div>
        <span className="absolute left-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white">
          {KIND[item.media] ?? item.media}
        </span>
      </Link>
      <div className="flex flex-1 flex-col gap-2 p-3">
        <p className="line-clamp-2 text-[14px] font-medium leading-snug text-fg" title={item.title}>
          {item.title}
        </p>
        <p className="text-[12px] text-fg-tertiary">{formatDate(item.created_at)}</p>
        <div className="mt-auto flex items-center gap-1.5 pt-1">
          <a
            href={contentUrl(item.handle)}
            download={item.filename || `${item.title}${item.ext}`}
            className="inline-flex items-center gap-1.5 rounded-md bg-accent px-2.5 py-1.5 text-[12px] font-medium text-on-accent hover:bg-accent-hover"
          >
            <Download size={13} /> Download
          </a>
          {item.job && section !== "uploaded" ? (
            <Link
              href={`/studio/${item.job}`}
              className="inline-flex items-center gap-1.5 rounded-md border border-border-strong px-2.5 py-1.5 text-[12px] text-fg hover:bg-bg-subtle"
            >
              <MessageSquare size={13} /> Open chat
            </Link>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function LibraryView() {
  const [section, setSection] = useState<LibrarySection>("created");
  const [media, setMedia] = useState("all");
  const [page, setPage] = useState<LibraryPage | null>(null);
  const [shown, setShown] = useState(""); // which section/filter `page` belongs to
  const { items: uploads, upload, remove } = useUpload();
  const finished = uploads.filter((u) => u.status === "done").length;
  const want = `section=${section}&media=${media}`;
  const loading = shown !== want;

  useEffect(() => {
    let alive = true;
    fetch(`/api/proxy/library?${want}&limit=120`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: LibraryPage | null) => {
        if (!alive) return;
        if (data) setPage(data);
        setShown(want);
      })
      .catch(() => alive && setShown(want));
    return () => {
      alive = false;
    };
  }, [want, finished]);

  const counts = page?.counts;
  return (
    <div className="space-y-5">
      <div>
        <h1 className="animate-rise text-[30px] font-semibold leading-tight tracking-[-0.03em] text-fg">Library</h1>
        <p className="mt-1 text-[13px] text-fg-secondary">{SECTIONS.find((s) => s.key === section)?.hint}</p>
      </div>

      <div className="flex flex-wrap items-center gap-1 border-b border-border">
        {SECTIONS.map((s) => (
          <button
            key={s.key}
            type="button"
            onClick={() => setSection(s.key)}
            className={clsx(
              "-mb-px border-b-2 px-3 py-2 text-[14px] font-medium transition-colors",
              section === s.key ? "border-accent text-fg" : "border-transparent text-fg-secondary hover:text-fg",
            )}
          >
            {s.label}
            {counts ? <span className="ml-1.5 text-[12px] text-fg-tertiary">{counts[s.key]}</span> : null}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setMedia(f.key)}
            className={clsx(
              "rounded-full border px-3 py-1 text-[12px] transition-colors",
              media === f.key ? "border-accent bg-accent-subtle-bg text-accent" : "border-border-strong text-fg-secondary hover:text-fg",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {section === "uploaded" ? <Dropzone items={uploads} onFiles={upload} onRemove={remove} /> : null}

      {loading && !page ? (
        <p className="flex items-center gap-2 text-[13px] text-fg-secondary">
          <Loader2 size={14} className="animate-spin" /> Loading…
        </p>
      ) : page && page.items.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border-strong py-14 text-center">
          <Sparkles size={26} className="text-fg-tertiary" />
          <p className="text-[14px] text-fg">
            {section === "created" ? "Nothing created yet" : section === "uploaded" ? "No uploads yet" : "Nothing here yet"}
          </p>
          {section === "created" ? (
            <Link href="/" className="text-[13px] font-medium text-accent hover:underline">
              Make your first video
            </Link>
          ) : null}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {page?.items.map((item) => <Card key={item.handle} item={item} section={section} />)}
        </div>
      )}
    </div>
  );
}
