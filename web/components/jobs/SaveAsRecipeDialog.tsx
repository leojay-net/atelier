"use client";

import { useRef, useState } from "react";
import { Dialog, type DialogHandle } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/errors/ErrorBanner";
import { useToast } from "@/components/errors/ToastProvider";
import { readApiError } from "@/lib/errors";

interface SaveAsRecipeDialogProps {
  jobId: string;
}

export function SaveAsRecipeDialog({ jobId }: SaveAsRecipeDialogProps) {
  const dialogRef = useRef<DialogHandle>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  async function submit() {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/proxy/jobs/${encodeURIComponent(jobId)}/save-recipe`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, description }),
      });
      if (!res.ok) {
        const info = await readApiError(res);
        throw new Error(info.message);
      }
      dialogRef.current?.close();
      toast.show("success", `Saved the recipe "${name}".`);
      setName("");
      setDescription("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the recipe.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => dialogRef.current?.show()}>
        Save as recipe
      </Button>
      <Dialog ref={dialogRef} title="Save as recipe">
        <div className="space-y-3">
          <p className="text-[13px] text-fg-secondary">
            Save the exact steps this run took as a reusable recipe.
          </p>
          <div className="space-y-1">
            <label htmlFor="recipe-name" className="text-[13px] font-medium text-fg">
              Name
            </label>
            <input
              id="recipe-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="social-thumbnail"
              className="w-full rounded-sm border border-border-strong bg-bg px-3 py-2 text-[13px] text-fg outline-none focus:border-accent"
            />
            <p className="text-[12px] text-fg-tertiary">
              Lowercase letters, numbers, hyphens and underscores.
            </p>
          </div>
          <div className="space-y-1">
            <label htmlFor="recipe-description" className="text-[13px] font-medium text-fg">
              Description
            </label>
            <input
              id="recipe-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional"
              className="w-full rounded-sm border border-border-strong bg-bg px-3 py-2 text-[13px] text-fg outline-none focus:border-accent"
            />
          </div>
          {error ? <ErrorBanner message={error} /> : null}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="secondary" size="sm" onClick={() => dialogRef.current?.close()}>
              Cancel
            </Button>
            <Button
              size="sm"
              loading={saving}
              disabled={!/^[a-z0-9][a-z0-9_-]{0,58}$/.test(name)}
              onClick={submit}
            >
              Save
            </Button>
          </div>
        </div>
      </Dialog>
    </>
  );
}
