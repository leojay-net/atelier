import clsx from "clsx";

interface ProgressBarProps {
  /** 0 to 1. Omit for an indeterminate bar. */
  value?: number;
  className?: string;
}

export function ProgressBar({ value, className }: ProgressBarProps) {
  const determinate = typeof value === "number";
  return (
    <div
      className={clsx("h-1.5 w-full overflow-hidden rounded-full bg-bg-inset", className)}
      role="progressbar"
      aria-valuenow={determinate ? Math.round(value! * 100) : undefined}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={clsx(
          "h-full rounded-full bg-accent transition-[width]",
          !determinate && "w-1/3 animate-indeterminate",
        )}
        style={determinate ? { width: `${Math.max(0, Math.min(1, value!)) * 100}%` } : undefined}
      />
    </div>
  );
}
