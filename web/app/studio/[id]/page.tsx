import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { ApiClientError } from "@/lib/errors";
import { Studio } from "@/components/studio/Studio";
import type { Job } from "@/lib/types";

export const dynamic = "force-dynamic";

/** A project (a conversation), opened by its latest job (the URL the studio keeps up to date).
 * `?asset=` opens a particular version, as the old "Edit" links did. */
export default async function StudioThreadPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ asset?: string }>;
}) {
  const { id } = await params;
  const { asset } = await searchParams;
  let items: Job[];
  try {
    items = (await api.thread(id)).items;
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) notFound();
    throw err;
  }
  return <Studio key={id} initialThread={items} initialAsset={asset} />;
}
