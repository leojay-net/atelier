"use client";

import { useCallback, useRef, useState } from "react";
import type { Asset } from "@/lib/types";
import { readApiError } from "@/lib/errors";

export interface UploadItem {
  id: string;
  file: File;
  progress: number;
  status: "uploading" | "done" | "error";
  asset?: Asset;
  error?: string;
}

/**
 * Uploads via XHR, not fetch: as of now fetch() has no reliable, universally supported way to
 * report request-upload progress, which XHR's upload.onprogress has always had.
 */
function uploadOne(file: File, onProgress: (fraction: number) => void): Promise<Asset> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/proxy/assets");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = async () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as Asset);
        return;
      }
      const res = new Response(xhr.responseText, { status: xhr.status });
      const info = await readApiError(res);
      reject(new Error(info.message));
    };
    xhr.onerror = () => reject(new Error("The upload failed. Check your connection."));
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

export function useUpload() {
  const [items, setItems] = useState<UploadItem[]>([]);
  const counter = useRef(0);

  const upload = useCallback((files: FileList | File[]) => {
    const list = Array.from(files);
    const withIds = list.map((file) => ({
      id: `${Date.now()}-${counter.current++}`,
      file,
      progress: 0,
      status: "uploading" as const,
    }));
    setItems((current) => [...current, ...withIds]);

    withIds.forEach((item) => {
      uploadOne(item.file, (progress) => {
        setItems((current) =>
          current.map((it) => (it.id === item.id ? { ...it, progress } : it)),
        );
      })
        .then((asset) => {
          setItems((current) =>
            current.map((it) =>
              it.id === item.id ? { ...it, status: "done", progress: 1, asset } : it,
            ),
          );
        })
        .catch((err: Error) => {
          setItems((current) =>
            current.map((it) =>
              it.id === item.id ? { ...it, status: "error", error: err.message } : it,
            ),
          );
        });
    });
  }, []);

  const remove = useCallback((id: string) => {
    setItems((current) => current.filter((it) => it.id !== id));
  }, []);

  return { items, upload, remove };
}
