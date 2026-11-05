"use client";

import { useState } from "react";

/** Rename a chat (any message id in it). Returns an error message, or "" on success. */
export async function renameChat(id: string, title: string): Promise<string> {
  const r = await fetch(`/api/proxy/conversations/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title }),
  }).catch(() => null);
  return r?.ok ? "" : "Couldn't rename it. Try again.";
}

/** Delete a chat; with `files`, also what it made. Returns an error message, or "". */
export async function deleteChat(id: string, files: boolean): Promise<string> {
  const r = await fetch(`/api/proxy/conversations/${id}${files ? "?files=true" : ""}`, {
    method: "DELETE",
  }).catch(() => null);
  return r?.ok ? "" : "Couldn't delete it. Try again.";
}

/** "Delete this chat?" with the two choices: keep what it made, or delete that too. */
export function DeleteChatChoice({
  onDelete,
  onCancel,
}: {
  onDelete: (files: boolean) => Promise<string>;
  onCancel: () => void;
}) {
  const [busy, setBusy] = useState<"" | "chat" | "files">("");
  const [error, setError] = useState("");

  async function go(files: boolean) {
    setBusy(files ? "files" : "chat");
    setError(await onDelete(files));
    setBusy("");
  }

  return (
    <div className="space-y-2">
      <p className="text-[13px] font-medium text-fg">Delete this chat?</p>
      <p className="text-[12px] text-fg-secondary">
        Choose whether the videos, pictures and sound it made go too. Files you uploaded are always
        kept.
      </p>
      {error ? <p className="text-[11px] text-red-600 dark:text-red-400">{error}</p> : null}
      <div className="flex flex-col gap-1.5 pt-1">
        <button
          type="button"
          disabled={!!busy}
          onClick={() => go(false)}
          className="rounded-sm border border-border px-2.5 py-1.5 text-left text-[12px] text-fg hover:bg-bg-inset disabled:opacity-60"
        >
          {busy === "chat" ? "Deleting…" : "Delete the chat, keep what it made"}
          <span className="block text-[11px] text-fg-tertiary">Its results stay in your Library</span>
        </button>
        <button
          type="button"
          disabled={!!busy}
          onClick={() => go(true)}
          className="rounded-sm bg-red-600 px-2.5 py-1.5 text-left text-[12px] font-medium text-white hover:bg-red-700 disabled:opacity-60"
        >
          {busy === "files" ? "Deleting…" : "Delete the chat and what it made"}
          <span className="block text-[11px] font-normal text-white/80">This can&apos;t be undone</span>
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="self-end px-2 py-1 text-[12px] text-fg-secondary hover:text-fg"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
