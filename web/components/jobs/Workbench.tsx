"use client";

import { useEffect, useState } from "react";
import { Play } from "lucide-react";
import type { Asset, Job } from "@/lib/types";
import { useUpload } from "@/hooks/useUpload";
import { Dropzone } from "@/components/upload/Dropzone";
import { AssetChip } from "@/components/upload/AssetChip";
import { AssetPicker } from "@/components/assets/AssetPicker";
import { ModeToggle, type Mode } from "./ModeToggle";
import { PromptForm } from "./PromptForm";
import { SpecEditor } from "./SpecEditor";
import { JobDetail, RETRY_STORAGE_KEY } from "./JobDetail";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/errors/ErrorBanner";
import { readApiError } from "@/lib/errors";
import Link from "next/link";

const DEFAULT_SPEC = '{\n  "inputs": {},\n  "steps": [],\n  "outputs": []\n}';

interface RetryPayload {
  mode: Mode;
  prompt?: string;
  assets?: string[];
  spec?: Record<string, unknown>;
}

export function Workbench() {
  const { items, upload, remove } = useUpload();
  const [mode, setMode] = useState<Mode>("describe");
  const [prompt, setPrompt] = useState("");
  const [specText, setSpecText] = useState(DEFAULT_SPEC);
  const [extraAssets, setExtraAssets] = useState<Asset[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<{ message: string; details?: string[]; code?: string } | null>(
    null,
  );
  const [job, setJob] = useState<Job | null>(null);

  useEffect(() => {
    // sessionStorage only exists client-side, so this one-time prefill-on-mount read has to
    // happen in an effect (reading it during render would mismatch the server-rendered HTML).
    const raw = sessionStorage.getItem(RETRY_STORAGE_KEY);
    if (!raw) return;
    sessionStorage.removeItem(RETRY_STORAGE_KEY);
    try {
      const retry: RetryPayload = JSON.parse(raw);
      // A one-time prefill right after mount, not a render loop; the extra paint is negligible.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setMode(retry.mode);
      if (retry.mode === "describe") setPrompt(retry.prompt ?? "");
      if (retry.mode === "spec" && retry.spec) setSpecText(JSON.stringify(retry.spec, null, 2));
    } catch {
      // ignore a corrupted retry payload
    }
  }, []);

  const uploadedAssets = [...items.filter((i) => i.asset).map((i) => i.asset!), ...extraAssets];
  const uploading = items.some((i) => i.status === "uploading");

  async function run() {
    setError(null);
    setJob(null);
    setRunning(true);
    try {
      const body =
        mode === "describe"
          ? { prompt, assets: uploadedAssets.map((a) => a.handle) }
          : { spec: JSON.parse(specText) };
      const res = await fetch("/api/proxy/jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const info = await readApiError(res);
        throw Object.assign(new Error(info.message), { details: info.errors, code: info.code });
      }
      setJob((await res.json()) as Job);
    } catch (err) {
      if (err instanceof SyntaxError) {
        setError({ message: "That is not valid JSON." });
      } else if (err instanceof Error) {
        const { details, code } = err as Error & { details?: string[]; code?: string };
        setError({ message: err.message, details, code });
      } else {
        setError({ message: "Something went wrong." });
      }
    } finally {
      setRunning(false);
    }
  }

  const canRun = mode === "describe" ? prompt.trim().length > 0 && !uploading : specText.trim().length > 0;

  if (job) {
    return (
      <div className="space-y-4">
        <button
          type="button"
          onClick={() => setJob(null)}
          className="text-[13px] text-fg-secondary hover:text-fg"
        >
          &larr; New request
        </button>
        <JobDetail jobId={job.id} initialJob={job} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-fg">Workbench</h1>
        <p className="mt-1 text-[13px] text-fg-secondary">
          Upload a file, say what you want, and run it.
        </p>
      </div>

      <Dropzone items={items} onFiles={upload} onRemove={remove} />

      <div className="flex flex-wrap items-center gap-1.5">
        {extraAssets.map((a) => (
          <AssetChip
            key={a.handle}
            filename={a.filename}
            media={a.media}
            size={a.size}
            handle={a.handle}
            onRemove={() => setExtraAssets((cur) => cur.filter((x) => x.handle !== a.handle))}
          />
        ))}
        <AssetPicker
          label="Add existing asset"
          onPick={(asset) =>
            setExtraAssets((cur) =>
              cur.some((a) => a.handle === asset.handle) ? cur : [...cur, asset],
            )
          }
        />
      </div>

      <ModeToggle mode={mode} onChange={setMode} />

      {mode === "describe" ? (
        <PromptForm value={prompt} onChange={setPrompt} />
      ) : (
        <SpecEditor value={specText} onChange={setSpecText} />
      )}

      {error?.code === "quota_exceeded" ? (
        <div className="rounded-md border border-status-warn/30 bg-status-warn/5 px-4 py-3 text-[13px] text-fg">
          <p className="font-medium">{error.message}</p>
          <Link href="/settings" className="text-accent hover:underline">
            View usage in Keys
          </Link>
        </div>
      ) : error ? (
        <ErrorBanner message={error.message} details={error.details} />
      ) : null}

      <div className="flex justify-end">
        <Button loading={running} disabled={!canRun || running} onClick={run}>
          <Play size={14} />
          Run
        </Button>
      </div>
    </div>
  );
}
