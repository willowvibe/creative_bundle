"""Scene plan models for script-to-video."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from video_editor_core.models.asset import AssetResult

_SCENE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_ALLOWED_SCENE_TYPES = frozenset(
    {
        "opening",
        "narration",
        "narrative",
        "broll",
        "closing",
        "transition",
        "music_visual",
        "story",
        "typographic",
        "map",
        "composite",
        # A stock clip (or its AI fallback still) under the narration.
        "footage",
    }
)


class Scene(BaseModel):
    id: str
    type: str
    duration_seconds: float
    narration: str = ""
    visual_description: str = ""
    asset_query: str = ""
    props: dict[str, Any] = Field(default_factory=dict)
    assets: list[AssetResult] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("scene id must be non-empty")
        if not _SCENE_ID_RE.match(value):
            raise ValueError(
                f"scene id must contain only letters, digits, underscores, or hyphens: {value!r}"
            )
        return value

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        value = value.strip()
        if value not in _ALLOWED_SCENE_TYPES:
            raise ValueError(f"scene type must be one of {_ALLOWED_SCENE_TYPES}, got {value!r}")
        return value

    @field_validator("duration_seconds")
    @classmethod
    def _validate_duration(cls, value: float) -> float:
        if value <= 0:
            raise ValueError(f"scene duration_seconds must be > 0, got {value}")
        if value > 300:
            raise ValueError(f"scene duration_seconds must be <= 300, got {value}")
        return value


class ScenePlan(BaseModel):
    """Script-derived scene plan."""

    job_id: str
    script: str
    scenes: list[Scene] = []
    aspect_ratio: str = "16:9"
    music_mood: str = ""
    music_path: str = ""
    design: str = ""
    narration_path: str = ""
    #: Measured length of the narration file. A plan's final scene can hold a
    #: tail past the last spoken word; the audio cue is shortened to this so
    #: its slot matches the media instead of being trimmed by the runtime.
    narration_duration: float = 0.0

    @model_validator(mode="after")
    def _validate_scenes(self) -> ScenePlan:
        if not self.scenes:
            raise ValueError("scene plan must contain at least one scene")
        seen: set[str] = set()
        for scene in self.scenes:
            if scene.id in seen:
                raise ValueError(f"duplicate scene id: {scene.id}")
            seen.add(scene.id)
        return self
