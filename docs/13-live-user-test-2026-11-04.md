# 13. Live user test — 2026-11-04

A black-box test of the live deployment (`https://209.250.229.110.sslip.io`, London `vx1-g-2c-8g`),
done the way a user would: upload real files, ask for edits, wait, download, inspect the result.
~35 jobs, 16 uploads, 4K and multi-clip edits, malformed inputs, concurrency and load.

Test media, harness and raw results (`results.jsonl`) were kept in the session scratchpad.

---

## Verdict

**The editing engine is solid. The product around it is not demo-ready yet.**

- Multi-clip editing works: 3 clips of different resolution, frame rate, orientation and codec,
  one with no audio, come out as one clean, in-sync file. Every bad input gets a clear error.
- **But a user cannot actually use it today**, for three reasons:
  1. **Every natural-language (AI) job fails** (`APIConnectionError … Connection refused`).
  2. **The public HTTPS site cannot upload or run jobs**: the UI's calls go to 404.
  3. A logo in any corner of a vertical video **always** fails its own safe-margin check.

---

## Blockers (fix before the demo)

### B1. AI prompt jobs fail on the server
- Error: `internal error: APIConnectionError caused by ConnectionRefusedError (Connection refused)`,
  in 3–7 s, every time (two attempts, 17:05 and 18:00).
- From inside the same worker container, a hand-run `openai` chat call to the same URL with the
  same key **succeeds**, and DNS resolves to the same IP as from outside (`155.138.237.57`).
  So it is not DNS, the key, the URL or a proxy env var. Something differs between the
  long-running worker process and an `exec`'d one.
- It fails faster (2.9 s) than 5 retries with back-off should take, which suggests the refused
  connection may not be the first request, or the deployed adapter isn't the one we think.
- **Next step (decisive):** with the worker stopped, run one job in the foreground:
  ```bash
  docker compose stop worker
  docker compose run --rm worker manage run_worker --once   # then submit a prompt job
  ```
  and check how many worker/API processes exist (`docker ps`, `docker compose ps`): a stray
  older worker container polling the same database would explain everything.

### B2. The HTTPS site's UI cannot upload or start jobs
- nginx sends **all** of `/api/` to Django; the Next.js UI's browser calls go to
  `/api/proxy/...`, which only Next serves. Result on `https://…sslip.io`:
  `POST /api/proxy/assets` → **404**. Pages render (server-side), but nothing works.
- It only works on `http://209.250.229.110:3000` (plain HTTP, port left open).
- **Fix:** in `deploy/nginx.conf.template`, route `/api/proxy/` to `web:3000` *before* the `/api/`
  block (or narrow the Django block to `/api/v1/`), and turn off buffering for
  `/api/proxy/stream/`. Then close port 3000 (and 8000) in the firewall.

### B3. The whole API is public with no login
- `http://209.250.229.110:3000/api/proxy/*` adds the org's API key server-side, so **anyone**
  can list/download every asset, upload files, run jobs (spending Vultr inference) and delete
  recipes. `GET /api/proxy/me` returns the org and key prefix to an anonymous caller.
- For a judged demo this is acceptable only with a cap: set a job/units limit, a lower upload
  size, and rate limiting on the proxy. Note it in the README as a deliberate demo choice.

### B4. A logo in a corner of a vertical video always fails checks
- `video.overlay` with its **default** margin (0.04) → `safe_margins:fail (enters the top safe
  margin by 225px)`. Even `margin=0.1` bottom-right → `fail by 276px`.
- So "make it a Reel with my logo in the corner", the core demo ask, ends `checks_failed`.
- **Fix:** when the export is a vertical social preset, place overlays inside the platform's safe
  area by default (or make the margin "safe" by default, as the spec docs already show:
  `"margin": "safe"`).

---

## What works well

| Scenario | Result | Compute | Notes |
|---|---|---|---|
| Join 3 clips: 1080p30 + 720p25 + vertical 24fps with no audio, 0.5 s fades | ✅ | cached | 1920x1080 30fps, audio throughout, all 6 checks pass |
| Join 3 clips with 1 s dissolves → YouTube 1080p | ✅ | 31.7 s | 16 s output; ~2x real time |
| 2 clips → 9:16 blur-pad → burned captions (SRT) → logo → music → Reels | ⚠️ checks_failed | 42.0 s | Looks right (captions, blur, logo); only B4 fails |
| Picture-in-picture: vertical clip over landscape | ✅ | 8.8 s | |
| Wipe from vertical no-audio clip to landscape clip | ✅ | 14.2 s | Output takes the first clip's shape (vertical, 24 fps) |
| Phone video with rotation metadata, trim | ✅ | 4.1 s | Comes out upright |
| WebM (VP9/Opus) → MP4 | ✅ | 1.9 s | |
| Join WebM + 60fps MOV + rotated MP4 | ✅ | 7.7 s | |
| Join 24 clips (3 min 12 s of 1080p) | ✅ | 160 s | ~1.2x real time, 160 MB output |
| 4K 20 s → 9:16 Reel with logo | ⚠️ checks_failed | 19.4 s | B4 again |
| 4K 20 s fades → YouTube 4K | ✅ | 106 s | ~5x real time |
| Frame grab → YouTube thumbnail | ✅ | 1.2 s | |
| Text with `'; rm -rf / # \ %{pts} [a]` | ✅ | 8.4 s | Rendered literally; no filter injection |
| Identical job again | ✅ | 0 s | Cache hit, 5.5 s total (mostly the download) |

