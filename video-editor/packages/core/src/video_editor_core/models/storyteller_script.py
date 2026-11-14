"""Pydantic models for the storyteller-template input script.

A storyteller script is a markdown file with a YAML frontmatter block
declaring the global ``language``, ``music_mood``, ``title``, and ``cast``,
followed by ``## Speaker: <name>`` section headings, each holding
narration text and optional ``> visual:``, ``> sfx:``, ``> language:``,
and ``> motion:`` blockquote directives.

The planner converts one ``StorytellerScript`` to one ``ScenePlan``;
this module only carries the parsed-script shape.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class CastMember(BaseModel):
    """One declared character in the cast map."""

    voice_id: str
    language: str
    # TODO(N2 unused-field): ``volume`` is parsed and validated but is not
    # applied to the per-speaker audio mix yet. The storyteller→FableCut
    # exporter copies audio clips without scaling amplitude by this value;
    # wire it into the mix (or the export's per-clip gain) when per-speaker
    # ducking is needed.
    volume: float = 1.0

    @field_validator("volume")
    @classmethod
    def _validate_volume(cls, value: float) -> float:
        if not 0.0 <= value <= 2.0:
            raise ValueError(f"cast member volume must be in [0.0, 2.0], got {value}")
        return value


class StorytellerScene(BaseModel):
    """A single speaker's segment in the script."""

    id: str
    speaker: str
    text: str
    language: str
    voice_id: str
    visual_override: str | None = None
    sfx_prompts: list[str] = Field(default_factory=list)
    motion_override: bool | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        v = value.strip()
        if not v:
            raise ValueError("scene id must be non-empty")
        return v


class StorytellerScript(BaseModel):
    """The full parsed script."""

    language: str = "en"
    music_mood: str = "cinematic"
    title: str = ""
    cast: dict[str, CastMember]
    scenes: list[StorytellerScene] = Field(default_factory=list)
