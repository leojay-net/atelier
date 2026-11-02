import { Download } from "lucide-react";
import type { JobOutput } from "@/lib/types";
import { MediaPreview, contentUrl } from "@/components/media/MediaPreview";
import { formatBytes } from "@/lib/format";

interface ResultPreviewProps {
  outputs: Record<string, JobOutput>;
}

export function ResultPreview({ outputs }: ResultPreviewProps) {
  const entries = Object.entries(outputs);
  if (entries.length === 0) return null;

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {entries.map(([outputId, output]) => (
        <div key={outputId} className="overflow-hidden rounded-md border border-border">
          <MediaPreview
            handle={output.handle}
            media={output.media}
            filename={outputId}
            className="max-h-80 w-full object-contain"
          />
          <div className="flex items-center justify-between gap-2 border-t border-border px-3 py-2.5">
            <div className="min-w-0">
              <p className="truncate text-[13px] text-fg">{output.summary || outputId}</p>
              <p className="text-[12px] text-fg-tertiary">{formatBytes(output.size)}</p>
            </div>
            <a
              href={contentUrl(output.handle)}
              download
              className="shrink-0 rounded-sm p-1.5 text-fg-secondary hover:bg-bg-subtle hover:text-fg"
              title="Download"
            >
              <Download size={15} />
            </a>
          </div>
        </div>
      ))}
    </div>
  );
}
