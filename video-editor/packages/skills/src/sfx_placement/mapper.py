"""Keyword → SFX prompt mapping and transcript moment extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Map spoken keywords to SFX generation/search prompts.
# Each entry is ``keyword -> (category, prompt)``.  The prompt is passed to a
# generative SFX provider (e.g. ElevenLabs) or used to search a stock library.
KEYWORD_MAP: dict[str, tuple[str, str]] = {
    "click": ("ui", "short user interface click, clean and modern"),
    "tap": ("ui", "finger tap on glass screen"),
    "button": ("ui", "soft button click sound"),
    "notification": ("ui", "short notification ding"),
    "alert": ("ui", "alert chime"),
    "ding": ("ui", "pleasant notification bell ding"),
    "whoosh": ("transition", "fast whoosh swoosh transition"),
    "swipe": ("transition", "quick swipe whoosh"),
    "transition": ("transition", "smooth whoosh transition"),
    "sizzle": ("cooking", "food sizzling in hot pan"),
    "cook": ("cooking", "gentle cooking sizzle"),
    "fry": ("cooking", "frying sizzle sound"),
    "boil": ("cooking", "water boiling bubbles"),
    "crowd": ("atmosphere", "small crowd ambience murmur"),
    "applause": ("atmosphere", "light applause clapping"),
    "cheer": ("atmosphere", "small crowd cheering"),
    "laugh": ("atmosphere", "small group laugh"),
    "explosion": ("impact", "small distant explosion"),
    "bang": ("impact", "sharp impact bang"),
    "crash": ("impact", "short crash sound"),
    "typing": ("tech", "mechanical keyboard typing"),
    "keyboard": ("tech", "keyboard key presses"),
    "door": ("household", "door opening and closing"),
    "knock": ("household", "door knock"),
    "car": ("vehicle", "car engine idle"),
    "engine": ("vehicle", "vehicle engine rumble"),
    "horn": ("vehicle", "car horn beep"),
    "rain": ("nature", "gentle rain ambience"),
    "thunder": ("nature", "distant thunder rumble"),
    "storm": ("nature", "storm wind and rain"),
    "wind": ("nature", "soft wind ambience"),
}


@dataclass(frozen=True)
class SfxMoment:
    """A single detected SFX placement opportunity."""

    start: float
    end: float
    text: str
    keyword: str
    category: str
    prompt: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "keyword": self.keyword,
            "category": self.category,
            "prompt": self.prompt,
        }


def _normalize_word(word: str) -> str:
    """Lowercase and strip punctuation from a transcript word."""
    return re.sub(r"[^a-z0-9]", "", word.lower())


def extract_moments_from_words(
    words: list[dict[str, Any]],
    keyword_map: dict[str, tuple[str, str]] | None = None,
) -> list[SfxMoment]:
    """Find SFX moments from a word-level transcript.

    Args:
        words: List of dicts with keys ``word``, ``start``, ``end``.
        keyword_map: Optional override of the default keyword → SFX map.

    Returns:
        Detected SFX moments in chronological order.
    """
    mapping = keyword_map or KEYWORD_MAP
    moments: list[SfxMoment] = []
    seen: set[tuple[float, str]] = set()

    for entry in words:
        raw_word = entry.get("word", "")
        normalized = _normalize_word(raw_word)
        if normalized not in mapping:
            continue

        start = float(entry.get("start", 0.0))
        end = float(entry.get("end", start + 0.5))
        key = (round(start, 2), normalized)
        if key in seen:
            continue
        seen.add(key)

        category, prompt = mapping[normalized]
        moments.append(
            SfxMoment(
                start=start,
                end=end,
                text=raw_word,
                keyword=normalized,
                category=category,
                prompt=prompt,
            )
        )

    return moments


def extract_moments_from_segments(
    segments: list[dict[str, Any]],
    keyword_map: dict[str, tuple[str, str]] | None = None,
) -> list[SfxMoment]:
    """Find SFX moments from segment-level transcript text.

    Args:
        segments: List of dicts with keys ``text``, ``start``, ``end``.
        keyword_map: Optional override of the default keyword → SFX map.

    Returns:
        One moment per matching segment keyword, timed at segment start.
    """
    mapping = keyword_map or KEYWORD_MAP
    moments: list[SfxMoment] = []
    seen: set[tuple[float, str]] = set()

    for segment in segments:
        text = segment.get("text", "")
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start + 1.0))
        words = re.findall(r"\b\w+\b", text.lower())

        for word in words:
            if word not in mapping:
                continue
            key = (round(start, 2), word)
            if key in seen:
                continue
            seen.add(key)

            category, prompt = mapping[word]
            moments.append(
                SfxMoment(
                    start=start,
                    end=end,
                    text=text.strip(),
                    keyword=word,
                    category=category,
                    prompt=prompt,
                )
            )
            break  # one moment per segment

    return moments
