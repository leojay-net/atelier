import type { ReactNode } from "react";
import { sessionEmail } from "@/lib/server/session";
import { Logo } from "./Logo";
import { Sidebar } from "./Sidebar";

export async function AppShell({ children }: { children: ReactNode }) {
  const email = await sessionEmail();
  if (!email) {
    // Signed out: only /login and /signup render here (proxy.ts sends everything else there).
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-7 bg-bg-subtle px-4 py-10">
        <div className="animate-pop flex flex-col items-center gap-3">
          <Logo size={48} />
          <span className="text-[19px] font-semibold tracking-tight text-fg">Atelier</span>
        </div>
        {children}
      </main>
    );
  }
  return (
    <div className="flex h-screen">
      <Sidebar email={email} />
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-5 pb-10 pt-[4.5rem] md:px-10 md:py-10">{children}</div>
      </main>
    </div>
  );
}
