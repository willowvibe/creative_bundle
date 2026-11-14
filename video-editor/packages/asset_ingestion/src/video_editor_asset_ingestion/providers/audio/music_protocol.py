"""Abstract MusicProvider protocol and MusicResult data class.

The storyteller planner depends on this interface so it can swap between
PoYo's music endpoint and ElevenLabs' ``/v1/music/generate`` without
touching the planner code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MusicResult:
    """Result of a single music-generation call."""

    provider: str
    model: str
    path: Path
    duration_seconds: float


class MusicProvider(ABC):
    """Interface for AI music providers."""

    provider_name: str = "music_provider"

    @abstractmethod
    async def generate_music(
        self,
        prompt: str,
        duration_seconds: float,
        output_path: Path,
    ) -> MusicResult:
        """Synthesise a music track matching ``prompt`` and write it to ``output_path``."""
