"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronRight } from "lucide-react";
import clsx from "clsx";
import { SpecEditor } from "@/components/jobs/SpecEditor";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/errors/ErrorBanner";
import { readApiError } from "@/lib/errors";

const PLACEHOLDER = '{\n  "inputs": {"photo": "asset:..."},\n  "steps": [],\n  "outputs": []\n}';

export function NewRecipePanel() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [specText, setSpecText] = useState(PLACEHOLDER);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<{ message: string; details?: string[] } | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      let spec: unknown;
      try {
        spec = JSON.parse(specText);
      } catch {
        throw new Error("That is not valid JSON.");
      }
      const res = await fetch("/api/proxy/recipes", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, description, spec }),
      });
      if (!res.ok) {
        const info = await readApiError(res);
        throw Object.assign(new Error(info.message), { details: info.errors });
      }
      setOpen(false);
      setName("");
      setDescription("");
      setSpecText(PLACEHOLDER);
      router.refresh();
    } catch (err) {
      const details = err instanceof Error ? (err as Error & { details?: string[] }).details : undefined;
      setError({ message: err instanceof Error ? err.message : "Could not save.", details });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="border-t border-border pt-4">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-[13px] font-medium text-fg-secondary hover:text-fg"
      >
        <ChevronRight size={14} className={clsx("transition-transform", open && "rotate-90")} />
        New recipe from spec
      </button>
      {open ? (
        <div className="mt-3 space-y-3">
          <p className="text-[12.5px] text-fg-tertiary">
            The spec needs an &quot;inputs&quot; object; each key becomes a parameter when the
            recipe is run.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="recipe-name"
              className="rounded-sm border border-border-strong bg-bg px-2.5 py-1.5 text-[13px] text-fg outline-none focus:border-accent"
            />
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Description (optional)"
              className="rounded-sm border border-border-strong bg-bg px-2.5 py-1.5 text-[13px] text-fg outline-none focus:border-accent"
            />
          </div>
          <SpecEditor value={specText} onChange={setSpecText} />
          {error ? <ErrorBanner message={error.message} details={error.details} /> : null}
          <div className="flex justify-end">
            <Button
              size="sm"
              loading={saving}
              disabled={!/^[a-z0-9][a-z0-9_-]{0,58}$/.test(name)}
              onClick={save}
            >
              Save recipe
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
