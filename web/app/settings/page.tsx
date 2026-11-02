import { api } from "@/lib/api";
import { KeyIdentityCard } from "@/components/settings/KeyIdentityCard";
import { UsageCard } from "@/components/settings/UsageCard";
import { BrandKitForm } from "@/components/settings/BrandKitForm";

// Always live data from Django, never a build-time static render.
export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  const [me, usage, brandKit] = await Promise.all([
    api.me(),
    api.getUsage(),
    api.getBrandKit(),
  ]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-fg">Keys</h1>
        <p className="mt-1 text-[13px] text-fg-secondary">
          The organization and key this app is configured with, this month&apos;s usage, and your
          brand kit.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <KeyIdentityCard me={me} />
        <UsageCard usage={usage} />
      </div>

      <BrandKitForm initial={brandKit} />
    </div>
  );
}
