"""Lay the written narration onto the words Whisper actually heard.

Synthetic narration is transcribed into the *phonetic* spelling we sent
("Sah-joo", not "Saju"), so captions written from the transcript would show
the wrong text. The known display narration is aligned onto the recognised
stream character by character (``difflib.SequenceMatcher``), and every display
word inherits the timing at its position — correct captions that still land on
the voice.

Scene boundaries then follow the words, never a written duration:

* a scene starts ``LEAD_IN_SECONDS`` before its first word, never earlier than
  the previous scene's end;
* it ends ``INTERIOR_TAIL_SECONDS`` after its last word, or
  ``FINAL_TAIL_SECONDS`` on the final scene;
* scenes are contiguous, so an interior scene is held up to the next scene's
  lead-in (and its tail is clamped there when the words run close together);
  a single scene never runs shorter than ``MIN_SCENE_SECONDS``, and a scene the
  recogniser dropped falls back to a word-count estimate.

The alignment and tail-spreading are the saju build's algorithm
(``videos/2026-10-22/saju-zodiac-animal/build/transcribe.py``), which also
produced the #268 lesson: run Whisper with ``vad_filter=False`` and an
``initial_prompt``, or it trims a synthetic tail and collapses the last scene.

Every function here is a pure function of its inputs: no clock, no filesystem,
no randomness, so the same transcript always yields the same plan.
"""

from __future__ import annotations

import difflib
import math
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

LEAD_IN_SECONDS = 0.12
INTERIOR_TAIL_SECONDS = 0.25
FINAL_TAIL_SECONDS = 1.2
MIN_SCENE_SECONDS = 1.2
#: ``narration_mode: per_scene``: the pause joined between two scene clips, and
#: how far before its clip a scene's picture cuts in.
SCENE_GAP_SECONDS = 0.45
PER_SCENE_LEAD_SECONDS = 0.12
#: A cut never lands closer than this after the last spoken word (#345): a
#: shorter tail clips the decay of the word, which reads as cut off.
MIN_CUT_TAIL_SECONDS = 0.15
#: Under this pause the cut goes in the silence, not at the next lead-in.
SHORT_PAUSE_SECONDS = 0.5
#: The next scene's first word keeps at least this much picture before it.
MIN_NEXT_LEAD_SECONDS = 0.04

#: Seconds a dropped scene's narration is assumed to take, per word. Only used
#: when the recogniser heard none of that scene's words, so there is nothing
#: measured to go on.
SECONDS_PER_ESTIMATED_WORD = 0.38
MIN_ESTIMATED_SCENE_SECONDS = 1.5


@dataclass(frozen=True)
class SceneTiming:
    """Where one scene sits in the narration track, and the words inside it."""

    id: str
    start: float
    end: float
    words: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


def _norm(text: str) -> str:
    """Letters, combining marks and digits of any script, lowercased.

    Punctuation cannot break matching, and Telugu (or any non-Latin) narration
    keeps its characters: an ASCII-only filter turned every Telugu word into
    nothing, so no word got a time (chai-stall-pilot-1). Marks are kept because
    Indic vowel signs are marks, not letters.
    """
    return "".join(
        char for char in text.lower() if unicodedata.category(char)[0] in {"L", "M", "N"}
    )


def _words_with_spans(text: str) -> list[tuple[str, int, int]]:
    """(word, start, end) offsets over ``_norm`` of the whole text."""
    spans: list[tuple[str, int, int]] = []
    cursor = 0
    prefix = ""
    for raw in text.split():
        width = len(_norm(raw))
        if width == 0:
            # A token with no letters ("—", "…") has nothing to time, but the
            # captions must still show it (#339): attach it to the previous
            # word, or to the next one when it opens the text.
            if spans:
                word, start, end = spans[-1]
                spans[-1] = (f"{word} {raw}", start, end)
            else:
                prefix = f"{prefix}{raw} "
            continue
        spans.append((prefix + raw, cursor, cursor + width))
        prefix = ""
        cursor += width
    return spans


