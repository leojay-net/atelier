import type { ReactNode } from "react";
import clsx from "clsx";

interface BadgeProps {
  children: ReactNode;
  tone?: "neutral" | "accent";
  className?: string;
}

export function Badge({ children, tone = "neutral", className }: BadgeProps) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[11px] font-medium",
        tone === "neutral" && "bg-bg-inset text-fg-secondary",
        tone === "accent" && "bg-accent-subtle-bg text-accent",
        className,
      )}
    >
      {children}
    </span>
  );
}
