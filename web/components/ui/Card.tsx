import type { HTMLAttributes } from "react";
import clsx from "clsx";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={clsx(
        "rounded-md border border-border bg-bg shadow-[var(--shadow-card)]",
        className,
      )}
      {...rest}
    />
  );
}
