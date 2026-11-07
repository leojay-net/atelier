import { api } from "@/lib/api";
import { UsageCard } from "@/components/settings/UsageCard";
import { BrandKitForm } from "@/components/settings/BrandKitForm";
import { MyVoiceCard } from "@/components/settings/MyVoiceCard";
import { ChatsCard } from "@/components/settings/ChatsCard";

// Always live data from Django, never a build-time static render.
export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  const [usage, brandKit] = await Promise.all([api.getUsage(), api.getBrandKit()]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="animate-rise text-[30px] font-semibold leading-tight tracking-[-0.03em] text-fg">Settings</h1>
        <p className="mt-1 text-[13px] text-fg-secondary">
          Your voice, your chats, this month&apos;s usage and your brand kit.
        </p>
      </div>

      <MyVoiceCard />
      <ChatsCard />
      <UsageCard usage={usage} />
      <BrandKitForm initial={brandKit} />
    </div>
  );
}
