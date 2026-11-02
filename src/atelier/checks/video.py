"""Video checks (docs/06 §6.1). They judge the probed, decode-verified output's metadata.

Ops and `export` measure and verify; these functions only judge. Each returns None when the
export preset does not say anything about it, so the same checks serve every preset.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from atelier.checks import CheckResult, Status, check
from atelier.media import MediaType

VIDEO = MediaType.VIDEO
AV = (MediaType.VIDEO, MediaType.AUDIO)  # checks that also judge audio-only outputs
SYNC_TOLERANCE = 0.15  # seconds
FPS_TOLERANCE = 0.01
TOLERANCE = 1e-4


def _export(meta: Mapping[str, Any]) -> Mapping[str, Any] | None:
    export = meta.get("export")
    return export if isinstance(export, Mapping) else None


def _ok(name: str) -> CheckResult:
    return CheckResult(name, Status.PASS)


@check("playable", AV, "the output decodes cleanly end to end", default=True)
def playable(meta: Mapping[str, Any]) -> CheckResult:
    if meta.get("verified") and (meta.get("video") or meta.get("audio")):
        return _ok("playable")
    return CheckResult(
        "playable",
        Status.FAIL,
        "the encoded file did not verify as playable",
        "try a different preset, or trim/reframe the source first",
    )


@check("av_sync", VIDEO, "audio and video are the same length", default=True)
def av_sync(meta: Mapping[str, Any]) -> CheckResult | None:
    video, audio = meta.get("video"), meta.get("audio")
    if not video or not audio:
        return None
    vd, ad = video.get("duration"), audio.get("duration")
    if not vd or not ad:
        return None
    if abs(vd - ad) <= SYNC_TOLERANCE:
        return _ok("av_sync")
    return CheckResult(
        "av_sync",
        Status.FAIL,
        f"video is {vd:.2f}s but audio is {ad:.2f}s",
        "the audio and picture were cut differently; use video.trim rather than separate edits",
    )


@check("audio_present", VIDEO, "the output has sound when the preset expects it", default=True)
def audio_present(meta: Mapping[str, Any]) -> CheckResult | None:
    export = _export(meta)
    if not export or not export.get("audio"):
        return None
    if meta.get("audio"):
        return _ok("audio_present")
    return CheckResult(
        "audio_present",
        Status.WARN,
        "the output has no audio track",
        "the source has no audio; that is fine for silent clips, otherwise use a source with sound",
    )


@check("duration_max", AV, "duration within the preset's limit", default=True)
def duration_max(meta: Mapping[str, Any]) -> CheckResult | None:
    export = _export(meta)
    if export is None:
        return None
    limit, duration = export.get("max_duration"), meta.get("duration")
    if not limit or duration is None:
        return None
    if duration <= limit + TOLERANCE:
        return _ok("duration_max")
    return CheckResult(
        "duration_max",
        Status.FAIL,
        f"{duration:.1f}s exceeds the {limit:g}s limit of {export['preset']}",
        f"trim to at most {limit:g}s with video.trim, or speed it up with video.speed",
    )


@check("resolution", VIDEO, "output size matches the preset", default=True)
def resolution(meta: Mapping[str, Any]) -> CheckResult | None:
    export = _export(meta)
    size = export.get("size") if export else None
    video = meta.get("video")
    if not size or not video:
        return None
    w, h = video["display"]
    tw, th = size["width"], size["height"]
    mode = size["mode"]
    if mode in ("fill", "exact"):
        ok, want = (w, h) == (tw, th), f"exactly {tw}x{th}"
    elif mode == "fit":
        ok, want = w == tw and h == th, f"exactly {tw}x{th}"  # padded to the full frame
    else:
        ok, want = w <= tw and h <= th, f"at most {tw}x{th}"
    if ok:
        return _ok("resolution")
    return CheckResult("resolution", Status.FAIL, f"{w}x{h}, preset wants {want}")


@check("frame_rate", VIDEO, "frame rate matches the preset", default=True)
def frame_rate(meta: Mapping[str, Any]) -> CheckResult | None:
    export = _export(meta)
    video = meta.get("video")
    if not export or not video or not video.get("fps"):
        return None
    fps = video["fps"]
    if export.get("fps"):
        want, ok = f"{export['fps']:g}", abs(fps - export["fps"]) <= FPS_TOLERANCE
    elif export.get("max_fps"):
        want, ok = f"at most {export['max_fps']:g}", fps <= export["max_fps"] + FPS_TOLERANCE
    else:
        return None
    if ok:
        return _ok("frame_rate")
    return CheckResult("frame_rate", Status.FAIL, f"{fps:g}fps, preset wants {want}")


LUFS_TOLERANCE = 1.0  # LU: normalization is exact, the AAC encode moves it by a fraction
PEAK_TOLERANCE = 0.5  # dB above the ceiling that the codec may add


@check("loudness_target", AV, "integrated loudness and true peak match the preset", default=True)
def loudness_target(meta: Mapping[str, Any]) -> CheckResult | None:
    export = _export(meta)
    target = export.get("loudness") if export else None
    measured = meta.get("loudness")
    if not target or not measured:
        return None  # no target in the preset, or silent audio (nothing to normalize)
    lufs, peak = measured["integrated"], measured["true_peak"]
    if abs(lufs - target["target_lufs"]) > LUFS_TOLERANCE:
        return CheckResult(
            "loudness_target",
            Status.FAIL,
            f"{lufs:.1f} LUFS, preset wants {target['target_lufs']:g}",
            "the audio could not be brought to the target; check the source is not almost silent",
        )
    if peak > target["true_peak_db"] + PEAK_TOLERANCE:
        return CheckResult(
            "loudness_target",
            Status.FAIL,
            f"true peak {peak:.1f} dBTP, ceiling is {target['true_peak_db']:g}",
            "lower the audio level before export",
        )
    return _ok("loudness_target")


BLACK_EDGE = 0.5  # seconds of black at the start or end that looks like a mistake
BLACK_MIDDLE = 1.0
FREEZE_LONG = 2.0
DEAD_AIR = 3.0


def _intervals(meta: Mapping[str, Any], kind: str) -> list[tuple[float, float]] | None:
    found = (meta.get("analysis") or {}).get(kind)
    return None if found is None else [(float(a), float(b)) for a, b in found]


@check("black_frames", VIDEO, "no stretch of black at the start, end or middle", default=True)
def black_frames(meta: Mapping[str, Any]) -> CheckResult | None:
    black, total = _intervals(meta, "black"), meta.get("duration")
    if black is None or not total:
        return None
    for a, b in black:
        if a <= 0.05 and b - a >= BLACK_EDGE:
            what, hint = f"opens with {b - a:.1f}s of black", "trim the start (video.trim start)"
        elif b >= total - 0.05 and b - a >= BLACK_EDGE:
            what, hint = f"ends with {b - a:.1f}s of black", "trim the end (video.trim end)"
        elif b - a >= BLACK_MIDDLE:
            what = f"has {b - a:.1f}s of black at {a:.1f}s"
            hint = "cut that part out, or check the clips it came from"
        else:
            continue
        return CheckResult("black_frames", Status.WARN, what, hint)
    return _ok("black_frames")


@check("frozen_frames", VIDEO, "the picture keeps changing (no long freeze)")
def frozen_frames(meta: Mapping[str, Any]) -> CheckResult | None:
    frozen = _intervals(meta, "frozen")
    if frozen is None:
        return None
    longest = max(((b - a, a) for a, b in frozen), default=(0.0, 0.0))
    if longest[0] >= FREEZE_LONG:
        return CheckResult(
            "frozen_frames",
            Status.WARN,
            f"the picture stands still for {longest[0]:.1f}s from {longest[1]:.1f}s",
            "trim the frozen part, or use video.speed / a different clip if it is unintended",
        )
    return _ok("frozen_frames")


@check("dead_air", AV, "no long stretch of silence in the sound")
def dead_air(meta: Mapping[str, Any]) -> CheckResult | None:
    silent = _intervals(meta, "silent")
    if silent is None:
        return None
    longest = max(((b - a, a) for a, b in silent), default=(0.0, 0.0))
    if longest[0] >= DEAD_AIR:
        return CheckResult(
            "dead_air",
            Status.WARN,
            f"{longest[0]:.1f}s of silence from {longest[1]:.1f}s",
            "cut the pause with audio.silence (where=all) or trim it out",
        )
    return _ok("dead_air")
