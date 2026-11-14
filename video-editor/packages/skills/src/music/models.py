"""Data models for the Music skill."""

from __future__ import annotations

from pathlib import Path

from graphics.models import Brand
from pydantic import BaseModel, Field


class MusicStyle(BaseModel):
    """Configuration for background music mixing and speech ducking."""

    music_path: str = ""
    music_gain_db: float = -14.0
    speech_gain_db: float = 0.0
    # Sidechain threshold as a linear amplitude in [0.000976563, 1].
    # This is the FFmpeg `sidechaincompress threshold` value; 0.1 corresponds
    # to roughly -20 dBFS.
    duck_threshold: float = 0.1
    duck_ratio: float = 15.0
    attack_ms: int = 50
    release_ms: int = 300
    knee_db: float = 3.0
    makeup: float = 1.0
    loop: bool = True
    fade_in: float = 0.0
    fade_out: float = 0.0

    @classmethod
    def from_brand(cls, brand: Brand, music_path: str = "") -> MusicStyle:
        """Build a MusicStyle from a Brand profile.

        Reads ``defaults.music_duck_db`` (used as ``music_gain_db``).
        The ``music_path`` is not part of the brand — it's a job
        artifact and must be passed separately.
        """
        gain_default = cls.model_fields["music_gain_db"].default
        return cls(
            music_path=music_path,
            music_gain_db=float(brand.defaults.get("music_duck_db", gain_default)),
        )


class MusicManifest(BaseModel):
    """Record of music mix applied to a job."""

    job_id: str
    style: MusicStyle = Field(default_factory=MusicStyle)
    input_video: str = ""
    music_path: str = ""
    output_video: str = ""
    duration: float = 0.0

    @property
    def music_file(self) -> Path | None:
        return Path(self.music_path) if self.music_path else None
