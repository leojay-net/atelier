"""ffprobe → compact metadata, and `inspect` for video and audio."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from atelier.av.probe import DEMUXERS, summarize
from atelier.engine.engine import Engine
from atelier.media import MediaType
from conftest import FIXTURES

AV = FIXTURES / "av"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")


def raw(**kw: Any) -> dict[str, Any]:
    """A minimal ffprobe result: one h264 video and one aac audio stream."""
    return {
        "format": {
            "format_name": "mov,mp4", "duration": "4.000000", "size": "1000", "bit_rate": "2000",
        },
        "streams": [
            {
                "codec_type": "video", "codec_name": "h264", "width": 640, "height": 360,
                "avg_frame_rate": "30/1", "r_frame_rate": "30/1", "pix_fmt": "yuv420p",
                "nb_frames": "120", "profile": "High",
            },
            {
                "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2,
                "channel_layout": "stereo", "duration": "4.0",
            },
        ],
        **kw,
    }  # fmt: skip


# -- summarize: pure ---------------------------------------------------------------------------


def test_summarize_the_common_case() -> None:
    meta = summarize(raw())
    assert meta["duration"] == 4.0 and meta["container"] == "mov,mp4"
    assert meta["streams"] == {"video": 1, "audio": 1, "subtitle": 0, "other": 0}
    v, a = meta["video"], meta["audio"]
    assert (v["width"], v["height"], v["fps"], v["vfr"], v["nb_frames"]) == (
        640,
        360,
        30.0,
        False,
        120,
    )
    assert v["display"] == [640, 360] and v["rotation"] == 0
    assert (a["codec"], a["sample_rate"], a["channels"]) == ("aac", 48000, 2)


@pytest.mark.parametrize(
    ("flag", "clockwise", "display"),
    [(-90, 90, [360, 640]), (90, 270, [360, 640]), (180, 180, [640, 360]), (0, 0, [640, 360])],
)
def test_rotation_swaps_the_displayed_size(flag: int, clockwise: int, display: list[int]) -> None:
    data = raw()
    data["streams"][0]["side_data_list"] = [{"side_data_type": "Display Matrix", "rotation": flag}]
    v = summarize(data)["video"]
    assert v["rotation"] == clockwise and v["display"] == display
    assert (v["width"], v["height"]) == (640, 360)  # the coded size is kept as is


def test_variable_frame_rate_is_flagged() -> None:
    data = raw()
    data["streams"][0].update(avg_frame_rate="2997/100", r_frame_rate="60/1")
    v = summarize(data)["video"]
    assert v["vfr"] is True and v["fps"] == 29.97
    # a stream whose frame count is far from duration * rate is variable, whatever the labels say
    data["streams"][0].update(
        avg_frame_rate="30/1", r_frame_rate="30/1", nb_frames="70", duration="4.0"
    )
    assert summarize(data)["video"]["vfr"] is True


def test_a_constant_rate_stream_reports_its_nominal_rate_despite_muxer_rounding() -> None:
    """ffmpeg 7.1 stores an MP4 video duration one frame short, so avg_frame_rate (frames divided
    by that duration) reads 30.405 for a 75-frame, 30 fps clip. It must still be 30 and not VFR."""
    data = raw()
    data["streams"][0].update(
        avg_frame_rate="1125/37", r_frame_rate="30/1", nb_frames="75", duration="2.466667"
    )
    v = summarize(data)["video"]
    assert v["fps"] == 30.0 and v["vfr"] is False


def test_cover_art_is_not_a_video_stream() -> None:
    data = raw()
    data["streams"] = [
        {
            "codec_type": "video",
            "codec_name": "mjpeg",
            "width": 500,
            "height": 500,
            "disposition": {"attached_pic": 1},
        },
        data["streams"][1],
    ]
    meta = summarize(data)
    assert meta["video"] is None and meta["streams"]["video"] == 0 and meta["audio"] is not None


def test_missing_duration_falls_back_to_the_streams() -> None:
    data = raw()
    del data["format"]["duration"]
    data["streams"][1]["duration"] = "3.5"
    assert summarize(data)["duration"] == 3.5
    data["streams"][1].pop("duration")
    assert summarize(data)["duration"] is None


def test_streams_that_are_neither_are_counted_and_extra_streams_ignored() -> None:
    data = raw()
    data["streams"] += [
        {"codec_type": "subtitle", "codec_name": "mov_text"},
        {"codec_type": "data", "codec_name": "bin_data"},
        {"codec_type": "audio", "codec_name": "ac3", "channels": 6},
    ]
    meta = summarize(data)
    assert meta["streams"] == {"video": 1, "audio": 2, "subtitle": 1, "other": 1}
    assert meta["audio"]["codec"] == "aac"  # the first audio stream is the one used


def test_junk_values_do_not_crash() -> None:
    data = {
        "format": {"duration": "N/A"},
        "streams": [
            {
                "codec_type": "video",
                "width": 2,
                "height": 2,
                "avg_frame_rate": "0/0",
                "r_frame_rate": "abc",
            }
        ],
    }
    meta = summarize(data)
    assert (
        meta["duration"] is None
        and meta["video"]["fps"] is None
        and meta["video"]["nb_frames"] is None
    )


def test_every_sniffable_av_type_has_a_forced_demuxer() -> None:
    """Forcing the demuxer stops a crafted file from selecting a playlist/concat demuxer."""
    assert {".mp4", ".mov", ".m4a", ".mkv", ".wav", ".mp3", ".ogg", ".flac"} <= set(DEMUXERS)
    assert not {"m3u8", "concat", "hls"} & set(DEMUXERS.values())


# -- through the sandboxed worker --------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.parametrize(
    ("name", "media", "expect"),
    [
        (
            "clip_landscape.mp4",
            MediaType.VIDEO,
            "video · 4.00s · 640x360 @30fps · h264 · audio aac stereo 48kHz",
        ),
        (
            "clip_portrait.mp4",
            MediaType.VIDEO,
            "video · 3.00s · 360x640 @30fps · h264 · audio aac stereo 48kHz",
        ),
        ("clip_silent.mp4", MediaType.VIDEO, "video · 3.00s · 640x360 @25fps · h264 · no audio"),
        (
            "clip_rotated.mp4",
            MediaType.VIDEO,
            "video · 4.00s · 360x640 @30fps · h264 · rotated 270° (auto-applied) · audio",
        ),
        ("tone.wav", MediaType.AUDIO, "audio · 3.00s · audio pcm_s16le 1ch 44.1kHz"),
    ],
)
def test_inspect_lines(engine: Engine, name: str, media: MediaType, expect: str) -> None:
    info = engine.store.put_file(AV / name)
    assert info.media is media
    line = engine.inspect(info)
    assert line.startswith(expect) and line.endswith(info.short) and "\n" not in line


@needs_ffmpeg
def test_probe_metadata_is_cached_by_content(engine: Engine) -> None:
    info = engine.store.put_file(AV / "clip_landscape.mp4")
    first = engine.probe(info)
    assert first["video"]["width"] == 640 and first["audio"]["sample_rate"] == 48000
    assert engine.probe(info) == first  # served from the cache the second time


@needs_ffmpeg
def test_a_damaged_file_gives_a_clean_message_without_internal_paths(
    engine: Engine, tmp_path: Path
) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 2)
    info = engine.store.put_file(bad)
    assert info.media is MediaType.VIDEO  # sniffed from the magic bytes
    line = engine.inspect(info)
    assert "unreadable: the file:" in line
    assert str(engine.home) not in line and "/store/" not in line


@needs_ffmpeg
def test_a_playlist_is_text_never_probed_as_video(engine: Engine, tmp_path: Path) -> None:
    playlist = tmp_path / "evil.m3u8"
    playlist.write_text("#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n")
    info = engine.store.put_file(playlist)
    assert info.media is MediaType.TEXT
    assert "lines" in engine.probe(info)  # handled as text, ffprobe never sees it
