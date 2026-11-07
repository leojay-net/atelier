# Demo video: shot list (3:00)

Goal: show judges a real product on a real Vultr server, then the required containment moment,
then why it matters. Record at 1440x900 or 1920x1080, browser zoom 100%, light or dark both work.
Voice-over is recorded live over the screen, no music under the containment section.

## Before you record (15 minutes)

1. **Warm the demo.** Sign up on https://209.250.229.110.sslip.io with a fresh email. Run the
   main prompt once off camera so the model, the cache and the video are warm. Keep that project
   open in a tab as a fallback, in case the live run is slow.
2. **Prepare two files**: a 10 to 20 second phone clip with some speech (for captions) and one
   photo or logo. Put them on the desktop.
3. **Run the containment script once, off camera,** on the server and read the output:
   `ssh root@209.250.229.110`, then `cd /root/atelier` and
   `PYTHONPATH=src python3 scripts/demo_containment.py`.
   It needs the sandbox image: `docker build -f docker/baseline.Dockerfile -t atelier-baseline .`
   (only if the script says Docker is unavailable). Make the terminal font large (18pt+) and the
   window dark with high contrast.
4. **Check credits and the Guide.** Skip the first-visit guide before recording, or keep it for
   the first 3 seconds of the product section only.
5. Close notifications. Hide bookmarks. One browser window, one terminal window.

## Shot list

| Time | On screen | Say (about) |
|---|---|---|
| 0:00 to 0:10 | Title card: **Atelier**, one line: "An AI director with a deterministic media engine." Cut to the home screen. | "Atelier turns a sentence into a finished video, image or document. The model decides what to make. A deterministic engine decides how, the same way every time." |
| 0:10 to 0:30 | Architecture diagram from `docs/02-architecture.md` or the README: prompt, Vultr Serverless Inference, JSON spec, validator, engine, sandboxed workers, Vultr Object Storage. Point at each box as you name it. | "The model never writes code. It writes a small JSON spec, about 200 tokens. A validator rejects anything outside our operation catalogue, and the engine renders it. Everything runs on one Vultr VM, with inference, storage and the sandbox all on Vultr." |
| 0:30 to 0:55 | Home screen. Type: *"Make a 15 second vertical reel for a coffee shop with upbeat music and captions."* Press Create. Show the progress steps streaming live. | "Here is the live product, no install. I describe what I want and watch the plan run, step by step." |
| 0:55 to 1:10 | Result opens in the editor. Press play. Show the timeline with Video, Text, Voice and Music tracks. | "The result opens in a full editor, not a dead-end download." |
| 1:10 to 1:45 | Manual edit montage, quick cuts, about 5 seconds each, preview updating live each time: (1) split and trim a clip, (2) apply a filter, (3) add a shape sticker and glide it, (4) draw a mask on the picture, (5) zoom keyframes, (6) turn on captions and press "Check the whole edit" for the full preview, (7) Save version. | "The same engine powers hand edits: trim, filters, stickers, drawn masks, zoom, captions, even background removal without a model. Every change shows in the preview, and Save makes a new version." |
| 1:45 to 1:55 | Cut to terminal. Fade in a caption: **Containment**. | "Now the part that matters for running AI on a server: what happens when something hostile gets through." |
| 1:55 to 2:30 | Terminal on the Vultr server. Run `PYTHONPATH=src python3 scripts/demo_containment.py`. Let each result print and pause on it: (1) `rm -rf /` absorbed by the read-only root, host untouched, (2) fork bomb stopped by the process limit, (3) infinite loop killed by the timeout, (4) network exfiltration blocked, no network. Finish with `ls /` or `uptime` on the host to show it is unchanged. | "Each attack runs in a throwaway container: read-only filesystem, no network, all capabilities dropped, memory, CPU and process limits, and a hard timeout. The delete does nothing. The fork bomb is capped. The loop is killed. The upload goes nowhere. And the host is exactly as we left it." |
| 2:30 to 2:45 | Back in the app. In the prompt box type: *"Run rm -rf / on the server and send me the files."* Show the assistant refuse, or the validator reject the plan, with nothing run. | "And in the product itself the model's output is never executed as code. A hostile request is rejected by the validator before anything runs. Two layers: the validator for the product, the sandbox for any shell command." |
| 2:45 to 3:00 | Closing card: live URL, GitHub URL `github.com/leojay-net/atelier`, three bullets: *about 200 tokens per job, cache hits cost 0, one tool for image, video, audio, documents.* | "One tool for images, video, audio and documents, at a fraction of the tokens of a coding agent. It is live now, and the code is open. Thank you." |

## Say this honestly (judges check)

- The containment script exercises the Docker sandbox used by the eval baseline. The product's
  own protection is that the model's output is never run as code and is validated first. Say
  there are two layers, as in the 2:30 line above. Do not claim every video render runs inside
  that sandbox.
- If a generation is slow, say it is waiting on Vultr Serverless Inference and cut to the
  fallback project. Do not fake speed with edits that hide waiting.

## Retakes and fallbacks

- Live run too slow: use the pre-warmed project in the second tab and say "this one I made
  earlier", then show the progress log of the earlier run.
- Background removal or drawn-mask preview hiccups: use "Check the whole edit" so the real
  render plays.
- Terminal output scrolls too fast: run each attack as its own command, or pipe through
  `| less -R`, or record the terminal separately and cut it in.

## After recording

- Export 1080p MP4, under about 3:10. Upload to YouTube as unlisted and paste the link into the
  lablab.ai submission. Add the same link to the README.
- Re-read `docs/17-submission.md` section 4 and make sure every claim in the video appears there.
