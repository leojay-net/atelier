# 15. UI rework: a CapCut-style workspace with the AI chat at the side

**Status:** plan only, written 2026-11-06. Nothing in this document is built yet. The features it
reorganizes are all live (see "Where things stand" at the end).

## Why

The current UI works, but it doesn't look or feel like a video tool:

- The studio is a chat with a player next to it.
- The hand editor is a separate page (`/edit/[id]`) you open with an "Edit" button.
- The home page is an empty chat.
- New users get no guidance.

People already know the layout of CapCut, Premiere and DaVinci: projects on a start screen; a
workspace with media on the left, the player in the middle and the timeline below. We follow that
pattern and add what those tools don't have, the AI chat in a side panel. Prompting and editing
by hand happen in the same place, and both add versions to the same project.

## The three screens

### 1. Home: Projects (`/`)

What you see when you open the app (CapCut's start screen).

```
┌──────────┬──────────────────────────────────────────────────────────┐
│ Atelier  │   What do you want to make?                              │
│ + New    │   ┌──────────────────────────────────────────────────┐   │
│ Projects │   │ e.g. a 15-second reel from my photos with music  │📎 │
│ Characters│  └──────────────────────────────────────────────────┘   │
│ Library  │   🎙 Voice: Let the AI choose ▾            [ Create ▸ ]   │
│ Settings │                                                          │
│          │   Quick start:  [Reel from photos] [Birthday video]      │
│          │                 [Tutorial] [Animated story] [Slides]     │
│          │                                                          │
│          │   Your projects                                          │
│          │   ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐                        │
│          │   │thumb│ │thumb│ │thumb│ │ ... │   (⋯ rename / delete)  │
│          │   └─────┘ └─────┘ └─────┘ └─────┘                        │
│          │   Tolu's   Python  Friends                               │
│          │   Birthday Tutorial Reel                                 │
└──────────┴──────────────────────────────────────────────────────────┘
```

- **Prompt box:** text, attach files (drag and drop too), voice picker. "Create" makes the
  first job (`POST /jobs`) and opens the project at `/studio/<job id>`.
- **Quick-start cards:** each one fills the box with a starter request the user finishes, e.g.
  "Make a 15-second reel from these photos with upbeat music", or "Make episode 1 of my
  animated series introducing…" (this card also links to Characters).
- **Your projects:** a grid built from `GET /conversations` (it already returns `title`,
  `updated_at`, `status` and a `preview` {handle, media}). Each card shows:
  - the latest video (a muted thumbnail) or picture, else an icon for decks and documents;
  - the title and when it was last used, plus a "working…" badge while a job runs;
  - a ⋯ menu with Rename and Delete, reusing `components/chats/chatActions.tsx`, including the
    "delete the chat only / the chat and what it made" choice.
- **Sidebar on Home:** the full sidebar as today (Create, Recent chats, Characters, Library,
  Activity, Recipes, Settings). The Recent chats list can stay, since the grid is the main view.

### 2. Project: the workspace (`/studio/[id]`)

A full-height dark workspace.

```
┌──┬───────────────────────────────────────────────────────────┬──────────────┐
│🏠│ ← Projects   Tolu's Birthday Video ✎      ? Help  ⬇  [Save]│ AI assistant │
│🎭├──────────────┬────────────────────────────────────────────┤              │
│📚│ Media        │                                            │ You: make a  │
│⚙ │ Audio        │                                            │ birthday…    │
│  │ Text         │              PLAYER                        │              │
│  │ Effects      │      (preview of the selected version,     │ AI: Here's   │
│  │ Adjust       │       with the hand edits applied live)    │ your video…  │
│  │──────────────│                                            │              │
│  │ (the chosen  ├────────────────────────────────────────────┤              │
│  │  tab's panel)│ ⏵ 0:03.2/0:30  ✂ Split  🗑 Delete  ⇤ Start ⇥ End │      │
│  │              │ ▬▬▬▬▬▬▬▬▬ video ▬▬▬▬▬  ░░cut░░  ▬▬▬▬▬▬▬▬▬▬ │ [Ask for a   │
│  │              │ ▬▬ text ▬▬           ▬▬ text ▬▬            │  change…] 📎 │
│  │              │ ▬▬▬▬ voice ▬▬▬▬   ● ● sfx   ♪ music ♪♪♪♪♪♪ │ 🎙 Voice ▾   │
└──┴──────────────┴────────────────────────────────────────────┴──────────────┘
```

- **App sidebar → icon rail** (`w-14`) on `/studio/*`: Home, Characters, Library, Settings, with
  tooltips. It gives the workspace the screen. (`Sidebar` gets a `compact` mode from the
  pathname; `Studio`'s `fixed inset-y-0 left-56` becomes `left-14` there.)
- **Top bar:**
  - "← Projects" goes back to Home.
  - The project name, which can be edited in place (`PATCH /conversations/{id}`).
  - **? Help** replays the tour.
  - **Download** downloads the selected version.
  - **Save** ("Save as new version") is enabled only when there are unsaved hand edits.
- **Left panel, the CapCut tabs:**
  - **Media:** every version (V1, V2…, with thumbnails and "AI" or "Edited by hand" labels) and
    every upload. Clicking a version loads it in the player. "+ Add files" attaches files to the
    next chat message, as the paperclip does today.
  - **Audio:** the editor's Voiceover panel (type it with Make voiceover, or use a recording;
    choose the voice; start and volume) and its Music panel (Make music or My song; volume).
  - **Text:** the editor's Text panel.
  - **Effects:** the editor's Sound effects panel.
  - **Adjust:** Shape (with framing), Volume and Speed.
  - Trim and cut move to the timeline toolbar, where CapCut has them.
- **Centre:** the player. For videos it's the editor's preview, so hand edits show live, with
  the Web Audio sound. For pictures, decks and audio it's today's viewer, the timeline is hidden,
  and the Audio, Text, Effects and Adjust tabs are disabled with a tooltip saying "for videos".
- **Timeline (videos):**
  - **Toolbar:** play/pause, time, Split, Delete part, Start here, End here, Undo cuts.
  - **Tracks:** video (kept parts, cuts greyed), text (yellow bars), audio (the voiceover as a
    green bar, music as a full-length bar, sound effects as dots). Click a track to move the
    playhead.
  - **Phase 2:** drag bar edges to trim, drag voice and text bars to move them, and zoom.
- **Right panel: the AI assistant.** Today's chat column (messages, activity lines, attachments,
  voice picker, composer), about 380 px wide and collapsible to a tab on the right edge.
