"""Pure subtitle conversion helpers.

Everything goes through one small pipeline:

    parse_cues(text)  -> list[Cue]   (WebVTT or SRT in, cleaned cue text out)
    clean_cues(cues)  -> list[Cue]   (drops empty cues and YouTube "rolling" duplicates)
    render_srt / render_vtt / render_txt (cues, sentences or paragraphs)

No I/O and no third-party imports, so it is easy to test.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

FORMATS = ("srt", "vtt", "txt")
LAYOUTS = ("paragraphs", "sentences", "cues")  # TXT layouts; the first is the default

# Tokens that end with a period but do not end a sentence (compared lowercased,
# without the final period). Extend freely.
ABBREVIATIONS = frozenset(
    "mr mrs ms dr prof sr jr st vs etc e.g i.e no fig approx dept inc ltd co u.s u.k a.m p.m".split()
)

# A new paragraph starts after a sentence that ends a cue followed by a pause
# this long, or after any sentence once the paragraph is this long.
PARAGRAPH_GAP_MS = 2000
PARAGRAPH_MAX_CHARS = 600

# HH:MM:SS.mmm, MM:SS.mmm, and the SRT flavor with a comma.
_TS = r"(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3}"
_TIMING_RE = re.compile(rf"^\s*({_TS})\s*-->\s*({_TS})")
_TS_PARTS_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})$")
_TAG_RE = re.compile(r"<[^>]*>")
_SKIP_BLOCKS = ("NOTE", "STYLE", "REGION")
# Sentence end: Latin punctuation needs following whitespace; CJK full-width
# punctuation does not. Both may be followed by closing quotes or brackets.
_CLOSERS = "\"')]\u201d\u2019\u300d\u300f\uff09"
_OPENERS = "\"'([\u201c\u2018"
_SENTENCE_END_RE = re.compile(
    rf"[.!?\u2026]+[{re.escape(_CLOSERS)}]*(?=\s)|[\u3002\uff01\uff1f]+[{re.escape(_CLOSERS)}]*"
)

# Duplicate cues further apart than this are treated as intentional repeats.
_MERGE_GAP_MS = 1000


@dataclass
class Cue:
    start: int  # milliseconds
    end: int  # milliseconds
    text: str  # cleaned text, lines joined with "\n"


def parse_timestamp(value: str) -> int:
    """Parse a VTT or SRT timestamp into milliseconds."""
    match = _TS_PARTS_RE.match(value.strip())
    if not match:
        raise ValueError(f"bad timestamp: {value!r}")
    hours, minutes, seconds, frac = match.groups()
    millis = int(frac.ljust(3, "0"))
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + millis


def format_timestamp(ms: int, sep: str = ",") -> str:
    """Format milliseconds as HH:MM:SS,mmm (SRT) or HH:MM:SS.mmm (VTT)."""
    ms = max(0, int(ms))
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{millis:03d}"


def clean_text(raw: str) -> str:
    """Strip inline tags, unescape entities, trim lines, and drop empty lines."""
    lines = []
    for line in raw.split("\n"):
        line = _TAG_RE.sub("", line)
        line = html.unescape(line).replace("\xa0", " ")
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def parse_cues(text: str) -> list[Cue]:
    """Parse WebVTT or SRT text into cues with cleaned text.

    Blocks are separated by truly empty lines. YouTube auto captions put a
    single space on some text lines, so whitespace-only lines are kept inside
    a cue and removed later by clean_text.
    """
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n{2,}", text):
        lines = block.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            continue
        head = lines[0].strip()
        if head.startswith("WEBVTT") or any(
            head == kw or head.startswith(kw + " ") or head.startswith(kw + "\t") for kw in _SKIP_BLOCKS
        ):
            continue
        for index, line in enumerate(lines):
            match = _TIMING_RE.match(line)
            if match:
                # Lines before the timing line are cue identifiers; anything after
                # the second timestamp on the line is cue settings. Both are dropped.
                start, end = (parse_timestamp(g) for g in match.groups())
                cues.append(Cue(start, end, clean_text("\n".join(lines[index + 1 :]))))
                break
    return cues


def clean_cues(cues: list[Cue]) -> list[Cue]:
    """Drop empty cues and collapse YouTube "rolling" captions.

    Auto captions repeat the previous line at the top of the next cue and add
    tiny cues that only repeat the previous text. We strip a leading run of
    lines that equals the tail of the previous cue, then drop a cue whose text
    equals the previously kept cue (extending that cue's end time instead).
    """
    out: list[Cue] = []
    prev_lines: list[str] = []
    for cue in cues:
        lines = cue.text.split("\n") if cue.text else []
        if not lines:
            continue
        overlap = 0
        for k in range(min(len(lines) - 1, len(prev_lines)), 0, -1):
            if lines[:k] == prev_lines[-k:]:
                overlap = k
                break
        prev_lines = lines
        text = "\n".join(lines[overlap:])
        last = out[-1] if out else None
        if last and text == last.text and cue.start - last.end <= _MERGE_GAP_MS:
            last.end = max(last.end, cue.end)
            continue
        out.append(Cue(cue.start, cue.end, text))
    return out


def render_srt(cues: list[Cue], first: int = 1) -> str:
    blocks = [
        f"{i}\n{format_timestamp(c.start, ',')} --> {format_timestamp(c.end, ',')}\n{c.text}\n"
        for i, c in enumerate(cues, start=first)
    ]
    return "\n".join(blocks)


def render_vtt(cues: list[Cue]) -> str:
    blocks = ["WEBVTT\n"] + [
        f"{format_timestamp(c.start, '.')} --> {format_timestamp(c.end, '.')}\n{c.text}\n" for c in cues
    ]
    return "\n".join(blocks)


def render_txt(cues: list[Cue]) -> str:
    lines: list[str] = []
    for cue in cues:
        line = " ".join(cue.text.split("\n")).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return "\n".join(lines) + "\n" if lines else ""


def _is_false_end(text: str, match: re.Match) -> bool:
    """True when a period follows an initial or a known abbreviation."""
    if match.group().rstrip(_CLOSERS) != ".":
        return False
    before = text[: match.start()].split()
    token = before[-1].lstrip(_OPENERS) if before else ""
    if len(token) == 1 and token.isascii() and token.isalpha() and token != "I":
        return True  # initials like "J. R. Tolkien" (but not the pronoun "I")
    if token.lower() == "no":  # "No. 5" is an abbreviation, a spoken "no." is not
        return text[match.end() :].lstrip()[:1].isdigit()
    return token.lower() in ABBREVIATIONS


def _sentence_ends(text: str) -> list[int]:
    """Offsets just past each sentence end in text (the end of input is always one)."""
    ends = [m.end() for m in _SENTENCE_END_RE.finditer(text) if not _is_false_end(text, m)]
    if not ends or ends[-1] < len(text):
        ends.append(len(text))
    return ends


def split_sentences(text: str) -> list[str]:
    """Split running text into sentences with a small punctuation heuristic.

    Decimals like "3.5" never split because a Latin sentence end must be
    followed by whitespace. A trailing fragment without punctuation is kept.
    """
    out, start = [], 0
    for end in _sentence_ends(text):
        sentence = " ".join(text[start:end].split())
        if sentence:
            out.append(sentence)
        start = end
    return out


def _joined(cues: list[Cue]) -> tuple[str, dict[int, int]]:
    """Join cue texts with spaces; map each cue's end offset to the pause after it."""
    items: list[list] = []  # [text, start, end]
    for cue in cues:
        line = " ".join(cue.text.split())
        if not line:
            continue
        if items and items[-1][0] == line:
            items[-1][2] = max(items[-1][2], cue.end)
        else:
            items.append([line, cue.start, cue.end])
    text, gaps = "", {}
    for i, (line, _start, end) in enumerate(items):
        text += (" " if text else "") + line
        if i + 1 < len(items):
            gaps[len(text)] = items[i + 1][1] - end
    return text, gaps


def render_txt_sentences(cues: list[Cue]) -> str:
    sentences = split_sentences(_joined(cues)[0])
    return "\n".join(sentences) + "\n" if sentences else ""


def render_txt_paragraphs(cues: list[Cue]) -> str:
    text, gaps = _joined(cues)
    paragraphs: list[str] = []
    current: list[str] = []
    start = 0
    for end in _sentence_ends(text):
        sentence = " ".join(text[start:end].split())
        start = end
        if sentence:
            current.append(sentence)
        long_pause = gaps.get(end, 0) >= PARAGRAPH_GAP_MS
        if current and (long_pause or len(" ".join(current)) >= PARAGRAPH_MAX_CHARS):
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs) + "\n" if paragraphs else ""


