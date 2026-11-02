import { FileText, File as FileIcon, Music } from "lucide-react";
import type { MediaType } from "@/lib/types";

interface MediaPreviewProps {
  handle: string;
  media: MediaType;
  filename: string;
  className?: string;
}

/** The authenticated content URL: always through the proxy, never Django directly. */
export function contentUrl(handle: string): string {
  return `/api/proxy/assets/${encodeURIComponent(handle)}/content`;
}

export function MediaPreview({ handle, media, filename, className }: MediaPreviewProps) {
  const src = contentUrl(handle);

  if (media === "image") {
    // eslint-disable-next-line @next/next/no-img-element -- authenticated, same-origin proxy URL
    return <img src={src} alt={filename} className={className} />;
  }
  if (media === "video") {
    return <video src={src} controls className={className} />;
  }
  if (media === "audio") {
    return (
      <div className={className}>
        <div className="flex items-center gap-3 rounded-md bg-bg-inset px-4 py-6">
          <Music size={20} className="text-fg-tertiary" />
          <audio src={src} controls className="w-full" />
        </div>
      </div>
    );
  }
  const Icon = media === "document" || media === "text" ? FileText : FileIcon;
  return (
    <div
      className={`flex flex-col items-center justify-center gap-2 rounded-md bg-bg-inset py-10 ${className ?? ""}`}
    >
      <Icon size={28} className="text-fg-tertiary" />
      <p className="max-w-full truncate px-4 text-[13px] text-fg-secondary">{filename}</p>
    </div>
  );
}