- **While the AI works:** the player shows the activity overlay ("Animating a scene…"), and the
  edit tools are disabled with a hint ("You can edit when I'm done").
- **When an edit or an AI turn finishes:** the new version is added to Media and selected in
  the player.

### 3. Characters, Library, Settings

These stay as they are, inside the full sidebar layout. Two small things: the Characters page's
"Animate" button opens Home with the prompt filled in (it already pre-fills the composer); and
the Library's cards could open the project they belong to (each item already has `job`).

## First-time guide (tour)

A short guided tour: a highlighted area plus a small card with Back, Next and Skip. It shows the
first time each screen is opened, and the **? Help** button replays it. Whether it has been seen
is a per-viewer convenience, so it lives in `localStorage` (wrapped in try/catch).

**Home tour** (`atelier:tour:home`):
1. The prompt box: "Tell me what to make: a reel, a birthday video, a tutorial, a story…"
2. 📎 Attach: "Add your photos, videos or a song, or paste a music link."
3. Voice picker: "Pick a voice for voiceovers: 11 accents, a woman or a man, or your own voice
   (Settings)."
4. Quick starts: "Not sure? Start from one of these."
5. Your projects: "Everything you make is saved here. ⋯ to rename or delete."
6. Characters (sidebar): "Create characters once, then animate them in any episode."

**Workspace tour** (`atelier:tour:studio`):
1. AI assistant: "Ask for anything, or ask for changes: 'make it shorter', 'add music'."
2. Player: "Your latest version plays here."
3. Media tab: "All versions and your files. Click one to open it."
4. Timeline: "Click to move the playhead, then Split and Delete to cut, like CapCut."
5. Audio, Text and Effects tabs: "Add a voiceover, music, titles and sound effects by hand. You
   hear them in the preview."
6. Save: "Saves your hand edits as a new version. The original is kept."

