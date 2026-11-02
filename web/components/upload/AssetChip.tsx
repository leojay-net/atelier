"use client";

import { useState } from "react";
import { X, Check, Copy, Image as ImageIcon, Video, Music, FileText } from "lucide-react";
import type { MediaType } from "@/lib/types";
import { formatBytes } from "@/lib/format";

const ICONS: Record<MediaType, typeof ImageIcon> = {
  image: ImageIcon,
  video: Video,
  audio: Music,
  document: FileText,
  text: FileText,
};

interface AssetChipProps {
  filename: string;
  media: MediaType;
  size: number;
  handle: string;
  onRemove?: () => void;
}

export function AssetChip({ filename, media, size, handle, onRemove }: AssetChipProps) {
  const [copied, setCopied] = useState(false);
  const Icon = ICONS[media] ?? FileText;

  return (
    <div className="flex items-center gap-2 rounded-sm border border-border bg-bg-subtle py-1.5 pl-2.5 pr-1.5 text-[13px]">
      <Icon size={14} className="shrink-0 text-fg-tertiary" />
      <span className="max-w-[160px] truncate text-fg">{filename}</span>
      <span className="text-fg-tertiary">{formatBytes(size)}</span>
      <button
        type="button"
        title="Copy asset handle"
        onClick={() => {
          navigator.clipboard.writeText(handle).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          });
        }}
        className="rounded-sm p-1 text-fg-tertiary hover:bg-bg-inset hover:text-fg"
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
      {onRemove ? (
        <button
          type="button"
          title="Remove"
          onClick={onRemove}
          className="rounded-sm p-1 text-fg-tertiary hover:bg-bg-inset hover:text-status-failed"
        >
          <X size={12} />
        </button>
      ) : null}
    </div>
  );
}
