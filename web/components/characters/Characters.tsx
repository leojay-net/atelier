"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import clsx from "clsx";
import { Clapperboard, Loader2, MapPin, Pencil, Plus, RefreshCw, Trash2, User, Volume2 } from "lucide-react";
import type { CastKind, CastMember } from "@/lib/types";
import { contentUrl } from "@/components/media/MediaPreview";
import { readApiError } from "@/lib/errors";
import { RETRY_STORAGE_KEY } from "@/components/jobs/JobDetail";

const VOICE_IDEAS = [
  "a cheerful, excited 10-year-old boy",
  "a curious, gentle 8-year-old girl",
  "a tiny, beeping friendly robot",
  "a warm, wise grandmother",
  "a deep, booming but kind giant",
];

const LOOK_HINT: Record<CastKind, string> = {
  character:
    "Who they are and what they look like: age, body, face, hair, clothes and colors. e.g. A cheerful 10-year-old boy with short curly hair, big brown eyes, a bright orange hoodie and white sneakers",
  place:
    "The place the scenes happen in: what is there, the colors, the time of day. e.g. A colorful futuristic market street at golden hour, fruit stalls with bright umbrellas and hanging lanterns",
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error((await readApiError(res)).message);
  return res.json() as Promise<T>;
}

function NewMember({ onMade }: { onMade: (c: CastMember) => void }) {
  const [kind, setKind] = useState<CastKind>("character");
  const [name, setName] = useState("");
  const [look, setLook] = useState("");
  const [voice, setVoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function create() {
    setBusy(true);
    setError("");
    try {
      const made = await json<CastMember>(
        await fetch("/api/proxy/characters", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name: name.trim(), kind, look: look.trim(), voice: kind === "character" ? voice.trim() : "" }),
        }),
      );
      onMade(made);
      setName("");
      setLook("");
      setVoice("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-bg p-5">
      <div className="flex items-center gap-2">
        {(["character", "place"] as const).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setKind(k)}
            className={clsx(
              "flex items-center gap-1.5 rounded-full px-3 py-1 text-[13px] font-medium",
              kind === k ? "bg-accent text-on-accent" : "bg-bg-subtle text-fg-secondary hover:text-fg",
            )}
          >
            {k === "character" ? <User size={13} /> : <MapPin size={13} />}
            {k === "character" ? "A character" : "A place"}
          </button>
        ))}
      </div>
      <div className="mt-4 grid gap-3">
        <input
          aria-label="Name"
          placeholder={kind === "character" ? "Name, e.g. Kofi" : "Name, e.g. The Market"}
          value={name}
          maxLength={60}
          onChange={(e) => setName(e.target.value)}
          className="rounded-md border border-border bg-bg px-3 py-2 text-[14px] text-fg outline-none focus:border-accent"
        />
        <textarea
          aria-label="What it looks like"
          placeholder={LOOK_HINT[kind]}
          value={look}
          rows={3}
          maxLength={1200}
          onChange={(e) => setLook(e.target.value)}
          className="resize-none rounded-md border border-border bg-bg px-3 py-2 text-[14px] text-fg outline-none focus:border-accent"
        />
        {kind === "character" ? (
          <div className="space-y-1.5">
            <input
              aria-label="Their voice"
              placeholder="Their voice, e.g. a cheerful 10-year-old boy with a light Nigerian accent"
              value={voice}
              maxLength={400}
              onChange={(e) => setVoice(e.target.value)}
              className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[14px] text-fg outline-none focus:border-accent"
            />
            <div className="flex flex-wrap gap-1.5">
              {VOICE_IDEAS.map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setVoice(v)}
                  className="rounded-full border border-border px-2 py-0.5 text-[11px] text-fg-secondary hover:border-accent hover:text-fg"
                >
                  {v}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
      {error ? <p className="mt-2 text-[12px] text-red-600 dark:text-red-400">{error}</p> : null}
      <button
        type="button"
        onClick={create}
        disabled={busy || !name.trim() || look.trim().length < 3}
        className="mt-4 inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-[14px] font-medium text-on-accent disabled:opacity-40"
      >
        {busy ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
        {busy ? "Drawing the design…" : kind === "character" ? "Create character" : "Create place"}
      </button>
    </div>
  );
}

function MemberCard({
  member,
  onChange,
  onRemoved,
}: {
  member: CastMember;
  onChange: (c: CastMember) => void;
  onRemoved: () => void;
}) {
  const router = useRouter();
  const [mode, setMode] = useState<"view" | "edit" | "delete">("view");
  const [look, setLook] = useState(member.look);
  const [voice, setVoice] = useState(member.voice);
  const [name, setName] = useState(member.name);
  const [busy, setBusy] = useState<"" | "save" | "redraw" | "voice" | "delete">("");
  const [error, setError] = useState("");
  const player = useRef<HTMLAudioElement | null>(null);

  async function patch(body: Record<string, unknown>, what: "save" | "redraw") {
    setBusy(what);
    setError("");
    try {
      onChange(
        await json<CastMember>(
          await fetch(`/api/proxy/characters/${member.id}`, {
            method: "PATCH",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body),
          }),
        ),
      );
      setMode("view");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work. Try again.");
    } finally {
      setBusy("");
    }
  }

  async function hear() {
    setBusy("voice");
    setError("");
    try {
      const r = await fetch(`/api/proxy/characters/${member.id}/voice`);
      if (!r.ok) throw new Error((await readApiError(r)).message);
      const url = URL.createObjectURL(await r.blob());
      player.current?.pause();
      player.current = new Audio(url);
      await player.current.play();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't play the voice.");
    } finally {
      setBusy("");
    }
  }

  async function remove() {
    setBusy("delete");
    const r = await fetch(`/api/proxy/characters/${member.id}`, { method: "DELETE" }).catch(() => null);
    setBusy("");
    if (r?.ok) onRemoved();
    else setError("Couldn't delete it. Try again.");
  }

  function animate() {
    const who = member.kind === "place" ? `set in ${member.name}` : `with ${member.name}`;
    sessionStorage.setItem(RETRY_STORAGE_KEY, JSON.stringify({ prompt: `Animate a short scene ${who}: ` }));
    router.push("/");
  }

  return (
    <div className="flex flex-col overflow-hidden rounded-xl border border-border bg-bg">
      <div className={clsx("relative bg-bg-inset", member.kind === "place" ? "aspect-video" : "aspect-square")}>
        {/* eslint-disable-next-line @next/next/no-img-element -- an asset served by the API */}
        <img src={contentUrl(member.image)} alt={member.name} className="h-full w-full object-cover" />
        {busy === "redraw" ? (
          <div className="absolute inset-0 flex items-center justify-center bg-black/40 text-white">
            <Loader2 className="animate-spin" />
          </div>
        ) : null}
        <span className="absolute left-2 top-2 rounded-full bg-black/60 px-2 py-0.5 text-[11px] text-white">
          {member.kind === "place" ? "Place" : "Character"}
        </span>
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        {mode === "edit" ? (
          <div className="space-y-2">
            <input
              aria-label="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-md border border-border bg-bg px-2 py-1 text-[14px] font-semibold text-fg outline-none focus:border-accent"
            />
            <textarea
              aria-label="What it looks like"
              value={look}
              rows={4}
              onChange={(e) => setLook(e.target.value)}
              className="w-full resize-none rounded-md border border-border bg-bg px-2 py-1 text-[13px] text-fg outline-none focus:border-accent"
            />
            {member.kind === "character" ? (
              <input
                aria-label="Their voice"
                value={voice}
                onChange={(e) => setVoice(e.target.value)}
                className="w-full rounded-md border border-border bg-bg px-2 py-1 text-[13px] text-fg outline-none focus:border-accent"
              />
            ) : null}
            <p className="text-[11px] text-fg-tertiary">Changing the look draws a new design.</p>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={!!busy}
                onClick={() => patch({ name, look, voice }, look !== member.look ? "redraw" : "save")}
                className="rounded-md bg-accent px-3 py-1 text-[13px] font-medium text-on-accent disabled:opacity-50"
              >
                {busy === "save" || busy === "redraw" ? "Saving…" : "Save"}
              </button>
              <button type="button" onClick={() => setMode("view")} className="px-2 text-[13px] text-fg-secondary">
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <>
            <p className="text-[15px] font-semibold text-fg">{member.name}</p>
            <p className="line-clamp-3 text-[12.5px] text-fg-secondary">{member.look}</p>
            {member.voice ? (
              <p className="text-[12px] text-fg-tertiary">
                <span className="font-medium">Voice:</span> {member.voice}
              </p>
            ) : null}
          </>
        )}
        {error ? <p className="text-[12px] text-red-600 dark:text-red-400">{error}</p> : null}
        {mode === "delete" ? (
          <div className="rounded-md border border-border p-2.5">
            <p className="text-[13px] text-fg">Remove {member.name} from your cast?</p>
            <p className="text-[11px] text-fg-tertiary">Videos already made keep them; the design stays in your Library.</p>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={remove}
                className="rounded-md bg-red-600 px-3 py-1 text-[12px] font-medium text-white hover:bg-red-700"
              >
                {busy === "delete" ? "Removing…" : "Remove"}
              </button>
              <button type="button" onClick={() => setMode("view")} className="px-2 text-[12px] text-fg-secondary">
                Cancel
              </button>
            </div>
          </div>
        ) : null}
        {mode === "view" ? (
          <div className="mt-auto flex flex-wrap items-center gap-1 pt-2">
            <button
              type="button"
              onClick={animate}
              className="inline-flex items-center gap-1.5 rounded-md bg-accent px-2.5 py-1 text-[12px] font-medium text-on-accent"
            >
              <Clapperboard size={13} /> Animate
            </button>
            {member.kind === "character" ? (
              <IconButton label="Hear their voice" onClick={hear} busy={busy === "voice"}>
                <Volume2 size={14} />
              </IconButton>
            ) : null}
            <IconButton label="Draw a new design" onClick={() => patch({ redesign: true }, "redraw")} busy={busy === "redraw"}>
              <RefreshCw size={14} />
            </IconButton>
            <IconButton label="Edit" onClick={() => setMode("edit")}>
              <Pencil size={14} />
            </IconButton>
            <IconButton label="Remove" onClick={() => setMode("delete")}>
              <Trash2 size={14} />
            </IconButton>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function IconButton({
  label,
  onClick,
  busy,
  children,
}: {
  label: string;
  onClick: () => void;
  busy?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={busy}
      className="rounded-md p-1.5 text-fg-secondary hover:bg-bg-inset hover:text-fg disabled:opacity-50"
    >
      {busy ? <Loader2 size={14} className="animate-spin" /> : children}
    </button>
  );
}

export function CharactersView() {
  const [cast, setCast] = useState<CastMember[] | null>(null);
  const load = useCallback(() => {
    fetch("/api/proxy/characters")
      .then((r) => (r.ok ? r.json() : { items: [] }))
      .then((d: { items: CastMember[] }) => setCast(d.items))
      .catch(() => setCast([]));
  }, []);
  useEffect(load, [load]);

  const people = (cast ?? []).filter((c) => c.kind === "character");
  const places = (cast ?? []).filter((c) => c.kind === "place");
  const replace = (c: CastMember) => setCast((cur) => (cur ?? []).map((x) => (x.id === c.id ? c : x)));
  const removeId = (id: number) => setCast((cur) => (cur ?? []).filter((x) => x.id !== id));

  return (
    <div className="space-y-8">
      <div>
        <h1 className="animate-rise text-[30px] font-semibold leading-tight tracking-[-0.03em] text-fg">Characters</h1>
        <p className="mt-1 max-w-2xl text-[13px] text-fg-secondary">
          The cast of your animated stories: characters and the places they live in. Each one gets a design that keeps
          them looking the same in every scene and every episode. Then go to Create and ask, for example:{" "}
          <span className="text-fg">&ldquo;Make episode 1: introduce Kofi and Bolt in the market.&rdquo;</span> You can
          also ask the AI there to create characters for you.
        </p>
      </div>

      <NewMember onMade={(c) => setCast((cur) => [...(cur ?? []), c])} />

      {cast === null ? (
        <Loader2 className="animate-spin text-fg-tertiary" />
      ) : cast.length === 0 ? (
        <p className="text-[13px] text-fg-tertiary">No characters yet. Create your first one above.</p>
      ) : (
        <>
          {people.length ? (
            <section>
              <h2 className="mb-3 text-[14px] font-semibold text-fg">Characters</h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {people.map((c) => (
                  <MemberCard key={c.id} member={c} onChange={replace} onRemoved={() => removeId(c.id)} />
                ))}
              </div>
            </section>
          ) : null}
          {places.length ? (
            <section>
              <h2 className="mb-3 text-[14px] font-semibold text-fg">Places</h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {places.map((c) => (
                  <MemberCard key={c.id} member={c} onChange={replace} onRemoved={() => removeId(c.id)} />
                ))}
              </div>
            </section>
          ) : null}
        </>
      )}
    </div>
  );
}
