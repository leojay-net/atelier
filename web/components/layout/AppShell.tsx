import type { ReactNode } from "react";
import { sessionEmail } from "@/lib/server/session";
import { Sidebar } from "./Sidebar";

export async function AppShell({ children }: { children: ReactNode }) {
  const email = await sessionEmail();
  if (!email) {
    // Signed out: only /login and /signup render here (proxy.ts sends everything else there).
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-bg-subtle px-4 py-10">
        <span className="text-[17px] font-semibold tracking-tight text-fg">Atelier</span>
        {children}
      </main>
    );
  }
  return (
    <div className="flex h-screen">
      <Sidebar email={email} />
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-8 py-8">{children}</div>
      </main>
    </div>
  );
}
