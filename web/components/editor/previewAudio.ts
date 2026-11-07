/**
 * The editor's live sound: the video's own sound through a gain (so above 100% is heard too),
 * and the voiceover, music and sound effects scheduled with the Web Audio API against the
 * edited timeline, so the preview sounds like the saved video.
 *
 * Times: the editor works on the source clock; `clock` maps it to the edited clock (cut parts
 * removed, speed applied). While the video plays at `speed`, the edited clock runs in real
 * time, so layers play at their normal rate and stay in step across the cuts.
 */

export type Clock = (t: number) => number | null;
export type SoundKind = "whoosh" | "pop" | "riser" | "impact" | "ding" | "click" | "laser" | "boom" | "sparkle";
export interface Layer {
  handle: string;
  start: number; // edited clock
  volume: number;
  loop: boolean;
}
export interface Hit {
  at: number; // edited clock
  kind: SoundKind;
}

// where each effect peaks, seconds into it (the server's video.sfx places them the same way)
const PEAK: Record<SoundKind, number> = { whoosh: 0.35, pop: 0, riser: 1.2, impact: 0, ding: 0, click: 0, laser: 0, boom: 0, sparkle: 0 };

export class PreviewAudio {
  private ctx: AudioContext;
  private videoGain: GainNode;
  private playing: AudioScheduledSourceNode[] = [];
  private buffers = new Map<string, Promise<AudioBuffer | null>>();
  private effects = new Map<SoundKind, AudioBuffer>();

  constructor(video: HTMLVideoElement) {
    this.ctx = new AudioContext();
    this.videoGain = this.ctx.createGain();
    this.ctx.createMediaElementSource(video).connect(this.videoGain).connect(this.ctx.destination);
    video.volume = 1; // the gain does the volume from here on
  }

  setVideoVolume(v: number) {
    this.videoGain.gain.value = v;
  }

  /** Fetch and decode a clip once (voiceover, music). */
  load(url: string): Promise<AudioBuffer | null> {
    let p = this.buffers.get(url);
    if (!p) {
      p = fetch(url)
        .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(String(r.status)))))
        .then((data) => this.ctx.decodeAudioData(data))
        .catch(() => null);
      this.buffers.set(url, p);
    }
    return p;
  }

  stop() {
    for (const n of this.playing) {
      try {
        n.stop();
      } catch {
        // already stopped
      }
    }
    this.playing = [];
  }

  /** Start every layer and effect from edited time `now`. */
  async start(now: number, layers: { url: string; layer: Layer }[], hits: Hit[]) {
    this.stop();
    await this.ctx.resume();
    const loaded = await Promise.all(layers.map(async ({ url, layer }) => ({ layer, buf: await this.load(url) })));
    const t0 = this.ctx.currentTime;
    for (const { layer, buf } of loaded) {
      if (buf) this.schedule(buf, layer.start, layer.volume, layer.loop, now, t0);
    }
    for (const hit of hits) {
      const begin = hit.at - PEAK[hit.kind];
      if (hit.at >= now) this.schedule(this.effect(hit.kind), begin, 0.5, false, now, t0);
    }
  }

  private schedule(buf: AudioBuffer, at: number, volume: number, loop: boolean, now: number, t0: number) {
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.loop = loop;
    const gain = this.ctx.createGain();
    gain.gain.value = volume;
    src.connect(gain).connect(this.ctx.destination);
    if (at >= now) src.start(t0 + (at - now));
    else {
      const offset = now - at;
      if (loop) src.start(t0, offset % buf.duration);
      else if (offset < buf.duration) src.start(t0, offset);
      else return;
    }
    this.playing.push(src);
  }

  /** The four effects, synthesized like the server's (noise and tones shaped over time). */
  private effect(kind: SoundKind): AudioBuffer {
    const cached = this.effects.get(kind);
    if (cached) return cached;
    const sr = this.ctx.sampleRate;
    const seconds = { whoosh: 0.55, pop: 0.12, riser: 1.25, impact: 0.7, ding: 0.9, click: 0.05, laser: 0.3, boom: 1.0, sparkle: 0.5 }[kind];
    const buf = this.ctx.createBuffer(1, Math.ceil(seconds * sr), sr);
    const x = buf.getChannelData(0);
    let b0 = 0;
    let b1 = 0;
    let seed = 1000;
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) * 2 - 1;
    for (let i = 0; i < x.length; i++) {
      const t = i / sr;
      if (kind === "whoosh") {
        const white = rand();
        b0 = 0.97 * b0 + white * 0.15; // a softer, pinker noise
        b1 = 0.6 * b1 + (white - b0) * 0.4; // high-passed
        const env = t < 0.35 ? Math.pow(t / 0.35, 3) : Math.max(0, 1 - (t - 0.35) / 0.2);
        x[i] = b1 * env * 1.4;
      } else if (kind === "pop") {
        x[i] = 0.6 * Math.sin(2 * Math.PI * t * (1400 - 6000 * t)) * Math.exp(-38 * t);
      } else if (kind === "riser") {
        const env = t < 1.1 ? Math.pow(t / 1.1, 3) : Math.max(0, 1 - (t - 1.15) / 0.1);
        x[i] = 0.35 * Math.sin(2 * Math.PI * t * (180 + 520 * t)) * env;
      } else if (kind === "ding") {
        x[i] = 0.5 * (Math.sin(2 * Math.PI * 1320 * t) + 0.5 * Math.sin(2 * Math.PI * 1980 * t)) * Math.exp(-5 * t);
      } else if (kind === "click") {
        x[i] = 0.7 * Math.sin(2 * Math.PI * 2500 * t) * Math.exp(-150 * t);
      } else if (kind === "laser") {
        x[i] = 0.5 * Math.sin(2 * Math.PI * t * (1800 - 9000 * t)) * Math.exp(-6 * t);
      } else if (kind === "boom") {
        x[i] = 0.9 * Math.sin(2 * Math.PI * 40 * t * Math.exp(-3 * t)) * Math.exp(-3 * t);
      } else if (kind === "sparkle") {
        x[i] = 0.4 * (Math.sin(2 * Math.PI * 2400 * t) + Math.sin(2 * Math.PI * 3200 * t * (1 + t))) * Math.exp(-8 * t);
      } else {
        x[i] = 0.9 * Math.sin(2 * Math.PI * 55 * t) * Math.exp(-6 * t);
      }
    }
    this.effects.set(kind, buf);
    return buf;
  }
}
