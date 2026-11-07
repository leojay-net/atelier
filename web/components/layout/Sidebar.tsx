"use client";

import { useEffect, useRef, useState } from "react";
import Link, { useLinkStatus } from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BookMarked,
  Drama,
  Loader2,
  Library,
  Menu,
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
import { Logo } from "./Logo";
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
          "group flex items-center gap-2 rounded-md px-3 py-2 text-[13px] transition-colors",
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

export function RecentChats({ active }: { active: string | null }) {
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
    <div className="stagger flex flex-col gap-0.5">
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
  const [open, setOpen] = useState(false);
  const inStudio = pathname === "/" || pathname.startsWith("/studio");
  const activeChat = pathname.startsWith("/studio/") ? pathname.split("/")[2] ?? null : null;

  function newChat() {
    router.push("/");
  }

  // a project has its own workspace (with its own History panel on the left)
  if (pathname.startsWith("/studio")) return null;

  return (
    <>
    <header className="fixed inset-x-0 top-0 z-30 flex h-14 items-center gap-2.5 border-b border-border bg-bg/90 px-4 backdrop-blur-md md:hidden">
      <button type="button" aria-label="Open the menu" onClick={() => setOpen(true)} className="-ml-1.5 grid h-9 w-9 place-items-center rounded-full text-fg-secondary hover:bg-bg-inset hover:text-fg">
        <Menu size={20} />
      </button>
      <Logo size={24} />
      <span className="text-[15px] font-semibold tracking-[-0.02em] text-fg">Atelier</span>
    </header>
    <div
      aria-hidden
      onClick={() => setOpen(false)}
      className={clsx(
        "fixed inset-0 z-40 bg-black/45 backdrop-blur-[3px] transition-opacity duration-300 md:hidden",
        open ? "opacity-100" : "pointer-events-none opacity-0",
      )}
    />
    <aside
      onClick={(e) => {
        if ((e.target as HTMLElement).closest("a, button[type=submit]")) setOpen(false);
      }}
      className={clsx(
        "fixed inset-y-0 left-0 z-50 flex h-full w-72 shrink-0 flex-col border-r border-border bg-bg-subtle transition-transform duration-300 ease-[var(--ease-out)] md:static md:z-auto md:w-60 md:translate-x-0",
        open ? "translate-x-0 shadow-[var(--shadow-dialog)]" : "-translate-x-full",
      )}
    >
      <div className="flex h-16 items-center gap-2.5 px-5">
        <Logo size={26} />
        <span className="text-[16px] font-semibold tracking-[-0.02em] text-fg">Atelier</span>
      </div>

      <div className="px-3">
        <button
          type="button"
          onClick={() => {
            setOpen(false);
            newChat();
          }}
          className={clsx(
            "flex w-full items-center gap-2.5 rounded-md px-3 py-2.5 text-[14px] font-medium transition-colors",
            inStudio && !activeChat
              ? "bg-accent text-on-accent shadow-[0_1px_2px_rgba(0,0,0,0.14)] hover:bg-accent-hover"
              : "text-fg-secondary hover:bg-bg-inset hover:text-fg",
          )}
        >
          <Wand2 size={17} strokeWidth={2} />
          <span className="flex-1 text-left">Create</span>
          <Plus size={14} className="opacity-60" />
        </button>
      </div>

      <div className="mt-1 min-h-0 flex-1 overflow-y-auto px-3 pb-2">
        <p className="px-3 pb-1.5 pt-4 text-[11px] font-medium tracking-wide text-fg-tertiary">
          Recent
        </p>
        <RecentChats active={activeChat} />
      </div>

      <nav className="flex flex-col gap-0.5 border-t border-border px-3 py-3">
        {NAV.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            data-tour={`nav-${label.toLowerCase()}`}
            className={clsx(
              "flex items-center gap-2.5 rounded-md px-3 py-2 text-[14px] font-medium transition-colors",
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
        <p className="truncate px-3 text-[12px] text-fg-tertiary" title={email}>
          {email}
        </p>
        <form action={logout}>
          <button
            type="submit"
            className="mt-1 flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-[14px] font-medium text-fg-secondary transition-colors hover:bg-bg-inset hover:text-fg"
          >
            <LogOut size={17} strokeWidth={2} />
            Sign out
          </button>
        </form>
      </div>
    </aside>
    </>
  );
}
