# ADR-0014: Audio ops and two-pass loudness normalization

- **Status:** Accepted
- **Date:** 2026-09-26 (decided under delegated authority)
- **Builds on:** [ADR-0013](0013-video-engine-graph-ops-and-fusion.md)

## Context
The social and podcast presets in docs/03 §3.5 specify a loudness target (Reels: -14 LUFS, -1 dBTP). A
loudness target is only meaningful if it is measured on the signal that is actually encoded, after every
earlier edit (trim, fade, mix), and if the result is verified.

## Decisions
1. **Loudness is normalized in two passes, on the final audio of the fused graph.** The export builder cuts
   the sub-graph that feeds the audio (only those nodes, because ffmpeg rejects a graph with unconnected
   outputs), runs it through `loudnorm` in measuring mode to nowhere, and then applies the measured values
   in the real encode with `linear=true`, so dynamics are not touched. Silent audio (a `-inf` measurement)
   is left as it is. Where the required gain would breach the true-peak ceiling, `loudnorm` itself falls back
   to dynamic mode.
2. **The result is measured again on the finished file** and recorded (`meta.loudness`), and the
   `loudness_target` check judges that, not the intention: within 1.0 LU of the target and no more than
   0.5 dB above the ceiling. The tolerance exists because the encoder moves loudness by a fraction of a LU.
   Independent measurement in the tests lands within 0.6 LU for quiet, loud and noisy sources.
3. **Audio ops are graph builders like the video ops**: `audio.extract` (video → audio), `audio.trim`,
   `audio.fade`, `audio.loudnorm`, and `audio.mix` (the music is ducked while the voice is
   present, see 7; the result lasts as long as the voice). They fuse: `video.trim → audio.extract → export` is one
   decode and one encode.
4. **Audio export presets** (`podcast_mp3`, `podcast_m4a`, `voice_opus`, `wav_master`) are versioned and
   write no metadata (`-map_metadata -1`, no ID3, bit-exact flags). M4A uses the `ipod` muxer so the file
   carries the M4A brand and is recognised as audio, not video, if it is imported again.
5. **The video checks that make sense for audio also apply to audio outputs** (`playable`, `duration_max`,
   `loudness_target`). A check may now name several media types.
6. **The graph compiles only what the output depends on.** Dropping a stream (`audio.extract` after video
   steps) leaves earlier filters for it unused; they are left out rather than left dangling.
7. **Ducking is computed, not filtered.** The first version used `sidechaincompress`. Repeating the same graph
   on the same inputs gave up to five different outputs in twelve runs, on ffmpeg 7.1.5 and 8.1.2 alike, and
   `-threads 1`, `+bitexact` and `-thread_queue_size` did not help: a filter that consumes two live inputs sees
   chunk sizes that depend on thread timing. That breaks the determinism the cache and the checks rely on, so
   `audio.mix` now decodes the voice once (a single-input decode is deterministic), computes the gain curve in
   numpy (`av/ducking.py`: a gate on 10 ms RMS, eased in ahead of the voice and out slowly), and hands it to
   ffmpeg as a 1 kHz control track that `amultiply` applies to the music. A product of samples is stateless, so
   chunking cannot change it. Ten fresh runs give one output. The control track is upmixed to stereo with `pan`,
   not `aformat`: `aformat` lowers a mono track by 3 dB when it upmixes, which made the ducked music quieter
   than the plain mix. Version 2.0.0 of the op; the parameters are now `duck` and `duck_db`.

## Consequences
- Normalizing costs two extra audio decodes (measure, then verify), which are fast next to the encode.
- The ducking model is a gate, not a look-ahead compressor: no ratio, knee or release shape parameters.
- `audio.mix` does not loop music that is shorter than the voice, or offset either input in time.
- Speech-specific processing (denoise, silence removal) and the transcription plugin are not built.
