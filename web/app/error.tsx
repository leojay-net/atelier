"use client";

import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/Button";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-3 py-24 text-center">
      <AlertTriangle size={32} className="text-fg-tertiary" />
      <p className="text-[15px] font-medium text-fg">Something went wrong.</p>
      <p className="max-w-sm text-[13px] text-fg-secondary">{error.message}</p>
      <Button variant="secondary" size="sm" onClick={reset}>
        Try again
      </Button>
    </div>
  );
}
