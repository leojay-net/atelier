# 14. From "AI slideshow" to production-quality video

**Status:** proposal, 2026-11-05. Deadline Nov 8, so it is sized for about three days.

## Why our videos still feel robotic

Judged against what people pay for (agency explainers, Submagic/Opus/CapCut-style shorts),
looking only at our own live outputs (frames pulled from the Python tutorial, Tolu's birthday
video and the people reel):

| What pros do | What we do today |
|---|---|
| **Something moves in every shot**: slow push-ins and pans on photos (Ken Burns), parallax, punch-in zooms | Still pictures held **3–7 s with zero motion**. This is the single biggest "slideshow" tell |
| **Fast, rhythmic pacing**: a visual change about every 1.5–3 s in shorts, **cuts on the music's beat** | Even, slow slides; only crossfades; cuts unrelated to the music |
| **Word-by-word captions** synced to the voice, current word highlighted (sound-off viewing is the norm) | No captions under a voiceover; static text plates |
| **Kinetic typography**: titles pop, slide and scale in with easing; a **hook in the first 2 seconds** | Static text dropped onto a picture; no hook |
| **Music always**, original or licensed, mixed under the voice | Silent unless the user brings a song |
| **Sound design**: whooshes on transitions, pops on titles, risers into the hook | None |
| **Voice with energy and rhythm**: short spoken sentences, emphasis, pauses | A flat, even read; written-English scripts |
| **One consistent look**: a color grade over every shot, one image style | Found photos and AI pictures in mixed styles and colors |
| **Structure**: hook → value → payoff → call to action / end card | Title slide → points → "now try it" |

Sources (mostly vendor and practitioner guides, so treat the numbers as rules of thumb):
pacing of 1.5–2 s per visual in sub-60 s videos and captions for muted viewing
([Splice](https://spliceapp.com/blog/mastering-smooth-edits-for-short-form-videos-essential-tips/),
[aibrify](https://aibrify.com/en/blog/short-form-video-editing-captions-b-roll-guide),
[PW Skills](https://pwskills.com/blog/school-of-design/social-media-video-editing-in-2026));
word-by-word highlighted captions, auto-zoom, B-roll and SFX as the standard auto-edit pass
([Submagic vs Opus](https://www.submagic.co/alternatives/opus-clip),
[toolsforhumans](https://www.toolsforhumans.ai/ai-tools/submagic),
[AI editing landscape](https://emre-garden.vercel.app/garden/ai-video-editing-tools));
kinetic typography and parallax to bring stills to life
([Hera](https://hera.video/blog/kinetic-typography-video-generator-guide),
[Fueler](https://www.fueler.io/blog/motion-graphics-skills-every-video-editor-should-know));
natural, expressive TTS as the bar for voice
([Fish Audio comparison](https://fish.audio/blog/best-text-to-speech-tools-2026-comparison/)).
One useful counterpoint: over-polish can read as fake, so restraint matters.

## What Vultr gives us (tested on our key today)

| Need | Vultr model | Tested |
|---|---|---|
| Original music per video | **ACE-Step 1.5** at `POST /v1/music` (async job). Instrumental, `bpm`, `duration` 10–600 s, `seed` | 15 s track in **2.8 s**, **$0.003**, 48 kHz stereo |
| More expressive voice | **Chatterbox**: `exaggeration` 0.25–2, `speed`, voice cloning | Accepts the parameters; needs a listening test |
| Natural voice (current) | Qwen3-TTS, 9 voices | In use |
| Pictures | qwen-image-2.1 (3 s), plus photo search | In use |
| Word timings for captions | Whisper returns text only (no `verbose_json`) | We don't need it: generating speech one sentence at a time gives exact sentence timings, and words are spread within each sentence |
| AI *video* clips (image-to-video) | **None listed on Vultr** | Out of scope: using an outside provider would break the hackathon's "all inference on Vultr" rule |

## The plan, in order of impact

**Day 1: make it move, and make it rhythmic**
1. **Motion on every still.** The slideshow gets slow push-in, push-out and pans, alternating
   per shot, eased and on by default; titles get a gentle zoom. Implemented in the engine with
   ffmpeg `zoompan` at high internal resolution, so the motion is smooth and deterministic.
2. **Original music and beat-synced cuts.** New AI tool `make_music(mood, bpm, seconds)` using
   ACE-Step. Because the tempo is known, shot lengths snap to whole beats (e.g. 4 beats at
   100 bpm = 2.4 s), so cuts land on the beat. Music is always present unless the user says
   silent, and ducks under the voice (that part exists).
3. **Word-by-word captions on every voiceover.** The voice is generated sentence by sentence,
   which gives exact timings, and caption cues are built word by word: bold, centered, in the
   safe area, current word highlighted.

**Day 2: make it feel produced**
4. **Animated titles (kinetic type).** Presets: pop-in (scale with overshoot), slide-up, and
   typewriter for code. A **hook title in the first 2 seconds**, and an **end card** (title +
   call to action).
5. **Sound design.** A small bundled CC0 sound pack (whoosh, pop, riser, soft impact), placed
   automatically on cuts and title reveals at low level. A final master to -14 LUFS for social.
6. **A better voice.** Switch to Chatterbox with expressiveness ~0.6–0.8, or keep Qwen,
   depending on a blind listening test. Script rules for speech: short sentences,
   contractions, one idea per line, energy up front.

**Day 3: make it consistent and self-checked**
7. **One look.** A single color grade and LUT (`assets/luts` exists) over every shot, a subtle
   vignette, and one image style per video (generated pictures share a style prompt and seed).
8. **The director's playbook in the AI's rules.** Plan a shot list before making anything:
   hook → value → payoff → CTA; 8–15 shots for 30 s; vary shot types (wide, close, detail,
   text).
9. **Self-review before delivery.** Automated "production checks" (no shot over 3 s in a reel,
   captions present when there's a voice, music present, loudness on target, hook text in the
   first 2 s), plus a contact-sheet look at its own cut, fixing what fails before the user
   sees it.

**After the hackathon:** AI video clips (image-to-video, if Vultr adds a model);
motion-graphics templates (e.g. Remotion); brand kits applied automatically; voice cloning
(with consent).

## How we'll know it worked

The same three requests as the live tests (Python tutorial, Tolu's birthday, people reel),
rendered before and after, compared side by side, and judged as: *would someone pay for
this?* Plus the automated production checks above, on every job.