_TXT_RENDERERS = {"paragraphs": render_txt_paragraphs, "sentences": render_txt_sentences, "cues": render_txt}


def format_duration(seconds: int | float | None) -> str:
    """h:mm:ss, or mm:ss under an hour; "" when unknown."""
    if seconds is None or seconds < 0:
        return ""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _header_lines(meta: dict) -> list[str]:
    name = str(meta.get("track_name") or "").strip()
    if meta.get("track_auto"):
        # Auto track names already say "(auto-generated)"; show it once, as a tag.
        name = re.sub(r"\s*\(auto-generated\)$", "", name, flags=re.I)
        name = f"{name} [auto-generated]".strip()
    lang = str(meta.get("track_lang") or "").strip()
    subtitles = f"{name} ({lang})" if name and lang else name or lang
    fields = [
        ("Title", meta.get("title")),
        ("Channel", meta.get("channel")),
        ("Duration", format_duration(meta.get("duration"))),
        ("Published", meta.get("upload_date")),
        ("URL", meta.get("url")),
        ("Subtitles", subtitles),
    ]
    lines = []
    for label, value in fields:
        # One line per field; "-->" would end a VTT NOTE or look like SRT timing.
        value = " ".join(str(value or "").split()).replace("-->", "->")
        if value:
            lines.append(f"{label}: {value}")
    return lines


