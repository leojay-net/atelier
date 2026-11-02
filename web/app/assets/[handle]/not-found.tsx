import Link from "next/link";
import { FileQuestion } from "lucide-react";

export default function AssetNotFound() {
  return (
    <div className="flex flex-col items-center gap-3 py-24 text-center">
      <FileQuestion size={32} className="text-fg-tertiary" />
      <p className="text-[15px] font-medium text-fg">That asset could not be found.</p>
      <Link href="/assets" className="text-[13px] text-accent hover:underline">
        Back to Assets
      </Link>
    </div>
  );
}
