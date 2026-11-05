"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { MessageSquare, Pencil, Trash2 } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { DeleteChatChoice, deleteChat, renameChat } from "@/components/chats/chatActions";
import { CHATS_CHANGED_EVENT } from "@/components/layout/Sidebar";
import type { Conversation } from "@/lib/types";

function Row({ chat, onChanged }: { chat: Conversation; onChanged: () => void }) {
  const [mode, setMode] = useState<"idle" | "rename" | "delete">("idle");
  const [name, setName] = useState(chat.title);
  const [error, setError] = useState("");

  async function rename() {
    const title = name.trim();
    if (!title || title === chat.title) return setMode("idle");
    const err = await renameChat(chat.id, title);
    if (err) return setError(err);
    setMode("idle");
    onChanged();
  }

  return (
    <li className="py-2.5">
      <div className="flex items-center gap-2.5">
        <MessageSquare size={14} className="shrink-0 text-fg-tertiary" />
        {mode === "rename" ? (
          <input
            autoFocus
            aria-label="Chat name"
            value={name}
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") rename();
              if (e.key === "Escape") setMode("idle");
            }}
            onBlur={rename}
            className="min-w-0 flex-1 rounded-sm border border-accent bg-bg px-2 py-1 text-[13px] text-fg outline-none"
          />
        ) : (
          <Link href={`/studio/${chat.id}`} className="min-w-0 flex-1 truncate text-[13px] text-fg hover:underline">
            {chat.title}
          </Link>
        )}
        <span className="shrink-0 text-[11px] text-fg-tertiary">
          {new Date(chat.updated_at).toLocaleDateString()}
        </span>
        <button
          type="button"
          aria-label={`Rename ${chat.title}`}
          onClick={() => {
            setName(chat.title);
            setMode("rename");
          }}
          className="rounded-sm p-1 text-fg-tertiary hover:bg-bg-inset hover:text-fg"
        >
          <Pencil size={14} />
        </button>
        <button
          type="button"
          aria-label={`Delete ${chat.title}`}
          onClick={() => setMode(mode === "delete" ? "idle" : "delete")}
          className="rounded-sm p-1 text-fg-tertiary hover:bg-bg-inset hover:text-red-600"
        >
          <Trash2 size={14} />
        </button>
      </div>
      {mode === "delete" ? (
        <div className="ml-6 mt-2 max-w-sm rounded-md border border-border p-3">
          <DeleteChatChoice
            onCancel={() => setMode("idle")}
            onDelete={async (files) => {
              const err = await deleteChat(chat.id, files);
              if (!err) onChanged();
              return err;
            }}
          />
        </div>
      ) : null}
      {error ? <p className="ml-6 text-[11px] text-red-600 dark:text-red-400">{error}</p> : null}
    </li>
  );
}

/** Every chat, to rename or delete (the same as the menu on each chat in the sidebar). */
export function ChatsCard() {
  const [chats, setChats] = useState<Conversation[] | null>(null);
  const load = useCallback(() => {
    fetch("/api/proxy/conversations?limit=100")
      .then((r) => (r.ok ? r.json() : { items: [] }))
      .then((d: { items: Conversation[] }) => setChats(d.items))
      .catch(() => setChats([]));
  }, []);
  useEffect(load, [load]);

  const changed = () => {
    load();
    window.dispatchEvent(new Event(CHATS_CHANGED_EVENT));
  };

  return (
    <Card className="p-5">
      <h2 className="text-[14px] font-semibold text-fg">Your chats</h2>
      <p className="mt-1 text-[13px] text-fg-secondary">Rename a chat, or delete it with or without what it made.</p>
      {chats === null ? null : chats.length === 0 ? (
        <p className="mt-3 text-[13px] text-fg-tertiary">No chats yet.</p>
      ) : (
        <ul className="mt-2 divide-y divide-border">
          {chats.map((c) => (
            <Row key={c.id} chat={c} onChanged={changed} />
          ))}
        </ul>
      )}
    </Card>
  );
}
