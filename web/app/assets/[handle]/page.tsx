import { notFound } from "next/navigation";
import { Download } from "lucide-react";
import { api } from "@/lib/api";
import { ApiClientError } from "@/lib/errors";
import { MediaPreview, contentUrl } from "@/components/media/MediaPreview";
import { formatBytes, formatDate } from "@/lib/format";

// Always live data from Django, never a build-time static render.
export const dynamic = "force-dynamic";

export default async function AssetDetailPage({
  params,
}: {
  params: Promise<{ handle: string }>;
}) {
  const { handle } = await params;
  let asset;
  try {
    asset = await api.getAsset(decodeURIComponent(handle));
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) notFound();
    throw err;
  }

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold tracking-tight text-fg">
            {asset.filename}
          </h1>
          <p className="mt-1 text-[13px] text-fg-secondary">
            {asset.media} - {formatBytes(asset.size)} - {formatDate(asset.created_at)}
          </p>
        </div>
        <a
          href={contentUrl(asset.handle)}
          download
          className="flex shrink-0 items-center gap-1.5 rounded-sm border border-border-strong px-3 py-1.5 text-[13px] text-fg hover:bg-bg-subtle"
        >
          <Download size={14} />
          Download
        </a>
      </div>

      <MediaPreview
        handle={asset.handle}
        media={asset.media}
        filename={asset.filename}
        className="max-h-[480px] w-full rounded-md border border-border object-contain"
      />

      {asset.description ? (
        <p className="text-[13px] text-fg-secondary">{asset.description}</p>
      ) : null}

      <div className="space-y-1.5">
        <p className="text-[13px] font-medium text-fg">Metadata</p>
        <pre className="overflow-x-auto rounded-md border border-border bg-bg-inset p-3 font-mono text-[12px] text-fg-secondary">
          {JSON.stringify(asset.meta, null, 2)}
        </pre>
      </div>
    </div>
  );
}