**How to build it:** a ~120-line `components/tour/Tour.tsx` with no library. Steps target
`[data-tour="…"]` elements. The cutout is a fixed full-screen overlay with a "hole" (a big
`box-shadow` on an absolutely positioned box over the target's `getBoundingClientRect()`), plus
a card placed beside it. Keys: Esc skips, ←/→ move between steps. It recalculates on resize, and
skips a step whose target isn't on screen.

## How to build it (file plan)

The aim is to reuse what exists. The hand editor (`components/editor/Editor.tsx`, about 1,100
lines) already has the panels, preview, timeline, Web Audio and save. It becomes the
workspace's centre. Nothing on the server changes.

1. **Split `Editor.tsx`:**
   - `useEdit(jobId, source)`: the edit state, the clock, cuts, make voiceover and music, save,
     and `dirty`.
   - `<EditPlayer>`: the preview, the text overlays, the fitted frame and the Web Audio
     (`previewAudio.ts` as is).
   - `<EditTimeline>`: the toolbar and the tracks.
   - `<AudioPanel>`, `<TextPanel>`, `<EffectsPanel>` and `<AdjustPanel>`: today's tab bodies.

   Keep `/edit/[id]` working by composing the same pieces (or redirect it to
   `/studio/<id>?asset=`).
2. **`components/studio/Workspace.tsx`:** the new layout (the top bar, left tabs with `<MediaPanel>`
   plus the panels above, the centre player and timeline, and the right chat). `Studio.tsx`
   keeps the chat logic (thread, LiveJob, send, uploads); its `Stage` is replaced by
   `Workspace`. On save, `useEdit` calls `onSaved(job)`: the job is appended to the thread
   (LiveJob shows its progress) and selected when done. There's no `router.push`.
3. **`components/home/Home.tsx`:** the prompt box (shares the composer, attachments and
   `VoicePicker` with Studio, so export those), the quick starts, and the projects grid.
   `app/page.tsx` renders `<Home />`.
4. **`Sidebar`:** add the `compact` icon rail for `/studio/*` and `/edit/*`.
5. **`components/tour/Tour.tsx`:** add the tour, plus `data-tour` attributes on the targets.
6. **Small fixes to fold in:**
   - The Usage card's "Model units" and "Compute time" become plain words ("Requests this month",
     "Editing time").
   - The sidebar's "opening" spinner can go, since the loading screen covers it.
   - Hide the "Edit" link once the workspace is in place.

**Mobile (below 768 px):** the workspace stacks: the player, the timeline, a tab bar for the
panels, and the chat as a bottom sheet behind an "AI" button. Home is a single column.

## How we'll know it works

Browser tests (Playwright, like `scratchpad/browser/editor.mjs` and `episode.mjs`), on the live
site, as a new user:

1. **First visit:** the Home tour appears, Next goes through its 6 steps, and it doesn't appear
   again after a reload. **?** replays it.
2. **Create a project from Home:** the prompt plus an attached clip creates the project, the
   workspace opens, the AI works with the activity on the player, and the result shows in Media
   as V1 and in the player.
3. **Edit by hand:**
   - Split and delete a part on the timeline; add a text, Make voiceover and Make music, and a
     whoosh.
   - Press play: the preview plays the sound (3 audio sources started).
   - Save: V2 appears in Media and is selected.
   - Check on the server: the length, the text times, and sound at the voice and whoosh times.
4. **Ask the AI on top:** "make it brighter" creates V3 from V2.
5. **Projects grid:** the project shows with a thumbnail. Rename works, and delete with "and
   what it made" removes it and its files.
6. **Non-video result** (a deck): the player shows the deck card, the Audio, Text, Effects and
   Adjust tabs are disabled, and Download works.
7. **Loading:** opening a project from Home shows the loading screen at once.

## Order of work and estimate

| Step | What | Est. |
|---|---|---|
| 1 | Split `Editor.tsx` into `useEdit` + pieces (no visual change; existing editor test still passes) | 2 h |
| 2 | `Workspace` layout in `/studio/[id]` (Media panel, tabs, player, timeline, chat right, top bar, compact sidebar) | 3 h |
| 3 | `Home` (prompt, quick starts, projects grid) | 2 h |
| 4 | Tour (two tours, Help button) | 1.5 h |
| 5 | Mobile stacking, polish, browser tests above, deploy | 2 h |

