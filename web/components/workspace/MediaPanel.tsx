"use client";

import clsx from "clsx";
import { Plus } from "lucide-react";
import type { JobOutput, MediaType } from "@/lib/types";
import { contentUrl } from "@/components/media/MediaPreview";

export interface FileInfo {
  handle: string;
  media: MediaType | undefined;
  filename: string;
}

function Thumb({ handle, media, label }: { handle: string; media: MediaType | undefined; label: string }) {
  if (media === "image")
    // eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin URL
    return <img src={contentUrl(handle)} alt="" className="h-full w-full object-cover" />;
  if (media === "video")
    return <video src={contentUrl(handle)} muted preload="metadata" className="h-full w-full object-cover" />;
  return (
    <span className="flex h-full items-center justify-center px-2 text-center text-[11px] text-fg-tertiary">
      {media === "audio" ? "♪ " : ""}
      {label}
    </span>
  );
}

/** Left "Media" tab: every version the project has (click one to open it) and every file. */
export function MediaPanel({
  versions,
  files,
  shown,
  onPick,
  onAdd,
}: {
  versions: JobOutput[];
  files: FileInfo[];
  shown: string | undefined;
  onPick: (handle: string) => void;
  onAdd: () => void;
}) {
  return (
    <div className="space-y-5">
      <section>
        <div className="mb-2 flex items-center justify-between">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-fg-tertiary">Versions</p>
        </div>
        {versions.length ? (
          <div className="grid grid-cols-2 gap-2">
            {versions.map((o, n) => (
              <button
                key={o.handle}
                type="button"
                onClick={() => onPick(o.handle)}
                className={clsx(
                  "group relative aspect-video overflow-hidden rounded-md bg-bg-subtle ring-1 transition",
                  o.handle === shown ? "ring-2 ring-accent" : "ring-border hover:ring-border-strong",
                )}
              >
                <Thumb handle={o.handle} media={o.media} label={o.media} />
                <span className="absolute bottom-1 left-1 rounded bg-black/70 px-1.5 py-px text-[10px] font-medium text-white">
                  V{n + 1}
                </span>
              </button>
            ))}
          </div>
        ) : (
          <p className="text-fg-tertiary">Versions you make show up here. Click one to open it.</p>
        )}
      </section>

      <section>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg-tertiary">Your files</p>
        <div className="grid grid-cols-2 gap-2">
          {files.map((f) => (
            <div
              key={f.handle}
              title={f.filename}
              className="relative aspect-video overflow-hidden rounded-md bg-bg-subtle ring-1 ring-border"
            >
              <Thumb handle={f.handle} media={f.media} label={f.filename} />
            </div>
          ))}
          <button
            type="button"
            onClick={onAdd}
            className="flex aspect-video flex-col items-center justify-center gap-1 rounded-md border border-dashed border-border-strong text-[12px] text-fg-secondary transition-colors hover:border-accent hover:text-accent"
          >
            <Plus size={16} /> Add files
          </button>
        </div>
        <p className="mt-2 text-[12px] text-fg-tertiary">Added files go with your next message to the assistant.</p>
      </section>
    </div>
  );
}
