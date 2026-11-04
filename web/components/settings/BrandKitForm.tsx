"use client";

import { useState } from "react";
import { Plus, X } from "lucide-react";
import type { Asset, BrandKit } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/errors/ErrorBanner";
import { useToast } from "@/components/errors/ToastProvider";
import { AssetPicker } from "@/components/assets/AssetPicker";
import { readApiError } from "@/lib/errors";

function KeyValueRows({
  entries,
  onChange,
  keyPlaceholder,
  valuePlaceholder,
}: {
  entries: [string, string][];
  onChange: (entries: [string, string][]) => void;
  keyPlaceholder: string;
  valuePlaceholder: string;
}) {
  return (
    <div className="space-y-2">
      {entries.map(([key, value], i) => (
        <div key={i} className="flex gap-2">
          <input
            value={key}
            onChange={(e) => {
              const next = [...entries];
              next[i] = [e.target.value, value];
              onChange(next);
            }}
            placeholder={keyPlaceholder}
            className="w-32 rounded-sm border border-border-strong bg-bg px-2.5 py-1.5 text-[13px] text-fg outline-none focus:border-accent"
          />
          <input
            value={value}
            onChange={(e) => {
              const next = [...entries];
              next[i] = [key, e.target.value];
              onChange(next);
            }}
            placeholder={valuePlaceholder}
            className="flex-1 rounded-sm border border-border-strong bg-bg px-2.5 py-1.5 text-[13px] text-fg outline-none focus:border-accent"
          />
          <button
            type="button"
            onClick={() => onChange(entries.filter((_, j) => j !== i))}
            className="rounded-sm p-1.5 text-fg-tertiary hover:bg-bg-subtle hover:text-status-failed"
          >
            <X size={14} />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...entries, ["", ""]])}
        className="flex items-center gap-1 text-[12.5px] text-fg-secondary hover:text-fg"
      >
        <Plus size={13} />
        Add
      </button>
    </div>
  );
}

export function BrandKitForm({ initial }: { initial: BrandKit }) {
  const toast = useToast();
  const [name, setName] = useState(initial.name ?? "");
  const [tagline, setTagline] = useState(initial.tagline ?? "");
  const [colors, setColors] = useState<[string, string][]>(Object.entries(initial.colors ?? {}));
  const [fonts, setFonts] = useState<[string, string][]>(Object.entries(initial.fonts ?? {}));
  const [rules, setRules] = useState<string[]>(initial.rules ?? []);
  const [logo, setLogo] = useState(initial.logo);
  const [logoDark, setLogoDark] = useState(initial.logo_dark);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<{ message: string; details?: string[] } | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const body: BrandKit = {
        name,
        tagline,
        colors: Object.fromEntries(colors.filter(([k]) => k)),
        fonts: Object.fromEntries(fonts.filter(([k]) => k)),
        rules: rules.filter((r) => r.trim().length > 0),
        ...(logo ? { logo } : {}),
        ...(logoDark ? { logo_dark: logoDark } : {}),
      };
      const res = await fetch("/api/proxy/brand-kit", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const info = await readApiError(res);
        throw Object.assign(new Error(info.message), { details: info.errors });
      }
      toast.show("success", "Brand kit saved.");
    } catch (err) {
      const details = err instanceof Error ? (err as Error & { details?: string[] }).details : undefined;
      setError({ message: err instanceof Error ? err.message : "Could not save.", details });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="space-y-4 p-5">
      <h2 className="text-[14px] font-semibold text-fg">Brand kit</h2>
      <p className="text-[12.5px] text-fg-secondary">
        Sent with every prompt request, so the assistant can use your name, colors and fonts.
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <label className="text-[13px] font-medium text-fg">Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-sm border border-border-strong bg-bg px-2.5 py-1.5 text-[13px] text-fg outline-none focus:border-accent"
          />
        </div>
        <div className="space-y-1">
          <label className="text-[13px] font-medium text-fg">Tagline</label>
          <input
            value={tagline}
            onChange={(e) => setTagline(e.target.value)}
            className="w-full rounded-sm border border-border-strong bg-bg px-2.5 py-1.5 text-[13px] text-fg outline-none focus:border-accent"
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-fg">Colors</label>
        <KeyValueRows
          entries={colors}
          onChange={setColors}
          keyPlaceholder="role"
          valuePlaceholder="#000000"
        />
      </div>

      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-fg">Fonts</label>
        <p className="text-[12px] text-fg-tertiary">
          A family name from the bundled font pack. An unlisted name is rejected when you save,
          and the error lists which ones are available.
        </p>
        <KeyValueRows
          entries={fonts}
          onChange={setFonts}
          keyPlaceholder="role"
          valuePlaceholder="family name"
        />
      </div>

      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-fg">Rules, Limitations & Restrictions</label>
        <p className="text-[12px] text-fg-tertiary">
          Mandatory rules and negative constraints the agent must strictly follow (e.g. &quot;Cap video length at 60s&quot;, &quot;Always use primary brand color for headlines&quot;, &quot;No music without consent&quot;).
        </p>
        <div className="space-y-2">
          {rules.map((rule, idx) => (
            <div key={idx} className="flex gap-2">
              <input
                value={rule}
                onChange={(e) => {
                  const next = [...rules];
                  next[idx] = e.target.value;
                  setRules(next);
                }}
                placeholder="e.g. Max video duration 60 seconds"
                className="flex-1 rounded-sm border border-border-strong bg-bg px-2.5 py-1.5 text-[13px] text-fg outline-none focus:border-accent"
              />
              <button
                type="button"
                onClick={() => setRules(rules.filter((_, j) => j !== idx))}
                className="rounded-sm p-1.5 text-fg-tertiary hover:bg-bg-subtle hover:text-status-failed"
              >
                <X size={14} />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={() => setRules([...rules, ""])}
            className="flex items-center gap-1 text-[12.5px] text-fg-secondary hover:text-fg"
          >
            <Plus size={13} />
            Add rule
          </button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <label className="text-[13px] font-medium text-fg">Logo</label>
          <div className="flex items-center gap-2">
            {logo ? <code className="truncate font-mono text-[12px] text-fg-tertiary">{logo}</code> : null}
            <AssetPicker
              label={logo ? "Change" : "Choose"}
              filter={(a: Asset) => a.media === "image"}
              onPick={(a) => setLogo(a.handle)}
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <label className="text-[13px] font-medium text-fg">Logo (dark background)</label>
          <div className="flex items-center gap-2">
            {logoDark ? (
              <code className="truncate font-mono text-[12px] text-fg-tertiary">{logoDark}</code>
            ) : null}
            <AssetPicker
              label={logoDark ? "Change" : "Choose"}
              filter={(a: Asset) => a.media === "image"}
              onPick={(a) => setLogoDark(a.handle)}
            />
          </div>
        </div>
      </div>

      {error ? <ErrorBanner message={error.message} details={error.details} /> : null}

      <div className="flex justify-end">
        <Button size="sm" loading={saving} onClick={save}>
          Save
        </Button>
      </div>
    </Card>
  );
}
