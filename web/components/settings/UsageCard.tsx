import type { Usage } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { formatNumber, formatDuration, formatDate } from "@/lib/format";

function Meter({ label, used, limit, format }: { label: string; used: number; limit: number | null; format: (n: number) => string }) {
  const fraction = limit ? Math.min(1, used / limit) : undefined;
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <p className="text-[13px] text-fg">{label}</p>
        <p className="text-[12.5px] text-fg-secondary">
          {format(used)} {limit !== null ? `/ ${format(limit)}` : "(no limit)"}
        </p>
      </div>
      <ProgressBar value={fraction} />
    </div>
  );
}

export function UsageCard({ usage }: { usage: Usage }) {
  return (
    <Card className="space-y-4 p-5">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[14px] font-semibold text-fg">Usage this month</h2>
        <p className="text-[12px] text-fg-tertiary">since {formatDate(usage.period_start)}</p>
      </div>
      <p className="text-[13px] text-fg-secondary">{formatNumber(usage.jobs)} jobs run</p>
      <Meter label="Model units" used={usage.units} limit={usage.units_limit} format={formatNumber} />
      <Meter
        label="Compute time"
        used={usage.compute_seconds}
        limit={usage.compute_seconds_limit}
        format={formatDuration}
      />
    </Card>
  );
}
