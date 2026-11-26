"""Generate timed caption events from a transcript and an EDL."""

from __future__ import annotations

from typing import Any

from video_editor_core.caption_text import ends_sentence as _ends_sentence
from video_editor_core.caption_text import is_orphan
from video_editor_core.timeline import EDL
from video_editor_ffmpeg.captions import remap_transcript_to_cuts

from captions.models import CaptionLine, CaptionStyle, CaptionWord

#: A frame that is one very short word ("a", "of", "us.") reads as an orphan.
#: The penalty dominates the slack term so the DP only accepts one when the
#: chunk has no balanced alternative.
_SHORT_WORD_PENALTY = 10_000
_SHORT_WORD_LETTERS = 4
#: Reward for ending a frame on a clause mark, used to break exact cost ties.
_CLAUSE_ENDINGS = (",", ";", ":")
_CLAUSE_BONUS = 40


def _ends_clause(text: str) -> bool:
    """True when ``text`` ends on a comma, semicolon or colon."""
    return text.rstrip().rstrip("\"'”’)]}").endswith(_CLAUSE_ENDINGS)


def _letters(text: str) -> int:
    """Count letters in ``text``, ignoring punctuation and digits."""
    return sum(1 for char in text if char.isalpha())


def _chunks(
    words: list[CaptionWord],
    style: CaptionStyle,
) -> list[list[CaptionWord]]:
    """Split words at hard breaks: sentence ends and gaps wider than max_gap."""
    chunks: list[list[CaptionWord]] = []
    start = 0
    for index, word in enumerate(words[:-1]):
        following = words[index + 1]
        gap = following.start - word.end
        if _ends_sentence(word.text) or gap > style.max_gap:
            chunks.append(words[start : index + 1])
            start = index + 1
    chunks.append(words[start:])
    return chunks


def _greedy_frames(
    chunk: list[CaptionWord],
    style: CaptionStyle,
) -> list[list[CaptionWord]]:
    """Group one chunk greedily, as the caption renderer always has."""
    frames: list[list[CaptionWord]] = []
    current: list[CaptionWord] = [chunk[0]]
    for word in chunk[1:]:
        current.append(word)
        text = " ".join(item.text for item in current)
        duration = word.end - current[0].start
        if len(text) > style.max_chars or duration > style.max_duration:
            current.pop()
            frames.append(current)
            current = [word]
    frames.append(current)
    return frames


def _frame_fits(
    chunk: list[CaptionWord],
    start: int,
    end: int,
    style: CaptionStyle,
) -> bool:
    """True when ``chunk[start:end]`` may render as one frame."""
    if end - start == 1:
        # A single over-long word still gets its own frame (as before).
        return True
    text = " ".join(word.text for word in chunk[start:end])
    if len(text) > style.max_chars:
        return False
    return chunk[end - 1].end - chunk[start].start <= style.max_duration


def _frame_cost(
    chunk: list[CaptionWord],
    start: int,
    end: int,
    style: CaptionStyle,
) -> float:
    """Cost of one frame: squared unused width, orphan penalty, clause bonus."""
    text = " ".join(word.text for word in chunk[start:end])
    cost = float((style.max_chars - len(text)) ** 2)
    if end - start == 1 and _letters(chunk[start].text) < _SHORT_WORD_LETTERS:
        cost += _SHORT_WORD_PENALTY
    if _ends_clause(chunk[end - 1].text):
        cost -= _CLAUSE_BONUS
    return cost


