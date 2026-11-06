"use client";

import { Loader2, Presentation, Sparkles } from "lucide-react";
import type { JobOutput } from "@/lib/types";
import { contentUrl } from "@/components/media/MediaPreview";

/** What the player shows when the result isn't a video (or there's nothing yet). */
export function Viewer({ shown }: { shown: JobOutput | undefined }) {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 items-center justify-center">
      <ViewerBody shown={shown} />
    </div>
  );
}

function ViewerBody({ shown }: { shown: JobOutput | undefined }) {
  if (!shown)
    return (
      <div className="flex flex-col items-center gap-3 px-6 text-center text-fg-tertiary">
        <Sparkles size={30} />
        <p className="text-[15px] font-medium text-fg-secondary">Your video will appear here</p>
        <p className="max-w-sm text-[13px]">
          Add your photos, videos or music, then tell the assistant what you want.
        </p>
      </div>
    );
  if (shown.media === "image")
    return (
      // eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin URL
      <img src={contentUrl(shown.handle)} alt="Your result" className="max-h-full max-w-full object-contain" />
    );
  if (shown.media === "audio") return <audio src={contentUrl(shown.handle)} controls className="w-3/4" />;
  return (
    <div className="flex flex-col items-center gap-3 text-fg-secondary">
      {shown.media === "pptx" ? <Presentation size={44} /> : <Sparkles size={40} />}
      <p className="text-[15px] font-medium text-fg">
        {shown.media === "pptx" ? "Your presentation is ready" : "Your file is ready"}
      </p>
      <p className="text-[12px] text-fg-tertiary">Use Download in the top bar to open it.</p>
    </div>
  );
}

/** The bar over the picture while the assistant works. */
export function WorkingBar({ text }: { text: string }) {
  return (
    <div className="absolute inset-x-0 bottom-0 z-10 flex items-center gap-3 bg-black/75 px-4 py-3 text-[13px] text-white backdrop-blur">
      <Loader2 size={16} className="shrink-0 animate-spin" />
      <span className="truncate">{text}…</span>
      <span className="ml-auto hidden shrink-0 text-white/50 sm:inline">You can edit when I&apos;m done</span>
    </div>
  );
}
