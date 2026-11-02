"use client";

import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { UploadCloud, X } from "lucide-react";
import clsx from "clsx";
import type { UploadItem } from "@/hooks/useUpload";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { formatBytes } from "@/lib/format";

interface DropzoneProps {
  items: UploadItem[];
  onFiles: (files: FileList) => void;
  onRemove: (id: string) => void;
  maxFiles?: number;
}

export function Dropzone({ items, onFiles, onRemove, maxFiles = 20 }: DropzoneProps) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const atLimit = items.length >= maxFiles;

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) onFiles(e.dataTransfer.files);
  }

  return (
    <div className="space-y-2">
      <div
        onClick={() => !atLimit && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          if (!atLimit) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={atLimit ? undefined : handleDrop}
        role="button"
        tabIndex={atLimit ? -1 : 0}
        aria-disabled={atLimit}
        onKeyDown={(e) => {
          if (!atLimit && (e.key === "Enter" || e.key === " ")) inputRef.current?.click();
        }}
        className={clsx(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed px-6 py-10 text-center transition-colors",
          atLimit && "cursor-not-allowed opacity-50",
          dragging
            ? "border-accent bg-accent-subtle-bg"
            : "border-border-strong hover:border-fg-tertiary",
        )}
      >
        <UploadCloud size={28} className={dragging ? "text-accent" : "text-fg-tertiary"} />
        <p className={clsx("text-sm font-medium", dragging ? "text-accent" : "text-fg")}>
          Drag files here or click to browse
        </p>
        <p className="text-[13px] text-fg-tertiary">
          Images, video, audio, PDF, Word, Excel, PowerPoint
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files?.length) onFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {items.length > 0 ? (
        <ul className="space-y-1.5">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex items-center gap-3 rounded-sm border border-border px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13px] text-fg">{item.file.name}</span>
                  <span className="shrink-0 text-[12px] text-fg-tertiary">
                    {formatBytes(item.file.size)}
                  </span>
                </div>
                {item.status === "uploading" ? (
                  <div className="mt-1.5">
                    <ProgressBar value={item.progress} />
                  </div>
                ) : item.status === "error" ? (
                  <p className="mt-0.5 text-[12px] text-status-failed">{item.error}</p>
                ) : null}
              </div>
              <button
                type="button"
                aria-label="Remove"
                onClick={() => onRemove(item.id)}
                className="shrink-0 rounded-sm p-1 text-fg-tertiary hover:bg-bg-subtle hover:text-status-failed"
              >
                <X size={14} />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
