import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { ApiClientError } from "@/lib/errors";
import { Studio } from "@/components/studio/Studio";
import type { Job } from "@/lib/types";

export const dynamic = "force-dynamic";

/** A conversation, opened by its latest job (the URL the studio keeps up to date). */
export default async function StudioThreadPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let items: Job[];
  try {
    items = (await api.thread(id)).items;
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) notFound();
    throw err;
  }
  return <Studio key={id} initialThread={items} />;
}
