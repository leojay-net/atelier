"""SRT/WebVTT parsing: tolerant of real files, strict only about timing, safe on hostile ones."""

from __future__ import annotations

import pytest

from atelier.av.subtitles import MAX_BYTES, Cue, SubtitleError, clean, parse

SRT = """1
00:00:01,000 --> 00:00:02,500
Hello there

2
00:00:03,000 --> 00:00:05,250
Two lines
of text
"""

VTT = """WEBVTT - a comment on the header

NOTE this block is ignored
even across lines

STYLE
::cue { color: red }

intro
00:01.000 --> 00:02.500 align:start position:10%
<v Roger>Hello <b>there</b></v>

01:00:03.500 --> 01:00:05.000
Late cue
"""


def test_srt() -> None:
    assert parse(SRT) == [Cue(1.0, 2.5, "Hello there"), Cue(3.0, 5.25, "Two lines\nof text")]


def test_webvtt_with_header_notes_style_identifiers_settings_and_hours() -> None:
    assert parse(VTT) == [Cue(1.0, 2.5, "Hello there"), Cue(3603.5, 3605.0, "Late cue")]


@pytest.mark.parametrize(
    ("time", "seconds"),
    [("00:00:01,5", 1.5), ("00:00:01.50", 1.5), ("0:01.005", 1.005), ("10:00:00,000", 36000.0)],
)
def test_timestamp_variants(time: str, seconds: float) -> None:
    assert parse(f"{time} --> 99:59:59,999\nx")[0].start == seconds


def test_windows_line_endings_bom_and_missing_indexes() -> None:
    raw = (
        "﻿00:00:01,000 --> 00:00:02,000\r\nfirst\r\n\r\n00:00:03,000 --> 00:00:04,000\r\nsecond\r\n"
    )
    assert [c.text for c in parse(raw)] == ["first", "second"]


def test_a_missing_final_blank_line_is_fine() -> None:
    assert parse("00:00:01,000 --> 00:00:02,000\nlast").pop().text == "last"


@pytest.mark.parametrize(
    ("dirty", "expected"),
    [
        ("<i>slanted</i> and <font color='red'>red</font>", "slanted and red"),
        ("{\\an8}top text", "top text"),
        ("{\\pos(10,10)\\c&H0000FF&}styled", "styled"),
        ("Tom &amp; Jerry &lt;3", "Tom & Jerry <3"),
        ("a b   c\t d", "a b c d"),
        ("nul\x00 and bell\x07 and esc\x1b[31m", "nul and bell and esc[31m"),
        ("line separator", "lineseparator"),
        ("  padded  \n\n  lines  ", "padded\nlines"),
        ("<b></b>", ""),
    ],
)
def test_clean_keeps_only_plain_words(dirty: str, expected: str) -> None:
    assert clean(dirty) == expected


def test_a_hostile_file_cannot_smuggle_styling_or_control_sequences() -> None:
    raw = (
        "00:00:01,000 --> 00:00:02,000\n{\\an8}{\\fs200}<script>alert(1)</script>Buy now\x00\x1b[2J"
    )
    (cue,) = parse(raw)
    assert (
        "{" not in cue.text
        and "<" not in cue.text
        and "\x00" not in cue.text
        and "\x1b" not in cue.text
    )
    assert cue.text.startswith("alert(1)Buy now")  # tags dropped, words kept


def test_cues_with_no_words_are_dropped_and_overlaps_are_kept() -> None:
    raw = (
        "00:00:01,000 --> 00:00:03,000\nfirst\n\n"
        "00:00:02,000 --> 00:00:04,000\n<b></b>\n\n"
        "00:00:02,500 --> 00:00:03,500\nsecond"
    )
    assert [(c.start, c.text) for c in parse(raw)] == [(1.0, "first"), (2.5, "second")]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "no captions found"),
        ("just some words\nwith no times", "no captions found"),
        (
            "00:00:03,000 --> 00:00:01,000\nbackwards",
            "captions line 1: the cue ends (1s) before it starts",
        ),
        (
            "hello\n\n0:0:1,0 --> soon\ntext",
            "captions line 3: cannot read the time range '0:0:1,0 --> soon'",
        ),
    ],
)
def test_errors_say_where_and_what(raw: str, message: str) -> None:
    with pytest.raises(SubtitleError) as exc:
        parse(raw)
    assert message in str(exc.value)


def test_size_is_limited() -> None:
    with pytest.raises(SubtitleError, match="too large"):
        parse("x" * (MAX_BYTES + 1))


def test_a_long_error_excerpt_is_truncated() -> None:
    raw = "00:00:01,000 --> " + "9" * 500
    with pytest.raises(SubtitleError) as exc:
        parse(raw)
    assert len(str(exc.value)) < 200
