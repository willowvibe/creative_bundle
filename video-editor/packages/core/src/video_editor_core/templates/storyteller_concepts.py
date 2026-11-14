"""Starter concept markdown for the storyteller templates."""

from __future__ import annotations

from pathlib import Path


def build_storyteller_concept(path: Path) -> str:
    """Return a starter persona-tagged markdown script for new users."""
    return (
        "---\n"
        "language: en\n"
        "music_mood: cinematic\n"
        'title: "My Story"\n'
        "cast:\n"
        "  narrator:\n"
        "    voice_id: elevenlabs:Brian\n"
        "    language: en\n"
        "    volume: 1.0\n"
        "  king:\n"
        "    voice_id: elevenlabs:Lily\n"
        "    language: en\n"
        "    volume: 0.95\n"
        "---\n\n"
        f"# {path.stem.replace('_', ' ').title()}\n\n"
        "## Speaker: narrator\n"
        "> sfx: gentle ambient atmosphere\n"
        "Open the story here. The opening line sets the scene.\n\n"
        "## Speaker: king\n"
        "Their first line of dialogue.\n"
    )
