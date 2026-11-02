"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";
import { Dialog, type DialogHandle } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/errors/ErrorBanner";

export function DeleteRecipeConfirm({ id, name }: { id: number; name: string }) {
  const dialogRef = useRef<DialogHandle>(null);
  const router = useRouter();
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirmDelete() {
    setDeleting(true);
    setError(null);
    try {
      const res = await fetch(`/api/proxy/recipes/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Could not delete the recipe.");
      dialogRef.current?.close();
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the recipe.");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <Button variant="ghost" size="sm" onClick={() => dialogRef.current?.show()}>
        <Trash2 size={14} />
      </Button>
      <Dialog ref={dialogRef} title="Delete recipe">
        <div className="space-y-3">
          <p className="text-[13px] text-fg">
            Delete <strong className="font-medium">{name}</strong>? It will no longer be runnable.
          </p>
          {error ? <ErrorBanner message={error} /> : null}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => dialogRef.current?.close()}>
              Cancel
            </Button>
            <Button variant="destructive" size="sm" loading={deleting} onClick={confirmDelete}>
              Delete
            </Button>
          </div>
        </div>
      </Dialog>
    </>
  );
}
