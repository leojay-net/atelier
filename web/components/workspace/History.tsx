"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Drama, Library, LogOut, Plus, Settings } from "lucide-react";
import { logout } from "@/app/(auth)/actions";
import { RecentChats } from "@/components/layout/Sidebar";

const LINKS = [
  { href: "/characters", label: "Characters", icon: Drama },
  { href: "/library", label: "Library", icon: Library },
  { href: "/settings", label: "Settings", icon: Settings },
];

/** The far-left panel: every project, newest first, and the other places in the app. */
export function History({ active }: { active: string | null }) {
  const router = useRouter();
  const pathname = usePathname();
  return (
    <div className="flex h-full min-w-0 flex-col" data-tour="history">
      <div className="px-3 pb-2 pt-3">
        <button
          type="button"
          onClick={() => router.push("/")}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-accent px-3 py-2 text-[13px] font-semibold text-on-accent transition-colors hover:bg-accent-hover"
        >
          <Plus size={15} /> New project
        </button>
      </div>
      <p className="px-4 pb-1 pt-2 text-[10.5px] font-semibold uppercase tracking-wider text-fg-tertiary">
        History
      </p>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <RecentChats active={active} />
      </div>
      <nav className="flex items-center justify-between gap-1 border-t border-border px-2 py-2">
        {LINKS.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            title={label}
            aria-label={label}
            className={
              "grid h-8 flex-1 place-items-center rounded-md transition-colors hover:bg-bg-inset hover:text-fg " +
              (pathname.startsWith(href) ? "text-accent" : "text-fg-secondary")
            }
          >
            <Icon size={16} />
          </Link>
        ))}
        <form action={logout} className="flex-1">
          <button
            type="submit"
            title="Sign out"
            aria-label="Sign out"
            className="grid h-8 w-full place-items-center rounded-md text-fg-secondary transition-colors hover:bg-bg-inset hover:text-fg"
          >
            <LogOut size={16} />
          </button>
        </form>
      </nav>
    </div>
  );
}
