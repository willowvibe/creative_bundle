"""Utilities for parsing and timing Devanagari lyrics for FableCut captions.

The parser itself lives in :mod:`video_editor_core.lyrics` so the musical-video
caption burn-in and the FableCut timeline cannot drift. This module keeps the
bridge's public names by re-exporting it and adds the one-per-scene mapping.
"""

from __future__ import annotations

from pathlib import Path

from video_editor_core.lyrics import FOLDABLE_HEADERS as FOLDABLE_HEADERS
from video_editor_core.lyrics import parse_lyrics as parse_lyrics


def map_lyrics_to_scenes(lyrics: list[str], scene_durations: list[float]) -> list[str]:
    """Distribute caption lines evenly across the given scene durations.

    Returns one lyric string per scene. If there are more scenes than lyrics,
    remaining scenes keep the last lyric so the video doesn't end with empty
    captions. If there are fewer scenes than lyrics, lyrics are joined so that
    every scene has at least one caption.
    """
    if not lyrics:
        return [""] * len(scene_durations)

    n_scenes = len(scene_durations)
    n_lyrics = len(lyrics)

    out: list[str]
    if n_scenes >= n_lyrics:
        out = lyrics.copy()
        # Pad remaining scenes with the last lyric (or empty if none).
        last = lyrics[-1] if lyrics else ""
        while len(out) < n_scenes:
            out.append(last)
        return out

    # More lyrics than scenes: group consecutive lyrics into scenes.
    out = []
    base = n_lyrics // n_scenes
    extra = n_lyrics % n_scenes
    cursor = 0
    for i in range(n_scenes):
        take = base + (1 if i < extra else 0)
        chunk = lyrics[cursor : cursor + take]
        out.append("\n".join(chunk) if chunk else "")
        cursor += take
    return out


def load_lyrics_for_plan(lyrics_path: Path, scene_durations: list[float]) -> list[str]:
    """Load a lyrics file and map its lines one-per-scene."""
    text = lyrics_path.read_text(encoding="utf-8")
    lyrics = parse_lyrics(text)
    return map_lyrics_to_scenes(lyrics, scene_durations)
