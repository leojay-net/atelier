import type { ComponentType, ReactNode } from "react";

interface EmptyStateProps {
  icon: ComponentType<{ size?: number; className?: string }>;
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ icon: Icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="animate-rise flex flex-col items-center gap-4 rounded-xl border border-dashed border-border-strong px-6 py-16 text-center">
      <span className="grid h-14 w-14 place-items-center rounded-full bg-bg-subtle text-fg-tertiary shadow-[inset_0_0_0_0.5px_var(--color-border-strong)]">
        <Icon size={24} />
      </span>
      <div className="space-y-1">
        <p className="text-[15px] font-semibold tracking-tight text-fg">{title}</p>
        {description ? <p className="text-sm text-fg-secondary">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}