def render_header(meta: dict, fmt: str) -> str:
    """Metadata block to put before the cues, ending with a blank line ("" when meta is empty).

    txt: plain lines. vtt: a NOTE comment block (goes right after "WEBVTT").
    srt: a cue from 0 to 1 ms so every player still accepts the file; real
    cues are then numbered from 2.
    """
    if fmt not in FORMATS:
        raise ValueError(f"unsupported format: {fmt}")
    lines = _header_lines(meta)
    if not lines:
        return ""
    body = "\n".join(lines)
    if fmt == "vtt":
        return f"NOTE\n{body}\n\n"
    if fmt == "srt":
        return f"1\n{format_timestamp(0)} --> {format_timestamp(1)}\n{body}\n\n"
    return f"{body}\n\n"


def convert(text: str, fmt: str, layout: str = "paragraphs", header: dict | None = None) -> str:
    """Convert raw VTT/SRT subtitle text into the requested format.

    layout only applies to fmt="txt" and is ignored otherwise. header is the
    metadata for render_header; None (or a track with no cues) adds nothing.
    """
    if fmt not in FORMATS:
        raise ValueError(f"unsupported format: {fmt}")
    if fmt == "txt" and layout not in LAYOUTS:
        raise ValueError(f"unsupported layout: {layout}")
    cues = clean_cues(parse_cues(text))
    head = render_header(header, fmt) if header and cues else ""
    if fmt == "txt":
        return head + _TXT_RENDERERS[layout](cues)
    if fmt == "srt":
        return head + render_srt(cues, first=2 if head else 1)
    body = render_vtt(cues)
    prefix = "WEBVTT\n\n"
    return prefix + head + body[len(prefix) :] if head else body


def vtt_to_srt(vtt: str) -> str:
    return convert(vtt, "srt")


def vtt_to_txt(vtt: str) -> str:
    """One cue per line (the original TXT behavior)."""
    return convert(vtt, "txt", "cues")
