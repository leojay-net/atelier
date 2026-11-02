"use client";

import { useState } from "react";
import { ChevronRight, CheckCircle2 } from "lucide-react";
import clsx from "clsx";
import type { ValidateResult } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/errors/ErrorBanner";

interface SpecEditorProps {
  value: string;
  onChange: (value: string) => void;
}

export function SpecEditor({ value, onChange }: SpecEditorProps) {
  const [result, setResult] = useState<ValidateResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);
  const [showCatalogue, setShowCatalogue] = useState(false);
  const [catalogue, setCatalogue] = useState<string | null>(null);
  const [catalogueLoading, setCatalogueLoading] = useState(false);

  async function validate() {
    setParseError(null);
    setResult(null);
    let spec: unknown;
    try {
      spec = JSON.parse(value);
    } catch {
      setParseError("That is not valid JSON.");
      return;
    }
    setValidating(true);
    try {
      const res = await fetch("/api/proxy/validate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ spec }),
      });
      setResult((await res.json()) as ValidateResult);
    } catch {
      setParseError("Could not reach the API to validate.");
    } finally {
      setValidating(false);
    }
  }

  async function toggleCatalogue() {
    const next = !showCatalogue;
    setShowCatalogue(next);
    if (next && catalogue === null) {
      setCatalogueLoading(true);
      try {
        const res = await fetch("/api/proxy/ops");
        const body: { catalogue: string } = await res.json();
        setCatalogue(body.catalogue);
      } catch {
        setCatalogue("Could not load the catalogue.");
      } finally {
        setCatalogueLoading(false);
      }
    }
  }

  return (
    <div className="space-y-2">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
        placeholder={'{\n  "inputs": {},\n  "steps": [],\n  "outputs": []\n}'}
        rows={10}
        className="w-full resize-y rounded-md border border-border-strong bg-bg px-3.5 py-3 font-mono text-[12.5px] text-fg outline-none focus:border-accent"
      />

      <div className="flex items-center gap-2">
        <Button variant="secondary" size="sm" loading={validating} onClick={validate}>
          Validate
        </Button>
        <button
          type="button"
          onClick={toggleCatalogue}
          className="flex items-center gap-1 text-[12.5px] text-fg-secondary hover:text-fg"
        >
          <ChevronRight
            size={13}
            className={clsx("transition-transform", showCatalogue && "rotate-90")}
          />
          Op catalogue
        </button>
      </div>

      {parseError ? <ErrorBanner message={parseError} /> : null}

      {result ? (
        result.valid ? (
          <div className="space-y-2 rounded-md border border-status-succeeded/30 bg-status-succeeded/5 px-4 py-3 text-[13px]">
            <p className="flex items-center gap-1.5 font-medium text-status-succeeded">
              <CheckCircle2 size={15} />
              Valid: {result.steps} step{result.steps === 1 ? "" : "s"}, {result.runs} run
              {result.runs === 1 ? "" : "s"}, {result.cached} cached
            </p>
            <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-[12px] text-fg-secondary">
              {result.plan}
            </pre>
          </div>
        ) : (
          <ErrorBanner message="The spec is not valid." details={result.errors} />
        )
      ) : null}

      {showCatalogue ? (
        <pre className="max-h-64 overflow-auto rounded-md border border-border bg-bg-inset p-3 font-mono text-[11.5px] text-fg-secondary">
          {catalogueLoading ? "Loading..." : catalogue}
        </pre>
      ) : null}
    </div>
  );
}
