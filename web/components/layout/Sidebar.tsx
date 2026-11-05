"use client";

import { useEffect, useRef, useState } from "react";
import Link, { useLinkStatus } from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BookMarked,
  Drama,
  Loader2,
  Library,
  ListChecks,
  LogOut,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Plus,
  Settings,
  Trash2,
  Wand2,
} from "lucide-react";
import clsx from "clsx";
import { logout } from "@/app/(auth)/actions";
import type { Conversation } from "@/lib/types";
import { DeleteChatChoice, deleteChat, renameChat } from "@/components/chats/chatActions";

/** Studio and sidebar talk through these window events (the studio keeps its own URL). */
export const NEW_CHAT_EVENT = "atelier:new-chat";
export const CHATS_CHANGED_EVENT = "atelier:chats-changed";

const NAV = [
  { href: "/characters", label: "Characters", icon: Drama },
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

/** Inside a chat's link: a spinner while that chat opens, else when it was last used. */
function OpeningOr({ children }: { children: React.ReactNode }) {
  const { pending } = useLinkStatus();
  return pending ? <Loader2 size={12} className="shrink-0 animate-spin text-accent" /> : <>{children}</>;
}

function ChatRow({ chat, active, onChanged }: { chat: Conversation; active: boolean; onChanged: () => void }) {
  const router = useRouter();
  const [mode, setMode] = useState<"idle" | "menu" | "rename" | "confirm">("idle");
  const [name, setName] = useState(chat.title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (mode === "idle") return;
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setMode("idle");
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [mode]);

  async function rename() {
    const title = name.trim();
    if (!title || title === chat.title) return setMode("idle");
    setBusy(true);
    const err = await renameChat(chat.id, title);
    setBusy(false);
    if (err) return setError(err);
    setMode("idle");
    onChanged();
  }

  async function remove(files: boolean): Promise<string> {
    const err = await deleteChat(chat.id, files);
    if (err) return err;
    setMode("idle");
    onChanged();
    if (active) {
      window.dispatchEvent(new Event(NEW_CHAT_EVENT));
      router.push("/");
    }
    return "";
  }

  if (mode === "rename")
    return (
      <div ref={box} className="px-1 py-0.5">
        <input
          autoFocus
          aria-label="Chat name"
          value={name}
          maxLength={120}
          disabled={busy}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") rename();
            if (e.key === "Escape") setMode("idle");
          }}
          onBlur={rename}
          className="w-full rounded-sm border border-accent bg-bg px-2 py-1 text-[13px] text-fg outline-none"
        />
        {error && <p className="px-1 pt-0.5 text-[11px] text-red-600 dark:text-red-400">{error}</p>}
      </div>
    );

  return (
    <div ref={box} className="relative">
      <div
        className={clsx(
          "group flex items-center gap-2 rounded-sm px-2.5 py-1.5 text-[13px] transition-colors",
          active ? "bg-bg-inset text-fg" : "text-fg-secondary hover:bg-bg-inset hover:text-fg",
        )}
      >
        <Link href={`/studio/${chat.id}`} title={chat.title} className="flex min-w-0 flex-1 items-center gap-2">
          <MessageSquare size={13} className="shrink-0 opacity-60" />
          <span className="min-w-0 flex-1 truncate">{chat.title}</span>
          <OpeningOr>
            <span className="shrink-0 text-[10px] text-fg-tertiary group-hover:hidden">
              {chat.status === "running" || chat.status === "queued" ? "…" : ago(chat.updated_at)}
            </span>
          </OpeningOr>
        </Link>
        <button
          type="button"
          aria-label={`Options for ${chat.title}`}
          onClick={() => {
            setError("");
            setMode(mode === "menu" ? "idle" : "menu");
          }}
          className={clsx(
            "shrink-0 rounded-sm p-0.5 text-fg-tertiary hover:bg-bg hover:text-fg",
            mode === "menu" ? "block" : "hidden group-hover:block focus:block",
          )}
        >
          <MoreHorizontal size={14} />
        </button>
      </div>
      {mode === "menu" && (
        <div className="absolute right-1 top-8 z-20 w-40 rounded-md border border-border bg-bg p-1 shadow-lg">
          <button
            type="button"
            onClick={() => {
              setName(chat.title);
              setMode("rename");
            }}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-[13px] text-fg-secondary hover:bg-bg-inset hover:text-fg"
          >
            <Pencil size={13} /> Rename
          </button>
          <button
            type="button"
            onClick={() => setMode("confirm")}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-[13px] text-red-600 dark:text-red-400 hover:bg-bg-inset"
          >
            <Trash2 size={13} /> Delete
          </button>
        </div>
      )}
      {mode === "confirm" && (
        <div className="absolute inset-x-0 top-8 z-20 rounded-md border border-border bg-bg p-3 shadow-lg">
          <DeleteChatChoice onDelete={remove} onCancel={() => setMode("idle")} />
        </div>
      )}
    </div>
  );
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
        <ChatRow
          key={c.id}
          chat={c}
          active={active === c.id}
          onChanged={() => window.dispatchEvent(new Event(CHATS_CHANGED_EVENT))}
        />
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
