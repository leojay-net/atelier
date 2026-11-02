const MAX_CHARS = 4000;

interface PromptFormProps {
  value: string;
  onChange: (value: string) => void;
}

export function PromptForm({ value, onChange }: PromptFormProps) {
  return (
    <div className="space-y-1">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value.slice(0, MAX_CHARS))}
        placeholder="Describe what you want. For example: crop these photos to a square and remove the background."
        rows={4}
        className="w-full resize-none rounded-md border border-border-strong bg-bg px-3.5 py-3 text-[14px] text-fg outline-none focus:border-accent"
      />
      <p className="text-right text-[12px] text-fg-tertiary">
        {value.length} / {MAX_CHARS}
      </p>
    </div>
  );
}
