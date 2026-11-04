import type { Metadata } from "next";
import { LibraryView } from "@/components/library/Library";

export const metadata: Metadata = { title: "Library · Atelier" };

export default function LibraryPage() {
  return <LibraryView />;
}
