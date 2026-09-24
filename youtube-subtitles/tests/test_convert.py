from app.convert import (
    Cue,
    clean_cues,
    convert,
    format_timestamp,
    parse_cues,
    parse_timestamp,
    render_vtt,
    vtt_to_srt,
    vtt_to_txt,
)

BASIC_VTT = """WEBVTT
Kind: captions
Language: en

NOTE This is a comment
spanning two lines

STYLE
::cue { color: red; }

REGION
id:fred width:40%

intro
00:00:01.000 --> 00:00:03.500 align:start position:0%
<v Roger>Hello <b>world</b> &amp; friends

2
00:03.500 --> 00:05.250
Tom &lt;3 Jerry&nbsp;forever
<i>second line</i>

01:02:03.004 --> 01:02:04.000
Late cue
"""

# Realistic YouTube auto-caption sample: <c> tags, word timestamps, position
# settings, whitespace-only lines, and rolling duplicate cues.
YOUTUBE_AUTO_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.320 --> 00:00:02.310 align:start position:0%
 
we're<00:00:00.719><c> going</c><00:00:00.880><c> to</c><00:00:01.040><c> talk</c>

00:00:02.310 --> 00:00:02.320 align:start position:0%
we're going to talk
 

00:00:02.320 --> 00:00:04.630 align:start position:0%
we're going to talk
about<00:00:02.800><c> subtitles</c><00:00:03.200><c> today</c>

00:00:04.630 --> 00:00:04.640 align:start position:0%
about subtitles today
 

00:00:04.640 --> 00:00:06.000 align:start position:0%
about subtitles today
<c.colorE5E5E5>and</c><00:00:05.100><c> why</c><00:00:05.400><c> they</c><00:00:05.600><c> matter</c>
"""


def test_timestamps():
    assert parse_timestamp("00:00:01.000") == 1000
    assert parse_timestamp("01:02:03,004") == 3723004
    assert parse_timestamp("02:03.5") == 123500
    assert format_timestamp(3723004) == "01:02:03,004"
    assert format_timestamp(1500, ".") == "00:00:01.500"


def test_basic_vtt_to_srt():
    assert vtt_to_srt(BASIC_VTT) == (
        "1\n00:00:01,000 --> 00:00:03,500\nHello world & friends\n\n"
        "2\n00:00:03,500 --> 00:00:05,250\nTom <3 Jerry forever\nsecond line\n\n"
        "3\n01:02:03,004 --> 01:02:04,000\nLate cue\n"
    )


def test_header_note_style_region_and_ids_dropped():
    out = vtt_to_srt(BASIC_VTT)
    for junk in ("WEBVTT", "Kind:", "NOTE", "::cue", "REGION", "intro", "align:", "position:", "<"):
        assert junk not in out.replace("<3", "")


def test_youtube_auto_captions_srt():
    assert vtt_to_srt(YOUTUBE_AUTO_VTT) == (
        "1\n00:00:00,320 --> 00:00:02,320\nwe're going to talk\n\n"
        "2\n00:00:02,320 --> 00:00:04,640\nabout subtitles today\n\n"
        "3\n00:00:04,640 --> 00:00:06,000\nand why they matter\n"
    )


def test_youtube_auto_captions_txt():
    assert vtt_to_txt(YOUTUBE_AUTO_VTT) == "we're going to talk\nabout subtitles today\nand why they matter\n"


def test_txt_joins_lines_and_removes_consecutive_duplicates():
    vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\nHi\n\n00:05.000 --> 00:06.000\nHi\n\n00:07.000 --> 00:08.000\nTwo\nlines\n"
    assert vtt_to_txt(vtt) == "Hi\nTwo lines\n"


def test_empty_cues_dropped_and_numbering_restarts():
    vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\n<c> </c>\n\n00:02.000 --> 00:03.000\nReal\n"
    assert vtt_to_srt(vtt) == "1\n00:00:02,000 --> 00:00:03,000\nReal\n"


def test_distant_repeats_are_kept():
    cues = [Cue(0, 1000, "Hey!"), Cue(30000, 31000, "Hey!")]
    assert len(clean_cues(cues)) == 2


def test_srt_input_and_crlf():
    srt = "﻿1\r\n00:00:01,000 --> 00:00:02,000\r\nFirst\r\n\r\n2\r\n00:00:02,500 --> 00:00:04,000\r\nSecond\r\n"
    cues = parse_cues(srt)
    assert [(c.start, c.end, c.text) for c in cues] == [(1000, 2000, "First"), (2500, 4000, "Second")]
    assert convert(srt, "vtt") == "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nFirst\n\n00:00:02.500 --> 00:00:04.000\nSecond\n"


def test_render_vtt_roundtrip():
    cues = clean_cues(parse_cues(BASIC_VTT))
    assert clean_cues(parse_cues(render_vtt(cues))) == cues


def test_empty_input():
    assert vtt_to_srt("WEBVTT\n\n") == ""
    assert vtt_to_txt("") == ""