About 10 hours. With the Nov 8 deadline, do steps 1–4 first. Mobile polish and the timeline
dragging (phase 2) only if there's time.

## Other open items (not UI layout)

These came out of the live tests and aren't fixed yet:

- **Title size:**
  - Problem: the opening titles in reels and episodes are small ("good times", "The gang",
    "KOFI & BOLT"). The AI ignores the prompt rule (size 0.12).
  - Fix: in code. When `video.text` is at the very start of a video, uses the bold_pop style and
    has no size given, use a hook size.
- **Episode loudness:**
  - Problem: Episode 1 came out at −19 LUFS (aim −14 for social video).
  - Fix: a loudnorm on the final mix in the animation edit rule, or in the editor's export.
- **Faster episodes:**
  - Problem: `animate` calls in one turn run one after another, so 6 scenes take ~4.5 min.
  - Fix: run them in parallel, with a lock around the seconds budget, for ~1.5 min.
- **Character voices in the editor:** add the Characters' voices (`speak_as`) to the editor's
  voice list, so a hand-made voiceover can be "Kofi".
- **Lip-sync quality:** H3 uses our line only to time the mouth (its own audio is off). This is
  fine for short lines; check longer lines.
- **Editor phase 2:** colour sliders, fades, reordering clips, drag-to-move on the timeline, and
  recording a voiceover inside the editor (today: upload a recording, or use My voice).

## Where things stand (2026-11-06)

All of this is live at https://209.250.229.110.sslip.io (the latest commit is c9b6547, and the
7 server render checks pass).

- **Production quality (docs/14):**
  - moving stills, beat-length shots, original music (ACE-Step);
  - word-timed captions, animated titles and sound effects;
  - the AI gathers everything in one turn;
  - captions kept clear of slide text;
  - text kept in frame on moving slides;
  - a finished edit is delivered when the turns run out.
- **Render reliability:**
  - `MALLOC_ARENA_MAX=2` and 2 MB thread stacks for ffmpeg (long captioned videos fit the
    4 GB step cap);
  - retries for image rate limits;
  - the `video.sfx` field names the AI uses are accepted;
  - a Debian snapshot fallback in the Dockerfile.
- **Chats:** rename; delete the chat only, or the chat and what it made (uploads are always
  kept); a loading screen when opening a chat.
- **Voices:**
  - 22 voices: 11 accents (American, British, Nigerian, Ghanaian, Kenyan, South African,
    Indian, Australian, Irish, Scottish, Jamaican), each as a woman's and a man's voice. They
    use qwen3-tts voice design with a fixed seed;
  - a voice picker under the chat;
  - **My voice:** recorded or uploaded in Settings with consent, cloned with qwen3-tts.
- **Settings:** Your voice, Your chats, Usage, Brand kit. The developer key card was removed.
- **Animation:**
  - a Characters page (create, redraw, edit, hear the voice, remove, Animate);
  - the AI tools `make_character` and `animate` (MiniMax H3; the cast's design pictures are
    the references; each character's line in their own voice, laid on the clip);
  - Episode 1 of "Kofi & Bolt" made and copied into the user's account, with its cast.
- **Hand editor (`/edit/[id]`):**
  - Tools: trim, split, delete; shape and framing; volume 0–300%; speed; text; voiceover (Make
    voiceover in any of the voices, or a recording); music (Make music, or the user's song);
    sound effects.
  - The live preview includes all the sound (Web Audio), and saving creates the chat's next
    version.
  - Server side: `POST /jobs/{id}/edit`, `/edit/voiceover`, `/edit/music`, and the hidden
    `video.layers` op.
  - Byte-range support for assets, so the playhead and the player can seek.

**Deploy note:**
- **Normal deploy:** pull on the server, then rebuild the images and recreate `api`, `worker`
  and `web` (`docker compose up -d --force-recreate api worker web`).
- **When snapshot.debian.org is down:**
  - The Dockerfile now retries and falls back to deb.debian.org.
  - For a code-only change, the quick path is an overlay build on top of the existing
    `atelier-ref` image (copy `src/` and `tests/`, `uv sync`), then rebuild `atelier-app`.
- **Long builds:** run them detached (`nohup … > /tmp/build.log &`) and poll the log.
- **After recreating containers:** copy any scratch scripts in again (`docker compose cp`),
  because the containers' `/tmp` is gone.