def _matching_blocks(expected: str, recognised: str) -> list[tuple[int, int, int]]:
    matcher = difflib.SequenceMatcher(a=expected, b=recognised, autojunk=False)
    return [(block.a, block.b, block.size) for block in matcher.get_matching_blocks() if block.size]


def _recognised_spans(
    recognised: Sequence[Mapping[str, Any]],
) -> list[tuple[int, int, float, float]]:
    spans: list[tuple[int, int, float, float]] = []
    cursor = 0
    for word in recognised:
        width = len(_norm(str(word.get("word", ""))))
        if width == 0:
            continue
        spans.append((cursor, cursor + width, float(word["start"]), float(word["end"])))
        cursor += width
    return spans


def _offset_to_time(
    offset: int,
    blocks: Sequence[tuple[int, int, int]],
    spans: Sequence[tuple[int, int, float, float]],
) -> tuple[float, float] | None:
    """Map a character offset in the expected stream onto a recognised span.

    ``None`` means the offset falls past every matched block: the recogniser
    never heard those words, so there is no time to inherit. Returning a time
    anyway would paint a caption the voice never said.
    """
    if not spans:
        return None
    recorded: int | None = None
    for a, b, size in blocks:
        if a <= offset < a + size:
            recorded = b + (offset - a)
            break
        if offset < a:
            recorded = b
            break
    if recorded is None:
        return None
    for low, high, start, end in spans:
        if low <= recorded < high:
            if low < recorded and high > low:
                # The offset sits partway inside a merged STT token: enter it
                # proportionally by character position so words sharing that
                # token keep script order instead of all snapping to its start.
                start = start + (end - start) * (recorded - low) / (high - low)
            return start, end
    return spans[-1][2], spans[-1][3]


def _time_for_gap(
    expected_words: Sequence[tuple[str, int, int]],
    gap_word_indices: Sequence[int],
    window_start: float,
    window_end: float,
) -> dict[int, tuple[float, float]]:
    """Spread script words across a recognised time window, weighted by length."""
    weights = [len(_norm(expected_words[idx][0])) for idx in gap_word_indices]
    total = sum(weights)
    if total == 0:
        return {}
    available = window_end - window_start
    cursor = window_start
    timings: dict[int, tuple[float, float]] = {}
    for idx, weight in zip(gap_word_indices, weights, strict=True):
        span = available * weight / total
        timings[idx] = (round(cursor, 3), round(cursor + span, 3))
        cursor += span
    return timings


