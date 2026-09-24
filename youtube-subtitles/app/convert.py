"""Pure subtitle conversion helpers.

Everything goes through one small pipeline:

    parse_cues(text)  -> list[Cue]   (WebVTT or SRT in, cleaned cue text out)
    clean_cues(cues)  -> list[Cue]   (drops empty cues and YouTube "rolling" duplicates)
    render_srt / render_vtt / render_txt

No I/O and no third-party imports, so it is easy to test.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

FORMATS = ("srt", "vtt", "txt")

# HH:MM:SS.mmm, MM:SS.mmm, and the SRT flavor with a comma.
_TS = r"(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3}"
_TIMING_RE = re.compile(rf"^\s*({_TS})\s*-->\s*({_TS})")
_TS_PARTS_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})$")
_TAG_RE = re.compile(r"<[^>]*>")
_SKIP_BLOCKS = ("NOTE", "STYLE", "REGION")

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


def render_srt(cues: list[Cue]) -> str:
    blocks = [
        f"{i}\n{format_timestamp(c.start, ',')} --> {format_timestamp(c.end, ',')}\n{c.text}\n"
        for i, c in enumerate(cues, start=1)
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


def convert(text: str, fmt: str) -> str:
    """Convert raw VTT/SRT subtitle text into the requested format."""
    if fmt not in FORMATS:
        raise ValueError(f"unsupported format: {fmt}")
    cues = clean_cues(parse_cues(text))
    return {"srt": render_srt, "vtt": render_vtt, "txt": render_txt}[fmt](cues)


def vtt_to_srt(vtt: str) -> str:
    return convert(vtt, "srt")


def vtt_to_txt(vtt: str) -> str:
    return convert(vtt, "txt")
