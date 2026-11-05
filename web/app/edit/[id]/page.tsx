import type { Metadata } from "next";
import { Editor } from "@/components/editor/Editor";

export const metadata: Metadata = { title: "Edit · Atelier" };

export default async function EditPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ asset?: string }>;
}) {
  const { id } = await params;
  const { asset } = await searchParams;
  return <Editor jobId={id} source={asset ?? ""} />;
}
