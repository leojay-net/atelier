"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Image as ImageIcon, ListChecks, BookMarked, LogOut, Settings, Wand2 } from "lucide-react";
import { logout } from "@/app/(auth)/actions";
import clsx from "clsx";

const NAV = [
  { href: "/", label: "Create", icon: Wand2, exact: true },
  { href: "/assets", label: "Assets", icon: ImageIcon },
  { href: "/jobs", label: "Jobs", icon: ListChecks },
  { href: "/recipes", label: "Recipes", icon: BookMarked },
  { href: "/settings", label: "Keys", icon: Settings },
];

export function Sidebar({ email }: { email: string }) {
  const pathname = usePathname();

  return (
    <aside className="flex h-full w-56 shrink-0 flex-col border-r border-border bg-bg-subtle">
      <div className="flex h-14 items-center px-5">
        <span className="text-[15px] font-semibold tracking-tight text-fg">Atelier</span>
      </div>
      <nav className="flex flex-col gap-0.5 px-3 py-2">
        {NAV.map(({ href, label, icon: Icon, exact }) => {
          const active = exact
            ? pathname === href || pathname.startsWith("/studio")
            : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex items-center gap-2.5 rounded-sm px-2.5 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-accent-subtle-bg text-accent"
                  : "text-fg-secondary hover:bg-bg-inset hover:text-fg",
              )}
            >
              <Icon size={17} strokeWidth={2} />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto border-t border-border px-3 py-3">
        <p className="truncate px-2.5 text-[12px] text-fg-secondary" title={email}>
          {email}
        </p>
        <form action={logout}>
          <button
            type="submit"
            className="mt-1 flex w-full items-center gap-2.5 rounded-sm px-2.5 py-2 text-sm font-medium text-fg-secondary transition-colors hover:bg-bg-inset hover:text-fg"
          >
            <LogOut size={17} strokeWidth={2} />
            Sign out
          </button>
        </form>
      </div>
    </aside>
  );
}