def align_words(
    narrations: Sequence[str],
    recognised: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Time the display narration using the recognised word stream."""
    expected_text = " ".join(narrations)
    expected_norm = _norm(expected_text)
    recognised_norm = "".join(_norm(str(word.get("word", ""))) for word in recognised)
    blocks = _matching_blocks(expected_norm, recognised_norm)
    spans = _recognised_spans(recognised)

    expected_words = list(_words_with_spans(expected_text))

    # Leading and interior expected gaps whose corresponding recognised gap
    # contains real characters: give the script words inside them timings
    # weighted by their normalised length, instead of collapsing them to zero.
    gap_boundaries: list[tuple[int, int, int, int]] = []
    if blocks:
        first_a, first_b, _first_size = blocks[0]
        gap_boundaries.append((0, first_a, 0, first_b))
        for (prev_a, prev_b, prev_size), (next_a, next_b, _next_size) in zip(
            blocks, blocks[1:], strict=False
        ):
            gap_boundaries.append(
                (
                    prev_a + prev_size,
                    next_a,
                    prev_b + prev_size,
                    next_b,
                )
            )
    else:
        gap_boundaries.append((0, len(expected_norm), 0, len(recognised_norm)))

    override_timings: dict[int, tuple[float, float]] = {}
    for exp_start, exp_end, rec_start, rec_end in gap_boundaries:
        if rec_start >= rec_end:
            continue
        gap_indices = [
            idx
            for idx, (_word, word_start, word_end) in enumerate(expected_words)
            if word_start >= exp_start and word_end <= exp_end
        ]
        if not gap_indices:
            continue
        window_start: float | None = None
        window_end: float | None = None
        for low, high, start, end in spans:
            if low < rec_end and high > rec_start:
                span_start = start
                if low < rec_start and high > low:
                    # The span straddles the gap: STT merged the previous
                    # matched word into this token, so the gap starts partway
                    # inside it. Enter the span proportionally at rec_start
                    # instead of its whole start, or the previous word is
                    # squeezed to zero length.
                    span_start = start + (end - start) * (rec_start - low) / (high - low)
                if window_start is None or span_start < window_start:
                    window_start = span_start
                if window_end is None or end > window_end:
                    window_end = end
        if window_start is None or window_end is None:
            continue
        # A recognised span can straddle the gap and also carry the next matched
        # script word (STT merges tokens, e.g. "300kilometers"). Clamp the window
        # so the spread words stay between the previous matched word's start and
        # the next one's, keeping the emitted starts in script order.
        for block_a, _block_b, block_size in blocks:
            if block_a + block_size != exp_start:
                continue
            previous = _offset_to_time(block_a, blocks, spans)
            if previous is not None:
                window_start = max(window_start, previous[0])
            break
        following = _offset_to_time(exp_end, blocks, spans)
        if following is not None:
            window_end = min(window_end, following[0])
        window_end = max(window_start, window_end)
        override_timings.update(
            _time_for_gap(expected_words, gap_indices, window_start, window_end)
        )

    timed: list[dict[str, Any]] = []
    for idx, (display_word, start_char, _end_char) in enumerate(expected_words):
        if idx in override_timings:
            start, end = override_timings[idx]
        else:
            mapped = _offset_to_time(start_char, blocks, spans)
            if mapped is None:
                continue
            start, end = mapped
        timed.append(
            {
                "word": display_word,
                "start_char": start_char,
                "start": round(start, 3),
                "end": round(end, 3),
            }
        )
    # A word is current until the next one starts: that is what karaoke steps
    # through, and it removes the silent gaps between words. ``spoken_end``
    # keeps where the voice actually stopped, for placing cuts (#345).
    for entry in timed:
        entry["spoken_end"] = entry["end"]
    for position, entry in enumerate(timed[:-1]):
        entry["end"] = max(entry["start"], timed[position + 1]["start"])
    return timed


def spread_pinned_tail(words: list[dict[str, Any]], audio_end: float, min_gap: float = 0.6) -> int:
    """Give unmapped trailing words real times, weighted by word length.

    ``_offset_to_time`` falls back to the last recognised span, so a sentence
    the recogniser dropped leaves its words pinned to one instant. If that
    pinned run still leaves more than ``min_gap`` seconds unaccounted for,
    spread it across the remainder. Returns how many words were moved.
    """
    if len(words) < 2:
        return 0
    index = len(words) - 1
    while index > 0 and words[index]["start"] <= words[index - 1]["start"]:
        index -= 1
    pinned = words[index:]
    start = float(words[index]["start"])
    gap = audio_end - start
    if len(pinned) < 2 or gap < min_gap:
        return 0

    weights = [len(str(word["word"])) + 1 for word in pinned]
    total = sum(weights)
    cursor = start
    for word, weight in zip(pinned, weights, strict=True):
        span = gap * weight / total
        word["start"] = round(cursor, 3)
        word["end"] = round(cursor + span, 3)
        cursor += span
    for position, word in enumerate(words[:-1]):
        word["end"] = max(word["start"], words[position + 1]["start"])
    return len(pinned)


def _split_by_offset(
    narrations: Sequence[str], words: Sequence[Mapping[str, Any]]
) -> list[list[dict[str, Any]]]:
    """Group display words by the narration they came from."""
    groups: list[list[dict[str, Any]]] = [[] for _ in narrations]
    bounds: list[tuple[int, int]] = []
    cursor = 0
    for narration in narrations:
        width = len(_norm(narration))
        bounds.append((cursor, cursor + width))
        cursor += width
    for word in words:
        start_char = int(word.get("start_char", -1))
        for index, (low, high) in enumerate(bounds):
            if low <= start_char < high:
                groups[index].append(dict(word))
                break
    return groups


def group_words_by_narration(
    narrations: Sequence[str], words: Sequence[Mapping[str, Any]]
) -> list[list[dict[str, Any]]]:
    """Group aligned display words by the narration they came from.

    Public because the short pipeline splits a pack's lines the same way the
    explainer splits its scenes, and duplicating the offset walk would be a
    second source of truth for where one line ends and the next begins.
    """
    return _split_by_offset(narrations, words)


def _next_spoken_start(groups: Sequence[Sequence[Mapping[str, Any]]], index: int) -> float | None:
    """Start of the first later scene the recogniser heard something in."""
    for group in groups[index + 1 :]:
        if group:
            return float(group[0]["start"])
    return None


def derive_scene_timings(
    narrations: Sequence[str],
    words: list[dict[str, Any]],
    *,
    ids: Sequence[str] | None = None,
) -> list[SceneTiming]:
    """Split ``words`` across scenes and turn the split into durations."""
    scene_ids = (
        list(ids)
        if ids is not None
        else [f"scene_{number}" for number in range(1, len(narrations) + 1)]
    )
    if len(scene_ids) != len(narrations):
        raise ValueError("ids must have one entry per narration")
    groups = _split_by_offset(narrations, words)
    last_index = len(narrations) - 1
    timings: list[SceneTiming] = []
    cursor = 0.0
    for index, narration in enumerate(narrations):
        group = groups[index]
        if index == 0:
            # Nothing precedes the first scene, so its lead-in cannot start
            # before zero.
            start = 0.0
            if group:
                start = max(0.0, float(group[0]["start"]) - LEAD_IN_SECONDS)
        else:
            start = cursor
        if group:
            tail = FINAL_TAIL_SECONDS if index == last_index else INTERIOR_TAIL_SECONDS
            end = float(group[-1]["end"]) + tail
        else:
            # The recogniser dropped this scene: estimate from the word count.
            estimated = max(
                MIN_ESTIMATED_SCENE_SECONDS,
                SECONDS_PER_ESTIMATED_WORD * len(narration.split()),
            )
            end = start + estimated
        end = max(end, start + MIN_SCENE_SECONDS)

        following_start = _next_spoken_start(groups, index)
        if following_start is not None:
            boundary = max(start + MIN_SCENE_SECONDS, following_start - LEAD_IN_SECONDS)
            if index + 1 < len(narrations) and groups[index + 1]:
                # Contiguous: hold this scene up to the next scene's lead-in,
                # and clamp the interior tail so it never runs past it. After a
                # short pause the lead-in would clip the last word, so the cut
                # moves into the silence, keeping a minimum tail (#345).
                end = boundary
                spoken_end = group[-1].get("spoken_end") if group else None
                if spoken_end is not None:
                    spoken_end = float(spoken_end)
                    pause = following_start - spoken_end
                    if 0.0 <= pause < SHORT_PAUSE_SECONDS:
                        middle = (spoken_end + following_start) / 2.0
                        cut = max(middle, spoken_end + MIN_CUT_TAIL_SECONDS)
                        cut = min(cut, following_start - MIN_NEXT_LEAD_SECONDS)
                        # Never before the last word ends: with almost no pause
                        # the next scene's lead-in shrinks instead.
                        cut = max(cut, min(spoken_end, following_start))
                        end = max(start + MIN_SCENE_SECONDS, cut)
            else:
                # Only empty scenes sit in between: keep the estimate, but
                # never overrun the next scene's words.
                end = min(end, boundary)
        end = round(max(end, start + MIN_SCENE_SECONDS), 3)
        start = round(start, 3)
        timings.append(SceneTiming(id=scene_ids[index], start=start, end=end, words=list(group)))
        cursor = end
    return timings


@dataclass(frozen=True)
class CutCheck:
    """A QA result shaped like ``explainer_qa.QaCheck`` (kept local to avoid a cycle)."""

    name: str
    ok: bool
    detail: str


def sentence_cuts_check(timings: Sequence[SceneTiming]) -> CutCheck:
    """No cut lands on a word: each scene's last word ends ``MIN_CUT_TAIL`` before it.

    Uses ``spoken_end`` (where the voice stopped) when the words carry it. A cut
    inside a word interval, or within the minimum tail after it, fails.
    """
    tight: list[str] = []
    touching: list[str] = []
    for position, timing in enumerate(timings[:-1]):
        if not timing.words:
            continue
        last = timing.words[-1]
        spoken_end = float(last.get("spoken_end", last["end"]))
        tail = timing.end - spoken_end
        if tail >= MIN_CUT_TAIL_SECONDS - 1e-6:
            continue
        following = timings[position + 1].words
        next_start = float(following[0]["start"]) if following else math.inf
        if next_start - spoken_end < MIN_CUT_TAIL_SECONDS + MIN_NEXT_LEAD_SECONDS:
            # The pause is too short to hold the tail and a lead-in (or the
            # recogniser shows none: a spoken run-on). The latest cut before the
            # next word is the best available, so report it rather than fail.
            touching.append(timing.id)
            continue
        tight.append(f"{timing.id} cut {tail:.2f}s after {str(last['word'])!r}")
    if tight:
        return CutCheck("sentence_cuts", False, "; ".join(tight))
    if touching:
        return CutCheck(
            "sentence_cuts",
            True,
            f"cuts clear; pause too short for a full tail after {', '.join(touching)}",
        )
    return CutCheck(
        "sentence_cuts",
        True,
        f"every cut leaves >= {MIN_CUT_TAIL_SECONDS:.2f}s after the last word",
    )


def per_scene_timings(
    narrations: Sequence[str],
    durations: Sequence[float],
    *,
    gap: float = SCENE_GAP_SECONDS,
    lead: float = PER_SCENE_LEAD_SECONDS,
    ids: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], list[SceneTiming]]:
    """Words and scene timings for clips joined with ``gap`` seconds between them.

    Each clip's length is measured, so scene boundaries are exact: a scene cuts
    in ``lead`` seconds before its clip and runs to the next scene's cut; the
    last one keeps ``FINAL_TAIL_SECONDS`` after its clip. Words share their
    clip by normalised character count, which is what a karaoke caption needs.
    """
    if len(narrations) != len(durations):
        raise ValueError(f"{len(narrations)} narrations for {len(durations)} clip lengths")
    offsets: list[float] = []
    cursor = 0.0
    for length in durations:
        offsets.append(cursor)
        cursor += float(length) + gap

    words: list[dict[str, Any]] = []
    grouped: list[list[dict[str, Any]]] = []
    for text, offset, length in zip(narrations, offsets, durations, strict=True):
        spans = _words_with_spans(text) or [(text, 0, 1)]
        weights = [max(1, end - start) for _word, start, end in spans]
        total = float(sum(weights))
        at = offset
        scene_words = []
        for (word, _start, _end), weight in zip(spans, weights, strict=True):
            end = at + float(length) * weight / total
            entry = {"word": word, "start": round(at, 3), "end": round(end, 3)}
            entry["spoken_end"] = entry["end"]
            scene_words.append(entry)
            at = end
        scene_words[-1]["end"] = scene_words[-1]["spoken_end"] = round(offset + float(length), 3)
        grouped.append(scene_words)
        words.extend(scene_words)

    timings: list[SceneTiming] = []
    starts = [0.0] + [max(0.0, round(offset - lead, 3)) for offset in offsets[1:]]
    final_end = round(offsets[-1] + float(durations[-1]) + FINAL_TAIL_SECONDS, 3)
    for index, scene_words in enumerate(grouped):
        end = starts[index + 1] if index + 1 < len(starts) else final_end
        scene_id = ids[index] if ids is not None else f"scene_{index + 1}"
        timings.append(SceneTiming(id=scene_id, start=starts[index], end=end, words=scene_words))
    return words, timings
