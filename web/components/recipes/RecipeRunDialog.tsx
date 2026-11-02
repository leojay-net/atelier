"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Play } from "lucide-react";
import type { Asset, Job, Recipe } from "@/lib/types";
import { Dialog, type DialogHandle } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { AssetPicker } from "@/components/assets/AssetPicker";
import { ErrorBanner } from "@/components/errors/ErrorBanner";
import { readApiError } from "@/lib/errors";

export function RecipeRunDialog({ recipe }: { recipe: Recipe }) {
  const dialogRef = useRef<DialogHandle>(null);
  const router = useRouter();
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const slots = Object.entries(recipe.inputs);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      const res = await fetch("/api/proxy/jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ recipe_id: recipe.id, inputs: overrides }),
      });
      if (!res.ok) throw new Error((await readApiError(res)).message);
      const job: Job = await res.json();
      router.push(`/jobs/${job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the job.");
      setRunning(false);
    }
  }

  return (
    <>
      <Button size="sm" onClick={() => dialogRef.current?.show()}>
        <Play size={14} />
        Run
      </Button>
      <Dialog ref={dialogRef} title={`Run "${recipe.name}"`}>
        <div className="space-y-3">
          {slots.length === 0 ? (
            <p className="text-[13px] text-fg-secondary">This recipe takes no inputs.</p>
          ) : (
            <div className="space-y-2.5">
              {slots.map(([name, defaultHandle]) => (
                <div key={name} className="space-y-1">
                  <p className="text-[13px] font-medium text-fg">{name}</p>
                  <div className="flex items-center justify-between gap-2">
                    <code className="min-w-0 flex-1 truncate font-mono text-[12px] text-fg-tertiary">
                      {overrides[name] ?? defaultHandle}
                    </code>
                    <AssetPicker
                      label="Change"
                      onPick={(asset: Asset) =>
                        setOverrides((cur) => ({ ...cur, [name]: asset.handle }))
                      }
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
          {error ? <ErrorBanner message={error} /> : null}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="secondary" size="sm" onClick={() => dialogRef.current?.close()}>
              Cancel
            </Button>
            <Button size="sm" loading={running} onClick={run}>
              Run
            </Button>
          </div>
        </div>
      </Dialog>
    </>
  );
}
