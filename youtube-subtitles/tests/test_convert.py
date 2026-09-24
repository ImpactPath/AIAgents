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


# --- TXT layouts and sentence splitting -------------------------------------

import pytest  # noqa: E402
from pathlib import Path  # noqa: E402

from app.convert import (  # noqa: E402
    PARAGRAPH_MAX_CHARS,
    render_txt,
    render_txt_paragraphs,
    render_txt_sentences,
    split_sentences,
)

SAMPLE_LINES = (Path(__file__).parent / "data" / "sample.en-orig.auto.txt").read_text("utf-8").splitlines()


def cues_from_lines(lines, step=1500, gaps=None):
    """One cue per line, `step` ms apart, back to back; gaps adds extra ms before line i."""
    cues, t = [], 0
    for i, line in enumerate(lines):
        t += (gaps or {}).get(i, 0)
        cues.append(Cue(t, t + step, line))
        t += step
    return cues


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Mr. Smith met Dr. Jones. They talked.", ["Mr. Smith met Dr. Jones.", "They talked."]),
        ("Ms. Lee vs. Prof. Kim, etc. and more. Done", ["Ms. Lee vs. Prof. Kim, etc. and more.", "Done"]),
        ("Use e.g. apples, i.e. fruit. Next one.", ["Use e.g. apples, i.e. fruit.", "Next one."]),
        ("The U.S. and the U.K. agreed. At 9 a.m. sharp. Fine", ["The U.S. and the U.K. agreed.", "At 9 a.m. sharp.", "Fine"]),
        ("It took 3.5 minutes. Then 2.75 more.", ["It took 3.5 minutes.", "Then 2.75 more."]),
        ("J. R. R. Tolkien wrote it. So did I. Yes.", ["J. R. R. Tolkien wrote it.", "So did I.", "Yes."]),
        ('She said "stop." He stopped. (Quietly.) Then', ['She said "stop."', "He stopped.", "(Quietly.)", "Then"]),
        ("Really? Yes! Wow… ok", ["Really?", "Yes!", "Wow…", "ok"]),
        ("See fig. 2 and No. 5 please. I said no. Fine.", ["See fig. 2 and No. 5 please.", "I said no.", "Fine."]),
        ("  lots   of\n space .  here  ", ["lots of space .", "here"]),
        ("오늘은 자막을 봅니다. 그리고 저장합니다. 끝", ["오늘은 자막을 봅니다.", "그리고 저장합니다.", "끝"]),
        ("今日は晴れです。明日は雨！本当？はい", ["今日は晴れです。", "明日は雨！", "本当？", "はい"]),
        ("你好。「谢谢。」再见", ["你好。", "「谢谢。」", "再见"]),
        ("", []),
    ],
)
def test_split_sentences(text, expected):
    assert split_sentences(text) == expected


def test_sentences_layout_one_per_line():
    cues = cues_from_lines(["Hi, I'm Amina and welcome to the first", "episode. This channel is", "great! Is it?"])
    assert render_txt_sentences(cues) == "Hi, I'm Amina and welcome to the first episode.\nThis channel is great!\nIs it?\n"
    assert convert("", "txt", "sentences") == ""


def test_paragraphs_break_on_long_pause_after_sentence_end():
    lines = ["We start here.", "Still talking", "about this. Done.", "After a pause", "we continue."]
    # 2.5 s pause before "about this" (mid-sentence: no break) and before "After a pause" (break).
    cues = cues_from_lines(lines, gaps={2: 2500, 3: 2500})
    assert render_txt_paragraphs(cues) == (
        "We start here. Still talking about this. Done.\n\nAfter a pause we continue.\n"
    )


def test_paragraphs_short_pause_does_not_break():
    cues = cues_from_lines(["One.", "Two."], gaps={1: 1999})
    assert render_txt_paragraphs(cues) == "One. Two.\n"


def test_paragraphs_split_by_length_only_at_sentence_end():
    sentences = [f"Sentence number {i:02d} is split across two cues." for i in range(30)]
    cues = cues_from_lines([part for s in sentences for part in s.split(" is ", 1)])
    paragraphs = render_txt_paragraphs(cues).rstrip("\n").split("\n\n")
    assert len(paragraphs) > 1
    for para in paragraphs[:-1]:
        assert len(para) >= PARAGRAPH_MAX_CHARS and para.endswith(".")
        assert "\n" not in para
    assert " ".join(paragraphs) == " ".join(s.replace(" is ", " ") for s in sentences)


def test_cues_layout_unchanged():
    cues = cues_from_lines(["first line", "first line", "second. third"])
    assert render_txt(cues) == "first line\nsecond. third\n"
    assert convert(YOUTUBE_AUTO_VTT, "txt", "cues") == vtt_to_txt(YOUTUBE_AUTO_VTT)


def test_convert_layout_dispatch_and_validation():
    assert convert(YOUTUBE_AUTO_VTT, "txt") == "we're going to talk about subtitles today and why they matter\n"
    assert convert(YOUTUBE_AUTO_VTT, "srt", "bogus") == vtt_to_srt(YOUTUBE_AUTO_VTT)
    with pytest.raises(ValueError):
        convert(YOUTUBE_AUTO_VTT, "txt", "bogus")


def test_regression_real_sample_paragraphs_end_at_sentence_boundaries():
    assert len(SAMPLE_LINES) == 40
    out = render_txt_paragraphs(cues_from_lines(SAMPLE_LINES))
    lines = [line for line in out.split("\n") if line]
    assert len(lines) >= 2
    for line in lines[:-1]:
        assert line[-1] in ".!?\"')]”’", line[-40:]
    assert " ".join(lines).split() == " ".join(SAMPLE_LINES).split()
