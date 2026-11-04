"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Paperclip, Send } from "lucide-react";
import clsx from "clsx";
import type { Job } from "@/lib/types";
import { useUpload } from "@/hooks/useUpload";
import { Dropzone } from "@/components/upload/Dropzone";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/errors/ErrorBanner";
import { readApiError } from "@/lib/errors";

const SUGGESTIONS = [
  "Add a voiceover that sounds human",
  "Use the music I attached",
  "Make it vertical for TikTok",
  "Make it shorter",
];

/**
 * Continue the conversation about a finished job: refine its result ("add a voiceover, make it
 * sound human", "use this music instead"), optionally with new files, or answer the question
 * the AI asked. The new job carries `parent`, so the AI sees the earlier request, its plan and
 * its result, and edits that result instead of starting over.
 */
export function RefinePanel({ job, question }: { job: Job; question?: string }) {
  const router = useRouter();
  const { items, upload, remove } = useUpload();
  const [text, setText] = useState("");
  const [showFiles, setShowFiles] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const uploading = items.some((i) => i.status === "uploading");
  const handles = items.filter((i) => i.asset).map((i) => i.asset!.handle);
  const asking = Boolean(question);

  async function send() {
    if (!text.trim() || uploading) return;
    setSending(true);
    setError(null);
    try {
      const res = await fetch("/api/proxy/jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt: text.trim(), assets: handles, parent: job.id }),
      });
      if (!res.ok) throw new Error((await readApiError(res)).message);
      const next: Job = await res.json();
      router.push(`/jobs/${next.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send that.");
      setSending(false);
    }
  }

  return (
    <div
      className={clsx(
        "space-y-3 rounded-md border p-4",
        asking
          ? "border-status-needs-input/30 bg-status-needs-input/5"
          : "border-border bg-bg-subtle",
      )}
    >
      {asking ? (
        <p className="text-[14px] font-medium text-fg">{question}</p>
      ) : (
        <div>
          <p className="text-[14px] font-medium text-fg">Refine this result</p>
          <p className="text-[12px] text-fg-secondary">
            Say what to change. The AI keeps everything else, and you can attach new files.
          </p>
        </div>
      )}

      {!asking ? (
        <div className="flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setText(s)}
              className="rounded-full border border-border-strong px-2.5 py-1 text-[12px] text-fg-secondary transition-colors hover:border-accent hover:text-fg"
            >
              {s}
            </button>
          ))}
        </div>
      ) : null}

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
        }}
        placeholder={asking ? "Your answer" : "e.g. Add a warm female voiceover introducing each photo"}
        rows={3}
        className="w-full resize-none rounded-sm border border-border-strong bg-bg px-3 py-2 text-[13px] text-fg outline-none focus:border-accent"
      />

      {showFiles || items.length > 0 ? (
        <Dropzone items={items} onFiles={upload} onRemove={remove} maxFiles={10} />
      ) : null}

      {error ? <ErrorBanner message={error} /> : null}

      <div className="flex items-center justify-between gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowFiles((v) => !v)}
          disabled={items.length > 0}
        >
          <Paperclip size={14} />
          {items.length > 0 ? `${items.length} file${items.length === 1 ? "" : "s"}` : "Attach files"}
        </Button>
        <Button size="sm" loading={sending} disabled={!text.trim() || uploading} onClick={send}>
          <Send size={14} />
          {asking ? "Send answer" : "Refine"}
        </Button>
      </div>
    </div>
  );
}
