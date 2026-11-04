"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BookMarked,
  Library,
  ListChecks,
  LogOut,
  MessageSquare,
  Plus,
  Settings,
  Wand2,
} from "lucide-react";
import clsx from "clsx";
import { logout } from "@/app/(auth)/actions";
import type { Conversation } from "@/lib/types";

/** Studio and sidebar talk through these window events (the studio keeps its own URL). */
export const NEW_CHAT_EVENT = "atelier:new-chat";
export const CHATS_CHANGED_EVENT = "atelier:chats-changed";

const NAV = [
  { href: "/library", label: "Library", icon: Library },
  { href: "/jobs", label: "Activity", icon: ListChecks },
  { href: "/recipes", label: "Recipes", icon: BookMarked },
  { href: "/settings", label: "Settings", icon: Settings },
];

function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function RecentChats({ active }: { active: string | null }) {
  const [chats, setChats] = useState<Conversation[] | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch("/api/proxy/conversations?limit=30")
        .then((r) => (r.ok ? r.json() : { items: [] }))
        .then((d: { items: Conversation[] }) => alive && setChats(d.items))
        .catch(() => alive && setChats([]));
    load();
    window.addEventListener(CHATS_CHANGED_EVENT, load);
    return () => {
      alive = false;
      window.removeEventListener(CHATS_CHANGED_EVENT, load);
    };
  }, []);

  if (chats === null) return null;
  if (!chats.length)
    return <p className="px-2.5 py-1 text-[12px] text-fg-tertiary">Your chats will show up here.</p>;
  return (
    <div className="flex flex-col gap-0.5">
      {chats.map((c) => (
        <Link
          key={c.id}
          href={`/studio/${c.id}`}
          title={c.title}
          className={clsx(
            "group flex items-center gap-2 rounded-sm px-2.5 py-1.5 text-[13px] transition-colors",
            active === c.id ? "bg-bg-inset text-fg" : "text-fg-secondary hover:bg-bg-inset hover:text-fg",
          )}
        >
          <MessageSquare size={13} className="shrink-0 opacity-60" />
          <span className="min-w-0 flex-1 truncate">{c.title}</span>
          <span className="shrink-0 text-[10px] text-fg-tertiary">
            {c.status === "running" || c.status === "queued" ? "…" : ago(c.updated_at)}
          </span>
        </Link>
      ))}
    </div>
  );
}

export function Sidebar({ email }: { email: string }) {
  const pathname = usePathname();
  const router = useRouter();
  const inStudio = pathname === "/" || pathname.startsWith("/studio");
  const activeChat = pathname.startsWith("/studio/") ? pathname.split("/")[2] ?? null : null;

  function newChat() {
    window.dispatchEvent(new Event(NEW_CHAT_EVENT)); // resets the studio if it is open
    router.push("/");
  }

  return (
    <aside className="flex h-full w-56 shrink-0 flex-col border-r border-border bg-bg-subtle">
      <div className="flex h-14 items-center px-5">
        <span className="text-[15px] font-semibold tracking-tight text-fg">Atelier</span>
      </div>

      <div className="px-3">
        <button
          type="button"
          onClick={newChat}
          className={clsx(
            "flex w-full items-center gap-2.5 rounded-sm px-2.5 py-2 text-sm font-medium transition-colors",
            inStudio && !activeChat
              ? "bg-accent-subtle-bg text-accent"
              : "text-fg-secondary hover:bg-bg-inset hover:text-fg",
          )}
        >
          <Wand2 size={17} strokeWidth={2} />
          <span className="flex-1 text-left">Create</span>
          <Plus size={14} className="opacity-60" />
        </button>
      </div>

      <div className="mt-1 min-h-0 flex-1 overflow-y-auto px-3 pb-2">
        <p className="px-2.5 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-fg-tertiary">
          Recent chats
        </p>
        <RecentChats active={activeChat} />
      </div>

      <nav className="flex flex-col gap-0.5 border-t border-border px-3 py-2">
        {NAV.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={clsx(
              "flex items-center gap-2.5 rounded-sm px-2.5 py-2 text-sm font-medium transition-colors",
              pathname.startsWith(href)
                ? "bg-accent-subtle-bg text-accent"
                : "text-fg-secondary hover:bg-bg-inset hover:text-fg",
            )}
          >
            <Icon size={17} strokeWidth={2} />
            {label}
          </Link>
        ))}
      </nav>

      <div className="border-t border-border px-3 py-3">
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
