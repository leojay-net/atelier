import { forwardRef } from "react";
import type { ButtonHTMLAttributes } from "react";
import clsx from "clsx";
import { Spinner } from "./Spinner";

type Variant = "primary" | "secondary" | "destructive" | "ghost";
type Size = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

const base =
  "inline-flex select-none items-center justify-center gap-2 rounded-md font-medium tracking-[-0.01em] transition-[background-color,border-color,color,box-shadow,transform] duration-200 " +
  "disabled:cursor-not-allowed disabled:opacity-40";

const sizes: Record<Size, string> = {
  sm: "h-8 rounded-sm px-3.5 text-[13px]",
  md: "h-10 px-5 text-[14px]",
};

const variants: Record<Variant, string> = {
  primary:
    "bg-accent text-on-accent shadow-[0_1px_2px_rgba(0,0,0,0.14),inset_0_0_0_0.5px_rgba(255,255,255,0.18)] hover:bg-accent-hover active:bg-accent-active",
  secondary:
    "border border-border-strong bg-bg text-fg shadow-[0_1px_1px_rgba(0,0,0,0.04)] hover:bg-bg-subtle hover:border-fg-tertiary/60",
  destructive: "text-status-failed hover:bg-status-failed/10",
  ghost: "text-fg-secondary hover:bg-bg-subtle hover:text-fg",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", loading, disabled, className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={clsx(base, sizes[size], variants[variant], className)}
      {...rest}
    >
      {loading ? <Spinner size={14} /> : null}
      {children}
    </button>
  );
});
