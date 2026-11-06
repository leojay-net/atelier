import { redirect } from "next/navigation";

/** The hand editor now lives inside the workspace; old links land there. */
export default async function EditPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ asset?: string }>;
}) {
  const { id } = await params;
  const { asset } = await searchParams;
  redirect(`/studio/${id}${asset ? `?asset=${encodeURIComponent(asset)}` : ""}`);
}
