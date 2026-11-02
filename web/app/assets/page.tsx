import { Image as ImageIcon } from "lucide-react";
import { api } from "@/lib/api";
import { AssetCard } from "@/components/assets/AssetCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pagination } from "@/components/ui/Pagination";
import { UploadPanel } from "./UploadPanel";

const LIMIT = 24;

// Always live data from Django, never a build-time static render.
export const dynamic = "force-dynamic";

export default async function AssetsPage({
  searchParams,
}: {
  searchParams: Promise<{ offset?: string }>;
}) {
  const { offset: offsetParam } = await searchParams;
  const offset = Number(offsetParam ?? 0) || 0;
  const { items, count } = await api.listAssets(LIMIT, offset);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-fg">Assets</h1>
        <p className="mt-1 text-[13px] text-fg-secondary">
          Everything uploaded or produced, {count} total.
        </p>
      </div>

      <UploadPanel />

      {items.length === 0 ? (
        <EmptyState
          icon={ImageIcon}
          title="No assets yet"
          description="Upload a file above to get started."
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
            {items.map((asset) => (
              <AssetCard key={asset.handle} asset={asset} />
            ))}
          </div>
          <Pagination
            count={count}
            limit={LIMIT}
            offset={offset}
            hrefFor={(o) => `/assets?offset=${o}`}
          />
        </>
      )}
    </div>
  );
}
