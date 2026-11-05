import type { JobEvent } from "./types";

/**
 * Turns a job's live events into plain-English activity lines for the chat ("Looking at your
 * photos…", "Recording the voiceover…"), each with the technical name kept as a small detail for
 * the curious. Pure, so the same function renders a live job and a finished one.
 */

export interface ActivityLine {
  key: string;
  text: string;
  detail?: string;
  state: "doing" | "done" | "failed";
}

const OPS: Record<string, string> = {
  "video.slideshow": "Turning your photos into a video",
  "video.soundtrack": "Adding the sound",
  "video.concat": "Joining your clips",
  "video.transition": "Adding a transition",
  "video.trim": "Cutting the clip",
  "video.reframe": "Changing the shape of the video",
  "video.auto_reframe": "Reframing around the main subject",
  "video.crop": "Cropping the video",
  "video.text": "Adding text",
  "video.captions": "Adding captions",
  "video.lower_third": "Adding a name title",
  "video.overlay": "Adding your logo",
  "video.pip": "Adding a picture-in-picture",
  "video.fade": "Adding fades",
  "video.speed": "Changing the speed",
  "video.color": "Adjusting the colours",
  "video.mute": "Removing the sound",
  "video.frame": "Grabbing a still picture",
  "video.blur_faces": "Blurring faces",
  "video.stabilize": "Steadying shaky footage",
  "video.upscale": "Sharpening the video",
  "audio.mix": "Mixing the voice and the music",
  "audio.extract": "Taking the sound out of the video",
  "audio.loudnorm": "Evening out the volume",
  "audio.trim": "Cutting the sound",
  "audio.fade": "Fading the sound",
  "audio.denoise": "Cleaning up background noise",
  "audio.denoise_speech": "Cleaning up the voice",
  "audio.silence": "Removing silences",
  transcribe: "Listening to the speech for captions",
  export: "Finishing and saving your file",
};

const TOOLS: Record<string, string> = {
  inspect_media: "Looking at your files",
  preview: "Taking a closer look at your pictures",
  describe_op: "Checking my editing tools",
  make_voiceover: "Recording the voiceover",
  find_images: "Searching the web for pictures",
  make_image: "Creating a picture",
  make_character: "Designing a character",
  animate: "Animating a scene (about 20 seconds each)",
  make_music: "Composing original music",
  run_pipeline: "Editing",
};

export function friendlyOp(op: string): string {
  if (OPS[op]) return OPS[op];
  if (op.startsWith("image.")) return "Editing the picture";
  if (op.startsWith("audio.")) return "Working on the sound";
  if (op.startsWith("pdf.") || op.startsWith("doc")) return "Working on the document";
  return "Working on it";
}

/** One run can fuse several ops into a single render: name what it does, not just its last op. */
export function friendlyGroup(ops: string[]): string {
  const labels = [...new Set(ops.filter((o) => o !== "export").map(friendlyOp))];
  if (!labels.length) return friendlyOp("export");
  const [first, ...rest] = labels;
  return [first, ...rest.map((l) => l.charAt(0).toLowerCase() + l.slice(1))].join(", ");
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

export function activityLines(events: JobEvent[], finished: boolean): ActivityLine[] {
  const lines: ActivityLine[] = [];
  const steps = new Map<string, number>(); // step id -> index in lines
  let thinking = -1;
  const patch = (i: number, change: Partial<ActivityLine>) => {
    const line = lines[i];
    if (line) lines[i] = { ...line, ...change };
  };

  for (const e of events) {
    const d = e.data ?? {};
    switch (e.kind) {
      case "started":
        lines.push({ key: `s${e.seq}`, text: "Got it! Getting started", state: "done" });
        break;
      case "link": {
        const url = str(d.url);
        const state = str(d.state);
        const existing = lines.findIndex((l) => l.key === `link:${url}`);
        const line: ActivityLine =
          state === "downloading"
            ? { key: `link:${url}`, text: "Downloading the file from your link", detail: url, state: "doing" }
            : state === "done"
              ? { key: `link:${url}`, text: `Got the file from your link${d.name ? `: ${str(d.name)}` : ""}`, detail: url, state: "done" }
              : { key: `link:${url}`, text: `Couldn't use your link: ${str(d.reason)}`, detail: url, state: "failed" };
        if (existing >= 0) lines[existing] = line;
        else lines.push(line);
        break;
      }
      case "turn": {
        // one "thinking" line at a time: reuse it while the AI keeps thinking between tools
        const lastLine = lines[lines.length - 1];
        if (thinking >= 0 && lastLine && lines.length - 1 === thinking) {
          patch(thinking, { state: "doing" });
          break;
        }
        lines.push({ key: `t${e.seq}`, text: thinking < 0 ? "Thinking about how to do this" : "Thinking", state: "doing" });
        thinking = lines.length - 1;
        break;
      }
      case "tools": {
        if (thinking >= 0) patch(thinking, { state: "done" });
        const names = Array.isArray(d.names) ? (d.names as string[]) : [];
        const details = Array.isArray(d.details) ? (d.details as string[]) : [];
        names.forEach((name, i) => {
          if (name === "run_pipeline") return; // the steps below say what editing happens
          lines.push({
            key: `tool${e.seq}-${i}`,
            text: TOOLS[name] ?? "Working",
            detail: details[i] ? `${name} · ${details[i]}` : name,
            state: "done",
          });
        });
        break;
      }
      case "tool_error":
        lines.push({ key: `e${e.seq}`, text: "Fixing a problem and trying again", detail: `${str(d.name)} · ${str(d.text).slice(0, 160)}`, state: "done" });
        break;
      case "step_start": {
        const op = str(d.op);
        const ops = Array.isArray(d.ops) ? (d.ops as string[]) : [op];
        steps.set(str(d.step), lines.length);
        lines.push({ key: `st${e.seq}`, text: friendlyGroup(ops), detail: ops.join(" → "), state: "doing" });
        break;
      }
      case "step_done": {
        const at = steps.get(str(d.step));
        if (at !== undefined) {
          patch(at, { state: "done", detail: `${lines[at]?.detail ?? ""}${d.cached ? " · reused" : ""}` });
        } else {
          const op = str(d.op);
          lines.push({ key: `sd${e.seq}`, text: friendlyOp(op), detail: op, state: "done" });
        }
        break;
      }
      default:
        break;
    }
  }
  if (finished) {
    return lines
      .filter((l, i) => !(l.text === "Thinking" && i === lines.length - 1))
      .map((l) => (l.state === "doing" ? { ...l, state: "done" as const } : l));
  }
  return lines;
}
