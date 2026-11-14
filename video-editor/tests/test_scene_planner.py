"""Tests for the scene-planner prompt builder and JSON pipeline.

The planner decomposes a markdown script into a ScenePlan via an LLM.
These tests cover the prompt builder, the JSON parse / validation
pipeline, and the disk write — all without making real LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_pipelines.scene_planner import (
    _FALLBACK_SCENE_DURATION_SECONDS,
    _build_prompt,
    build_scene_plan,
    write_scene_plan,
)


def test_build_prompt_includes_aspect_and_mood() -> None:
    """The aspect ratio and music mood appear in the rendered prompt."""
    prompt = _build_prompt("hello world", aspect_ratio="9:16", music_mood="upbeat")
    assert "9:16" in prompt
    assert "upbeat" in prompt
    assert "hello world" in prompt
    # The schema hint is in the prompt.
    assert "scenes" in prompt
    assert "duration_seconds" in prompt


def test_build_prompt_includes_motion_guidance() -> None:
    """The planner prompt tells the LLM about always-on Ken Burns and motion scenes."""
    prompt = _build_prompt("hello world", aspect_ratio="16:9", music_mood="")
    assert "props.requires_motion" in prompt
    assert "props.motion" in prompt
    assert "HyperFrames" in prompt
    assert "Ken Burns" in prompt


def test_build_prompt_defaults_music_mood() -> None:
    """An empty music_mood renders as the literal string 'auto'."""
    prompt = _build_prompt("script", aspect_ratio="16:9", music_mood="")
    assert "auto" in prompt


def test_editorial_mode_is_opt_in() -> None:
    """The generic prompt stays generic; only the explainer asks for plates."""
    generic = _build_prompt("script", aspect_ratio="9:16", music_mood="")
    assert "Editorial mode" not in generic
    assert "| map" not in generic

    editorial = _build_prompt("script", aspect_ratio="9:16", music_mood="", editorial=True)
    assert "Editorial mode" in editorial
    assert "typographic | map | composite" in editorial
    assert "props.image_b_prompt" in editorial
    assert "bundled geography" in editorial
    # The prompt names each typographic layout's required props, so the model
    # cannot hand the renderer a sequence with two steps.
    assert "sequence: steps, a list of 3-5" in editorial
    assert "bar_compare: bars, a list of 2-4" in editorial
    # A map can name several places and connect them; the model must be told
    # the prop names the renderer parses.
    assert "markers: 1-6 {text, lon, lat, cue?}" in editorial
    assert "route: at least two [lon, lat] points" in editorial
    assert "zooms: 1-3 {bbox, cue?}" in editorial


def test_write_scene_plan_roundtrips(tmp_path: Path) -> None:
    """A written scene plan can be reloaded as a ScenePlan."""
    plan = ScenePlan(
        job_id="sp-test",
        script="hello",
        aspect_ratio="16:9",
        music_mood="calm",
        scenes=[
            Scene(
                id="scene_001",
                type="narration",
                duration_seconds=5.0,
                narration="Hello.",
                visual_description="A greeting.",
            )
        ],
    )
    out = tmp_path / "scene_plan.json"
    write_scene_plan(plan, out)
    assert out.exists()
    loaded = ScenePlan(**json.loads(out.read_text(encoding="utf-8")))
    assert loaded.job_id == "sp-test"
    assert loaded.aspect_ratio == "16:9"
    assert loaded.music_mood == "calm"


@pytest.mark.asyncio
async def test_build_scene_plan_parses_llm_response(
    tmp_path: Path,
) -> None:
    """A valid LLM JSON response is parsed into a ScenePlan."""
    script = tmp_path / "script.md"
    script.write_text("Demo script", encoding="utf-8")

    response_json = json.dumps(
        {
            "aspect_ratio": "16:9",
            "music_mood": "upbeat",
            "scenes": [
                {
                    "id": "scene_001",
                    "type": "opening",
                    "duration_seconds": 4.0,
                    "narration": "Welcome.",
                    "visual_description": "A bold title card.",
                    "asset_query": "",
                    "props": {},
                },
                {
                    "id": "scene_002",
                    "type": "closing",
                    "duration_seconds": 3.0,
                    "narration": "Thanks for watching.",
                    "visual_description": "A call-to-action.",
                    "asset_query": "",
                    "props": {},
                },
            ],
        }
    )

    class FakeRouter:
        async def chat(self, **kwargs: Any) -> str:
            return response_json

    plan = await build_scene_plan(
        "job-sp-1",
        script,
        FakeRouter(),  # type: ignore[arg-type]
    )

    assert plan.job_id == "job-sp-1"
    assert plan.script == "Demo script"
    assert plan.aspect_ratio == "16:9"
    assert plan.music_mood == "upbeat"
    assert len(plan.scenes) == 2
    assert plan.scenes[0].id == "scene_001"
    assert plan.scenes[0].type == "opening"
    assert plan.scenes[0].duration_seconds == 4.0


@pytest.mark.asyncio
async def test_build_scene_plan_falls_back_on_invalid_json(
    tmp_path: Path,
) -> None:
    """Invalid JSON from the LLM is retried and then falls back to a
    deterministic single-scene plan so the pipeline can continue."""
    script = tmp_path / "script.md"
    script.write_text("Demo", encoding="utf-8")

    class FakeRouter:
        async def chat(self, **kwargs: Any) -> str:
            return "{not valid json"

    plan = await build_scene_plan("job-bad", script, FakeRouter())  # type: ignore[arg-type]
    assert plan.job_id == "job-bad"
    assert len(plan.scenes) == 1
    assert plan.scenes[0].id == "scene_001"


@pytest.mark.asyncio
async def test_build_scene_plan_falls_back_on_empty_scenes(
    tmp_path: Path,
) -> None:
    """A scene plan with no scenes falls back to a deterministic plan."""
    script = tmp_path / "script.md"
    script.write_text("Demo", encoding="utf-8")

    class FakeRouter:
        async def chat(self, **kwargs: Any) -> str:
            return json.dumps({"scenes": []})

    plan = await build_scene_plan("job-empty", script, FakeRouter())  # type: ignore[arg-type]
    assert len(plan.scenes) == 1
    assert plan.scenes[0].type == "narration"


@pytest.mark.asyncio
async def test_build_scene_plan_raises_when_fallback_disabled(
    tmp_path: Path,
) -> None:
    """With fallback disabled, repeated LLM failures still raise."""
    script = tmp_path / "script.md"
    script.write_text("Demo", encoding="utf-8")

    class FakeRouter:
        async def chat(self, **kwargs: Any) -> str:
            return json.dumps({"scenes": []})

    with pytest.raises(RuntimeError, match="failed to produce a valid scene plan"):
        await build_scene_plan(
            "job-no-fb",
            script,
            FakeRouter(),  # type: ignore[arg-type]
            fallback_on_error=False,
        )


@pytest.mark.asyncio
async def test_build_scene_plan_retries_then_succeeds(
    tmp_path: Path,
) -> None:
    """The planner retries on a bad LLM response and succeeds once a valid
    response is returned."""
    script = tmp_path / "script.md"
    script.write_text("Demo script", encoding="utf-8")

    valid_json = json.dumps(
        {
            "scenes": [
                {
                    "id": "scene_001",
                    "type": "opening",
                    "duration_seconds": 4.0,
                    "narration": "Welcome.",
                    "visual_description": "A title card.",
                }
            ]
        }
    )

    responses = iter(["not json", valid_json])

    class FakeRouter:
        async def chat(self, **kwargs: Any) -> str:
            return next(responses)

    plan = await build_scene_plan("job-retry", script, FakeRouter())  # type: ignore[arg-type]
    assert len(plan.scenes) == 1
    assert plan.scenes[0].id == "scene_001"


@pytest.mark.asyncio
async def test_build_scene_plan_falls_back_on_invalid_scene_data(
    tmp_path: Path,
) -> None:
    """Validation errors from the new ScenePlan validators (bad type, negative
    duration, duplicate ids) trigger fallback, not a crash."""
    script = tmp_path / "script.md"
    script.write_text("Demo", encoding="utf-8")

    response_json = json.dumps(
        {
            "scenes": [
                {
                    "id": "scene_001",
                    "type": "explosion",  # invalid type
                    "duration_seconds": -5.0,  # invalid duration
                    "narration": "Boom.",
                    "visual_description": "Big explosion.",
                }
            ]
        }
    )

    class FakeRouter:
        async def chat(self, **kwargs: Any) -> str:
            return response_json

    plan = await build_scene_plan("job-validate", script, FakeRouter())  # type: ignore[arg-type]
    assert len(plan.scenes) == 1
    assert plan.scenes[0].type == "narration"
    assert plan.scenes[0].duration_seconds == _FALLBACK_SCENE_DURATION_SECONDS


@pytest.mark.asyncio
async def test_build_scene_plan_rejects_duplicate_scene_ids_when_fallback_disabled(
    tmp_path: Path,
) -> None:
    """When fallback is disabled, validation errors surface as RuntimeError."""
    script = tmp_path / "script.md"
    script.write_text("Demo", encoding="utf-8")

    response_json = json.dumps(
        {
            "scenes": [
                {
                    "id": "scene_001",
                    "type": "opening",
                    "duration_seconds": 2.0,
                    "narration": "One.",
                    "visual_description": "A.",
                },
                {
                    "id": "scene_001",
                    "type": "closing",
                    "duration_seconds": 2.0,
                    "narration": "Two.",
                    "visual_description": "B.",
                },
            ]
        }
    )

    class FakeRouter:
        async def chat(self, **kwargs: Any) -> str:
            return response_json

    with pytest.raises(RuntimeError, match="failed to produce a valid scene plan"):
        await build_scene_plan(
            "job-dup",
            script,
            FakeRouter(),  # type: ignore[arg-type]
            fallback_on_error=False,
        )
