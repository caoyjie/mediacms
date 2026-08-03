"""Small, deterministic WebVTT helpers used by the YouTube importer."""

from dataclasses import dataclass
import html
import json
import re


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    start: float
    end: float
    text: str


_TIMESTAMP = re.compile(r"^(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})$")


def _seconds(value):
    match = _TIMESTAMP.match(value.strip())
    if not match:
        raise ValueError("invalid subtitle timestamp")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = int(match.group(4))
    if minutes > 59 or seconds > 59:
        raise ValueError("invalid subtitle timestamp")
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def parse_webvtt(value):
    if not isinstance(value, str) or not value.lstrip().startswith("WEBVTT"):
        raise ValueError("subtitle must be WebVTT")
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            index += 1
            continue
        if "-->" not in line:
            index += 1
            continue
        left, right = (part.strip() for part in line.split("-->", 1))
        right = right.split(None, 1)[0]
        start, end = _seconds(left), _seconds(right)
        index += 1
        text_lines = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index].strip())
            index += 1
        text = " ".join(text_lines).strip()
        if end > start and text:
            cues.append(SubtitleCue(start, end, text))
    return cues


def _timestamp(seconds):
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def normalize_webvtt(value):
    cues = parse_webvtt(value)
    return "WEBVTT\n\n" + "\n\n".join(
        f"{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n{html.escape(cue.text)}"
        for cue in cues
    ) + ("\n" if cues else "")


def normalize_caption_payload(value):
    """Normalize WebVTT or YouTube JSON3 caption payload to WebVTT."""
    stripped = value.lstrip()
    if not stripped.startswith("{"):
        return normalize_webvtt(value)
    try:
        document = json.loads(value)
        events = document.get("events", [])
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("caption payload is not valid JSON3") from error
    cues = []
    for event in events:
        start = float(event.get("tStartMs", 0)) / 1000
        duration = float(event.get("dDurationMs", 0)) / 1000
        text = "".join(segment.get("utf8", "") for segment in event.get("segs", []))
        if duration > 0 and text.strip():
            cues.append(SubtitleCue(start, start + duration, text.replace("\n", " ")))
    return normalize_webvtt("WEBVTT\n\n" + "\n\n".join(
        f"{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n{cue.text}" for cue in cues
    ))


def build_bilingual_webvtt(primary, secondary, *, proximity=1.0):
    output = []
    for cue in primary:
        matches = [
            other for other in secondary
            if other.start <= cue.end + proximity and other.end >= cue.start - proximity
        ]
        text = cue.text
        if matches:
            text = f"{text} / {matches[0].text}"
        output.append(SubtitleCue(cue.start, cue.end, text))
    for cue in secondary:
        if not any(c.start <= cue.end and c.end >= cue.start for c in primary):
            output.append(cue)
    output.sort(key=lambda cue: (cue.start, cue.end, cue.text))
    return normalize_webvtt("WEBVTT\n\n" + "\n\n".join(
        f"{_timestamp(c.start)} --> {_timestamp(c.end)}\n{c.text}" for c in output
    ))
