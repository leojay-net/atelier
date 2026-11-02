"use client";

import { useRef, useState } from "react";
import { Image as ImageIcon, Video, Music, FileText, File as FileIcon } from "lucide-react";
import clsx from "clsx";
import type { Asset, MediaType } from "@/lib/types";
import { Dialog, type DialogHandle } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useAssets } from "@/hooks/useAssets";
import { formatBytes } from "@/lib/format";

const ICONS: Record<MediaType, typeof ImageIcon> = {
  image: ImageIcon,
  video: Video,
  audio: Music,
  document: FileText,
  text: FileText,
};

interface AssetPickerProps {
  label: string;
  onPick: (asset: Asset) => void;
  filter?: (asset: Asset) => boolean;
}

export function AssetPicker({ label, onPick, filter }: AssetPickerProps) {
  const dialogRef = useRef<DialogHandle>(null);
  const { assets, loading, error } = useAssets();
  const [query, setQuery] = useState("");

  const visible = assets
    .filter((a) => (filter ? filter(a) : true))
    .filter((a) => a.filename.toLowerCase().includes(query.toLowerCase()));

  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => dialogRef.current?.show()}>
        {label}
      </Button>
      <Dialog ref={dialogRef} title="Choose an asset">
        <div className="space-y-3">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by filename"
            className="w-full rounded-sm border border-border-strong bg-bg px-3 py-2 text-[13px] text-fg outline-none focus:border-accent"
          />
          <div className="max-h-80 overflow-y-auto">
            {loading ? (
              <div className="flex justify-center py-8">
                <Spinner />
              </div>
            ) : error ? (
              <p className="py-4 text-[13px] text-status-failed">{error}</p>
            ) : visible.length === 0 ? (
              <p className="py-4 text-center text-[13px] text-fg-tertiary">No assets found.</p>
            ) : (
              <ul className="space-y-0.5">
                {visible.map((asset) => {
                  const Icon = ICONS[asset.media] ?? FileIcon;
                  return (
                    <li key={asset.handle}>
                      <button
                        type="button"
                        onClick={() => {
                          onPick(asset);
                          dialogRef.current?.close();
                        }}
                        className={clsx(
                          "flex w-full items-center gap-2.5 rounded-sm px-2.5 py-2 text-left text-[13px] hover:bg-bg-subtle",
                        )}
                      >
                        <Icon size={15} className="shrink-0 text-fg-tertiary" />
                        <span className="min-w-0 flex-1 truncate text-fg">{asset.filename}</span>
                        <span className="shrink-0 text-[12px] text-fg-tertiary">
                          {formatBytes(asset.size)}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      </Dialog>
    </>
  );
}
