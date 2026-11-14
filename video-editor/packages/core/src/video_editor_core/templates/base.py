"""Base types for the template layer.

A ``Template`` is a declarative recipe that maps a user request (a set of
inputs) to a concrete pipeline configuration. Templates do not perform the
render themselves; they tell the caller which preset, aspect ratio, providers,
and CLI path to use.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

PipelineKind = Literal[
    "v2_skills",
    "v1_raw",
    "script_to_video",
    "music_video",
    "storyteller",
    "raw_to_video",
    "screen_to_video",
    "podcast_to_video",
    "shorts_from_long",
    "explainer_stills",
]


@dataclass
class InputSpec:
    """What a template expects from the user."""

    name: str
    description: str
    required: bool = True
    example: str = ""


@dataclass
class TemplateResult:
    """Resolved configuration after the router picks a template."""

    template_name: str
    pipeline_kind: PipelineKind
    preset: str
    aspect_ratio: str
    providers: dict[str, str]
    music_mood: str = ""
    notes: list[str] = field(default_factory=list)
    concept: str | None = None


@dataclass
class Template:
    """A supported video-creation recipe."""

    name: str
    description: str
    inputs: list[InputSpec]
    pipeline_kind: PipelineKind
    default_preset: str
    default_aspect_ratio: str
    default_providers: dict[str, str] = field(default_factory=dict)
    default_music_mood: str = ""
    scene_type_hints: list[str] = field(default_factory=list)
    build_concept: Callable[[Path], str] | None = None
    caveats: list[str] = field(default_factory=list)
    quality_tier_pair: tuple[str, str] | None = None

    def resolve(
        self,
        source: Path | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> TemplateResult:
        """Return the resolved configuration for this template.

        ``overrides`` can carry ``preset``, ``aspect_ratio``, or provider
        overrides. The router passes these through so a user can tweak a
        template without abandoning it.
        """
        overrides = overrides or {}
        providers = dict(self.default_providers)
        providers.update(overrides.get("providers", {}))
        return TemplateResult(
            template_name=self.name,
            pipeline_kind=self.pipeline_kind,
            preset=overrides.get("preset", self.default_preset),
            aspect_ratio=overrides.get("aspect_ratio", self.default_aspect_ratio),
            providers=providers,
            music_mood=overrides.get("music_mood", self.default_music_mood),
            notes=[
                f"pipeline={self.pipeline_kind}",
                f"preset={overrides.get('preset', self.default_preset)}",
            ],
            concept=None,
        )
