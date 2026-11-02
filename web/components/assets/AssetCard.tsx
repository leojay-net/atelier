import Link from "next/link";
import Image from "next/image";
import { Video, Music, FileText, File as FileIcon } from "lucide-react";
import type { Asset } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { contentUrl } from "@/components/media/MediaPreview";
import { formatBytes, formatRelative } from "@/lib/format";

const ICONS = { video: Video, audio: Music, document: FileText, text: FileText } as const;

export function AssetCard({ asset }: { asset: Asset }) {
  const Icon = asset.media in ICONS ? ICONS[asset.media as keyof typeof ICONS] : FileIcon;

  return (
    <Link
      href={`/assets/${encodeURIComponent(asset.handle)}`}
      className="group overflow-hidden rounded-md border border-border hover:border-border-strong"
    >
      <div className="relative aspect-square bg-bg-inset">
        {asset.media === "image" || asset.media === "video" ? (
          <Image
            src={contentUrl(asset.handle)}
            alt={asset.filename}
            fill
            unoptimized
            className="object-cover"
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <Icon size={28} className="text-fg-tertiary" />
          </div>
        )}
      </div>
      <div className="space-y-1 p-2.5">
        <p className="truncate text-[12.5px] text-fg">{asset.filename}</p>
        <div className="flex items-center justify-between">
          <Badge>{asset.media}</Badge>
          <span className="text-[11px] text-fg-tertiary">{formatBytes(asset.size)}</span>
        </div>
        <p className="text-[11px] text-fg-tertiary">{formatRelative(asset.created_at)}</p>
      </div>
    </Link>
  );
}