**Validation is excellent.** Every one of these got a precise, actionable error before any work ran:
trim end before start, trim past the end (`use end <= 8.00`), a text file as a video, speed ×100,
an unknown argument (with the valid ones listed), a cycle between steps, a raw path
(`/etc/passwd`), 25 clips (limit 24), fades longer than the clip, a truncated video.

---

## Timing: how long until an edit is online?

For a typical 6 MB, 8 s 1080p clip, from this connection (~50–320 KB/s upload):

| Stage | Time | Notes |
|---|---|---|
| Upload | 1–28 s per clip | Bound by the user's upload speed, not the server. 76 MB 4K took 238 s |
| AI planning | **unmeasured** | B1 |
| Render | 2–40 s | Simple edits 2–9 s; multi-clip with transitions 15–40 s; 4K ~5x real time |
| Download | 1–3 s | 160 MB in 29 s |

**Realistic total for "join my 3 clips and make a Reel": about 1–2 minutes**, most of it upload
and render, plus whatever the model takes (likely 5–20 s once B1 is fixed).

### Limits observed
- **One worker = one job at a time.** 3 jobs submitted together finished at 41 s, 80 s and 83 s.
  With several judges at once, the 3rd waits over a minute. Add a second worker replica (the
  VM has 2 vCPU; jobs are CPU-bound, so expect ~1.5x throughput, not 2x).
- 4K is slow (~5x real time). Fine for the 20 s demo clips; steer the demo to 1080p.
- Upload limit: nginx allows 100 MB, Django 200 MB; the UI path buffers the whole file in Next.
- Speed: `0.25` is rejected with "should be greater than 0.25". Quarter-speed slow-mo is a
  common ask and the message reads as wrong. Make the bound inclusive.
- The site stayed responsive under a 4K render (59/60 requests OK, ~0.6 s). About 1–2% of
  connections from here failed to connect at all; that looks like the network path, not load.

---

## Smaller findings

| # | Finding | Fix |
|---|---|---|
| S1 | Empty file accepted as a "text" asset | Reject 0-byte uploads (400) |
| S2 | Truncated/unreadable video accepted as `video`; the job fails later | Probe on upload; reject with a clear message (the probe already knows: "unreadable") |
| S3 | `evil.mp4.php` stored as `.txt` | ✅ Good, content-sniffed. No action |
| S4 | Random bytes named `.mp4` rejected 415 | ✅ Good |
| S5 | Chinese text and emoji render as empty boxes (tofu) in `video.text` / `lower_third` | Bundle a CJK + emoji fallback font (Noto Sans CJK, Noto Emoji), or reject unsupported glyphs at validation |
| S6 | Very long lower-third title is tiny but legible; long name fits | OK |
| S7 | Landscape clip in a vertical sequence is letterboxed small (expected for `fit`) | The AI should choose `fill`/`blur_pad` for mixed orientations; check once B1 is fixed |
| S8 | Uploading the same bytes under another name returns the original filename | Expected (content-addressed), but the UI shows the old name. Minor |

---

## Sandbox claim vs. production (important for the pitch)

The hackathon rule is that code runs in an isolated sandbox, and the pitch says every job runs
in a throwaway container. **In production that is not what happens:** each step runs as a
resource-limited subprocess (CPU, memory and file-size rlimits) inside the long-running
`worker` container, which has network access (it needs it for inference). The
`--network none --read-only --cap-drop ALL` Docker sandbox exists only in the evaluation tools
and `scripts/demo_containment.py`.

That is defensible (the model only writes validated JSON specs, never code; text is rendered
literally, as tested above), but the pitch and video should say exactly that, or the live path
should run steps in the Docker sandbox. Judges may check.

---

## Still to test once B1 and B2 are fixed
1. The same scenarios in plain English: "join these three and make a TikTok", "cut the boring
   bit", "make it better" (vague), "remove the person" (unsupported), prompt injection in the
   prompt and in an uploaded SRT.
2. Model latency and token use per job, and the failure mode when the model asks a question
   (`needs_input`).
3. The full flow in a browser on the HTTPS site: drag-drop, live progress, download.
