"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useUpload } from "@/hooks/useUpload";
import { Dropzone } from "@/components/upload/Dropzone";

export function UploadPanel() {
  const { items, upload, remove } = useUpload();
  const router = useRouter();
  const refreshed = useRef(new Set<string>());

  useEffect(() => {
    const justDone = items.filter((i) => i.status === "done" && !refreshed.current.has(i.id));
    if (justDone.length > 0) {
      justDone.forEach((i) => refreshed.current.add(i.id));
      router.refresh();
    }
  }, [items, router]);

  return <Dropzone items={items} onFiles={upload} onRemove={remove} />;
}