def _balanced_frames(
    chunk: list[CaptionWord],
    style: CaptionStyle,
) -> list[list[CaptionWord]]:
    """Re-partition a chunk into the same number of frames, with less slack.

    Dynamic program over break positions (chunks are short). The first row is
    the greedy count, which is the minimum possible; if no partition with that
    many frames fits, the greedy grouping is returned unchanged.
    """
    greedy = _greedy_frames(chunk, style)
    target = len(greedy)
    if target <= 1:
        return greedy

    count = len(chunk)
    infinity = float("inf")
    best = [[infinity] * (count + 1) for _ in range(target + 1)]
    previous = [[-1] * (count + 1) for _ in range(target + 1)]
    best[0][0] = 0.0

    for frame_count in range(1, target + 1):
        for end in range(1, count + 1):
            for start in range(end - 1, -1, -1):
                if best[frame_count - 1][start] == infinity:
                    continue
                if not _frame_fits(chunk, start, end, style):
                    continue
                cost = best[frame_count - 1][start] + _frame_cost(chunk, start, end, style)
                if cost < best[frame_count][end]:
                    best[frame_count][end] = cost
                    previous[frame_count][end] = start

    if best[target][count] == infinity:
        return greedy

    frames: list[list[CaptionWord]] = []
    end = count
    for frame_count in range(target, 0, -1):
        start = previous[frame_count][end]
        frames.append(chunk[start:end])
        end = start
    frames.reverse()
    return frames


def _merge_orphan_lines(lines: list[CaptionLine]) -> list[CaptionLine]:
    """Merge a lone short line into a neighbour so the QA gate accepts it.

    A long-spoken token such as ``₹583.`` can fill a frame on its own yet still
    read as a stranded word. Merge it back into the previous line, or into the
    next one when it opens the run. Lines are word lists, so timings move with
    the words.
    """
    merged: list[CaptionLine] = []
    texts: list[str] = []
    pending: CaptionLine | None = None
    for line in lines:
        if pending is not None:
            line = CaptionLine(words=[*pending.words, *line.words])
            pending = None
        if is_orphan([*texts, line.text], len(texts)):
            if merged:
                merged[-1].words.extend(line.words)
                texts[-1] = merged[-1].text
                continue
            pending = line
            continue
        merged.append(line)
        texts.append(line.text)
    if pending is not None:
        merged.append(pending)
    return merged


def _group_words_to_lines(
    words: list[CaptionWord],
    style: CaptionStyle,
) -> list[CaptionLine]:
    """Group words into caption lines using the style constraints.

    A terminal punctuation mark always starts a new line. Without this a
    width-based group carries the first words of the next sentence into the
    frame ("Masters. Wait for"), which reads as one broken sentence. Gaps wider
    than ``max_gap`` break too. Inside those hard-broken chunks the words are
    re-partitioned with the same frame count as greedy grouping, but with
    balanced widths, so a sentence does not end on an orphaned short word.
    Frames the gate still calls a lone short word are merged back into their
    neighbour afterwards.
    """
    if not words:
        return []

    lines: list[CaptionLine] = []
    for chunk in _chunks(words, style):
        for frame in _balanced_frames(chunk, style):
            lines.append(CaptionLine(words=list(frame)))
    return _merge_orphan_lines(lines)


def generate_caption_words(
    transcript: dict[str, Any],
    edl: EDL,
) -> list[CaptionWord]:
    """Return word-level captions remapped to the cut timeline.

    Args:
        transcript: Transcript dict with word-level timings.
        edl: Current locked EDL.

    Returns:
        List of CaptionWord objects with timings relative to the final cut.
    """
    kept_segments = [(seg.source_start, seg.source_end) for seg in edl.segments]
    remapped = remap_transcript_to_cuts(transcript, kept_segments)
    return [
        CaptionWord(
            text=word.get("word", "").strip(),
            start=word["start"],
            end=word["end"],
            confidence=word.get("probability", 1.0),
        )
        for word in remapped.get("words", [])
        if word.get("word", "").strip()
    ]


def generate_segment_caption_lines(
    words: list[CaptionWord],
    segment_start: float,
    segment_end: float,
    style: CaptionStyle,
) -> list[CaptionLine]:
    """Group cut-relative words that fall inside a single segment into lines."""
    segment_words = [w for w in words if w.end > segment_start and w.start < segment_end]
    # Normalize times to be relative to the segment start so they render correctly
    # against the pre-cut segment input.
    normalized = [
        CaptionWord(
            text=w.text,
            start=max(0.0, w.start - segment_start),
            end=min(segment_end - segment_start, w.end - segment_start),
            confidence=w.confidence,
        )
        for w in segment_words
    ]
    return _group_words_to_lines(normalized, style)
