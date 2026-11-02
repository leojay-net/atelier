import { AlertCircle } from "lucide-react";
import clsx from "clsx";

interface ErrorBannerProps {
  message: string;
  details?: string[];
  className?: string;
}

export function ErrorBanner({ message, details, className }: ErrorBannerProps) {
  return (
    <div
      role="alert"
      className={clsx(
        "flex gap-2.5 rounded-md border border-status-failed/25 bg-red-50 px-4 py-3 text-[13px] text-status-failed dark:bg-red-950/20",
        className,
      )}
    >
      <AlertCircle size={16} className="mt-0.5 shrink-0" />
      <div className="space-y-1">
        <p className="font-medium">{message}</p>
        {details && details.length > 0 ? (
          <ul className="list-disc space-y-0.5 pl-4">
            {details.map((d, i) => (
              <li key={i}>{d}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
