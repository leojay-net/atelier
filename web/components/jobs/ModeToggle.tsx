import clsx from "clsx";

export type Mode = "describe" | "spec";

interface ModeToggleProps {
  mode: Mode;
  onChange: (mode: Mode) => void;
}

export function ModeToggle({ mode, onChange }: ModeToggleProps) {
  return (
    <div className="inline-flex rounded-sm border border-border-strong p-0.5">
      {(["describe", "spec"] as const).map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => onChange(m)}
          className={clsx(
            "rounded-[4px] px-3 py-1.5 text-[13px] font-medium transition-colors",
            mode === m ? "bg-accent text-on-accent" : "text-fg-secondary hover:text-fg",
          )}
        >
          {m === "describe" ? "Describe" : "Spec"}
        </button>
      ))}
    </div>
  );
}
