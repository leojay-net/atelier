import type { TourStep } from "./Tour";

export const HOME_TOUR: TourStep[] = [
  {
    target: "prompt",
    title: "Tell me what to make",
    body: "A reel, a birthday video, a tutorial, a story… write it here in your own words.",
  },
  {
    target: "attach",
    title: "Add your own files",
    body: "Add your photos, videos or a song, or paste a music link. You can also drag files in.",
  },
  {
    target: "voice",
    title: "Pick a voice",
    body: "Choose a voice for voiceovers: 11 accents, a woman or a man, or your own voice (in Settings).",
  },
  {
    target: "quick",
    title: "Not sure where to start?",
    body: "Pick one of these and finish the sentence.",
  },
  {
    target: "projects",
    title: "Everything you make is saved here",
    body: "Open a project to keep editing it. Use ⋯ on a card to rename or delete it.",
  },
  {
    target: "nav-characters",
    title: "Characters",
    body: "Create a character once, then animate them in any episode.",
  },
];

export const STUDIO_TOUR: TourStep[] = [
  {
    target: "history",
    title: "Your projects",
    body: "Every project is listed here. Switch between them, or start a new one.",
  },
  {
    target: "player",
    title: "The player",
    body: "Your latest version plays here. Edits you make by hand show up live.",
  },
  {
    target: "tool-tabs",
    title: "Media, Audio, Text and Effects",
    body: "Media holds every version and file. Audio, Text and Effects add a voiceover, music, titles and sound effects by hand. You hear them in the preview.",
  },
  {
    target: "timeline",
    title: "The timeline",
    body: "Click or drag to move the playhead, then Split and Delete to cut, like in any video editor.",
  },
  {
    target: "assistant",
    title: "The AI assistant",
    body: "Ask for anything, or ask for changes: “make it shorter”, “add music”. Properties has shape, volume and speed.",
  },
  {
    target: "save",
    title: "Save version",
    body: "Saves your hand edits as a new version. The original is always kept.",
  },
];
