"""Caption burn-in helpers."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import structlog

from video_editor_ffmpeg.run import run_ffmpeg

logger = structlog.get_logger(__name__)


def _escape_ffmpeg_text(text: str) -> str:
    """Escape characters that FFmpeg drawtext interprets specially.

    drawtext treats `:` as a key/value separator, `,` as a filter-chain
    separator, `%` as an expansion prefix, `{}` as expression delimiters,
    and `'` as the text-quoting character. Backslashes must also be doubled.
    """
    # Order matters: escape backslashes first so we don't double-escape
    # the backslashes introduced by the other replacements.
    return (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace(",", "\\,")
        .replace("%", "\\%")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )


def _group_words_to_lines(
    words: list[dict[str, Any]],
    max_chars: int = 36,
    max_duration: float = 4.0,
    max_gap: float = 0.3,
) -> list[dict[str, Any]]:
    """Group word-level timings into caption lines."""
    if not words:
        return []

    groups: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = [words[0]]

    def _flush() -> None:
        if not current:
            return
        text = " ".join(w["word"] for w in current)
        groups.append(
            {
                "text": text,
                "start": current[0]["start"],
                "end": current[-1]["end"],
            }
        )
        current.clear()

    for prev, word in zip(words, words[1:], strict=False):
        current.append(word)
        text = " ".join(w["word"] for w in current)
        gap = word["start"] - prev["end"]
        duration = word["end"] - current[0]["start"]
        if len(text) > max_chars or gap > max_gap or duration > max_duration:
            # Roll back the trigger word and flush.
            current.pop()
            _flush()
            current.append(word)

    _flush()
    return groups


def remap_transcript_to_cuts(
    transcript: dict[str, Any],
    kept_segments: list[tuple[float, float]],
) -> dict[str, Any]:
    """Shift word timings so they align with a cut/concatenated video.

    Words that fall entirely inside a kept segment are remapped as-is.
    Words that straddle a cut boundary (start in one segment and end in
    the next, or bleed past a segment edge) are clipped to the boundary
    and remapped so the audible portion of the word is still labeled.
    Words outside all kept segments (filler, silence) are dropped.
    """
    words = transcript.get("words", [])
    if not words or not kept_segments:
        return transcript

    # Build a running offset for each kept segment.
    offsets: list[float] = []
    accumulated = 0.0
    for start, end in kept_segments:
        offsets.append(accumulated - start)
        accumulated += end - start

    remapped: list[dict[str, Any]] = []
    for word in words:
        w_start = word["start"]
        w_end = word["end"]
        for idx, (seg_start, seg_end) in enumerate(kept_segments):
            # Skip segments that end before this word even starts.
            if seg_end <= w_start:
                continue
            # Skip segments that start after this word has ended.
            if seg_start >= w_end:
                break
            # Word overlaps this segment — clip to the segment bounds so
            # we keep the audible portion (handles words that bleed past cuts).
            clipped_start = max(w_start, seg_start)
            clipped_end = min(w_end, seg_end)
            if clipped_end <= clipped_start:
                break
            remapped.append(
                {
                    **word,
                    "start": clipped_start + offsets[idx],
                    "end": clipped_end + offsets[idx],
                }
            )
            # If the word fits inside this segment, we're done with it.
            # Otherwise, fall through and label the tail in the next segment.
            if w_end <= seg_end:
                break
            # Continue searching for later segments to label the tail.

    # Rebuild segment list from remapped words, splitting on large gaps.
    new_segments: list[dict[str, Any]] = []
    current_words: list[dict[str, Any]] = []
    for word in remapped:
        if not current_words:
            current_words.append(word)
            continue
        if word["start"] - current_words[-1]["end"] > 0.5:
            text = " ".join(w["word"] for w in current_words)
            new_segments.append(
                {
                    "start": current_words[0]["start"],
                    "end": current_words[-1]["end"],
                    "text": text,
                    "words": current_words,
                }
            )
            current_words = [word]
        else:
            current_words.append(word)
    if current_words:
        text = " ".join(w["word"] for w in current_words)
        new_segments.append(
            {
                "start": current_words[0]["start"],
                "end": current_words[-1]["end"],
                "text": text,
                "words": current_words,
            }
        )

    return {
        **transcript,
        "words": remapped,
        "segments": new_segments,
    }


def generate_caption_segments(
    transcript: dict[str, Any],
    max_chars: int = 36,
    max_duration: float = 4.0,
    max_gap: float = 0.3,
) -> list[dict[str, Any]]:
    """Generate caption text segments from a transcript dict."""
    words = transcript.get("words", [])
    if words:
        return _group_words_to_lines(words, max_chars, max_duration, max_gap)

    # Fallback to segment-level captions if word timestamps are missing.
    return [
        {"text": seg["text"].strip(), "start": seg["start"], "end": seg["end"]}
        for seg in transcript.get("segments", [])
        if seg["text"].strip()
    ]


def burn_subtitles(video_path: Path | str, srt_path: Path | str, output_path: Path | str) -> Path:
    """Burn an SRT subtitle file into a video.

    The SRT is copied to a temporary file with a safe name before being passed
    to the ``subtitles`` filter so that paths containing ``:``, ``'``, or ``\\``
    do not break the FFmpeg filter syntax. The temporary copy lives in the
    system temp directory and is always removed, even if FFmpeg fails (Bug #35).
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".srt", delete=False, dir=tempfile.gettempdir()
    ) as safe_srt:
        shutil.copyfile(str(srt_path), safe_srt.name)
        safe_srt_path = safe_srt.name

    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"subtitles={Path(safe_srt_path).resolve()}",
            "-c:a",
            "copy",
            str(output),
        ]
        logger.info("burning_subtitles", video=str(video_path), subtitles=str(srt_path))
        run_ffmpeg(cmd)
    finally:
        Path(safe_srt_path).unlink(missing_ok=True)
    return output


def drawtext_captions(
    video_path: Path | str,
    output_path: Path | str,
    text_segments: list[dict[str, Any]],
    style: dict[str, Any] | None = None,
) -> Path:
    """Burn captions using drawtext with per-segment timing.

    text_segments: list of {"text": str, "start": float, "end": float}
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    cfg = {
        "fontfile": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "fontsize": 48,
        "fontcolor": "white",
        "borderw": 4,
        "bordercolor": "black",
        "box": 0,
        "x": "(w-text_w)/2",
        "y": "h*0.85",
        **(style or {}),
    }

    filters = []
    for seg in text_segments:
        escaped = _escape_ffmpeg_text(seg["text"])
        enable = f"between(t,{seg['start']},{seg['end']})"
        filters.append(
            f"drawtext=text='{escaped}':fontfile={cfg['fontfile']}:"
            f"fontsize={cfg['fontsize']}:fontcolor={cfg['fontcolor']}:"
            f"borderw={cfg['borderw']}:bordercolor={cfg['bordercolor']}:"
            f"x={cfg['x']}:y={cfg['y']}:enable='{enable}'"
        )

    vf = ",".join(filters)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        vf,
        "-c:a",
        "copy",
        str(output),
    ]
    logger.info("burning_drawtext_captions", video=str(video_path), segments=len(text_segments))
    run_ffmpeg(cmd)
    return output


def write_srt(segments: list[dict[str, Any]], output_path: Path | str) -> Path:
    """Write caption segments to an SRT file."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    def _fmt(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}".replace(".", ",")

    lines = []
    for idx, seg in enumerate(segments, start=1):
        lines.append(str(idx))
        lines.append(f"{_fmt(seg['start'])} --> {_fmt(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")

    output.write_text("\n".join(lines), encoding="utf-8")
    return output
