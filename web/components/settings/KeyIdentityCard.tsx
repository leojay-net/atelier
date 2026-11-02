import type { Me } from "@/lib/types";
import { Card } from "@/components/ui/Card";

export function KeyIdentityCard({ me }: { me: Me }) {
  return (
    <Card className="space-y-4 p-5">
      <h2 className="text-[14px] font-semibold text-fg">Organization</h2>
      <dl className="grid grid-cols-2 gap-y-2 text-[13px]">
        <dt className="text-fg-secondary">Name</dt>
        <dd className="text-fg">{me.org.name}</dd>
        <dt className="text-fg-secondary">Slug</dt>
        <dd className="font-mono text-fg">{me.org.slug}</dd>
        <dt className="text-fg-secondary">Key</dt>
        <dd className="text-fg">{me.key.name}</dd>
        <dt className="text-fg-secondary">Key prefix</dt>
        <dd className="font-mono text-fg">{me.key.prefix}...</dd>
      </dl>
      <div className="space-y-1.5 border-t border-border pt-3.5">
        <p className="text-[12.5px] text-fg-secondary">
          Keys are not managed over the API. Rotate or add one from the repository root:
        </p>
        <pre className="overflow-x-auto rounded-sm bg-bg-inset p-2.5 font-mono text-[12px] text-fg">
          {`uv run python manage.py create_api_key ${me.org.slug} <name>`}
        </pre>
        <pre className="overflow-x-auto rounded-sm bg-bg-inset p-2.5 font-mono text-[12px] text-fg">
          {`uv run python manage.py create_api_key --revoke <prefix>`}
        </pre>
      </div>
    </Card>
  );
}
