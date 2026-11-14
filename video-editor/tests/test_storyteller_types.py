"""Tests that PipelineKind and Scene type allow the new storyteller values."""

from __future__ import annotations

from typing import get_args

from video_editor_core.models.scene_plan import _ALLOWED_SCENE_TYPES
from video_editor_core.templates.base import PipelineKind


def test_pipeline_kind_includes_storyteller() -> None:
    assert "storyteller" in get_args(PipelineKind)


def test_scene_types_includes_story() -> None:
    assert "story" in _ALLOWED_SCENE_TYPES
