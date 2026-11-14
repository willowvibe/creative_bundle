"""Tests for the Jev-backed scene shot-type classifier — no network.

The scene planner optionally annotates every scene's ``props`` with a
Jev-classified ``shot_type`` (and ``requires_motion``) using a single batched
classify call. These tests use a fake client and never touch the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from video_editor_core.models.scene_plan import Scene
from video_editor_pipelines.scene_planner import (
    _classify_scenes_with_jev,
    build_scene_plan,
)


class _FakeClassifyClient:
    """Minimal stand-in for ``ClassifyClient`` returning choice/noul answers.

    ``choices`` and ``motion`` map a scene's ``visual_description`` to the
    answer the API would return for that scene. Records the shape of every
    ``classify`` call so tests can assert batching.
    """

    def __init__(
        self,
        *,
        choices: dict[str, str] | None = None,
        motion: dict[str, float] | None = None,
        available: bool = True,
    ) -> None:
        self._choices = choices or {}
        self._motion = motion or {}
        self._available = available
        self.calls: list[dict[str, Any]] = []

    def available(self) -> bool:
        return self._available

    async def classify(
        self,
        state: Any,
        questions: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"state": state, "keys": list(questions)})
        answers: dict[str, Any] = {}
        for key, question in questions.items():
            instructions = question.get("instructions") or {}
            scene = str(instructions.get("scene", ""))
            if question.get("type") == "choice":
                choice = next((v for k, v in self._choices.items() if k in scene), "ai-still")
                answers[key] = {"choice": choice, "confidence": 0.8}
            else:
                noul = next((v for k, v in self._motion.items() if k in scene), 0.1)
                answers[key] = {"noul": noul}
        return {"answers": answers}

    async def close(self) -> None:  # pragma: no cover
        pass


def _scene(
    scene_id: str,
    *,
    visual: str,
    narration: str = "",
    props: dict[str, Any] | None = None,
) -> Scene:
    return Scene(
        id=scene_id,
        type="broll",
        duration_seconds=5.0,
        narration=narration,
        visual_description=visual,
        props=dict(props or {}),
    )


def _router_response(scenes: int) -> str:
    return json.dumps(
        {
            "scenes": [
                {
                    "id": f"scene_{i:03d}",
                    "type": "broll",
                    "duration_seconds": 5.0,
                    "narration": f"Narration {i}.",
                    "visual_description": f"Visual {i}.",
                    "props": {},
                }
                for i in range(1, scenes + 1)
            ]
        }
    )


def _write_script(tmp_path: Path) -> Path:
    script = tmp_path / "script.md"
    script.write_text("Demo script", encoding="utf-8")
    return script


@pytest.mark.asyncio
async def test_jev_annotates_props_in_one_batched_call(tmp_path: Path) -> None:
    """Every scene gets a shot_type and requires_motion from one call."""
    client = _FakeClassifyClient(
        choices={"Visual 1.": "talking-head", "Visual 2.": "data-viz"},
        motion={"Visual 1.": 0.9, "Visual 2.": 0.1},
    )
    scenes = [
        _scene("scene_001", visual="Visual 1."),
        _scene("scene_002", visual="Visual 2."),
    ]

    await _classify_scenes_with_jev(scenes, client=client)

    assert scenes[0].props["shot_type"] == "talking-head"
    assert scenes[0].props["shot_type_confidence"] == pytest.approx(0.8)
    assert scenes[0].props["requires_motion"] is True
    assert scenes[1].props["shot_type"] == "data-viz"
    assert scenes[1].props["requires_motion"] is False
    # One classify call carrying both questions for both scenes.
    assert len(client.calls) == 1
    assert client.calls[0]["keys"] == ["shot0", "motion0", "shot1", "motion1"]


@pytest.mark.asyncio
async def test_jev_unavailable_leaves_props_untouched() -> None:
    """An unavailable classifier makes no call and changes nothing."""
    client = _FakeClassifyClient(available=False)
    scenes = [_scene("scene_001", visual="Visual 1.")]

    await _classify_scenes_with_jev(scenes, client=client)

    assert scenes[0].props == {}
    assert client.calls == []


@pytest.mark.asyncio
async def test_jev_does_not_clobber_existing_requires_motion() -> None:
    """An explicit requires_motion from the planner wins over Jev."""
    client = _FakeClassifyClient(motion={"Visual 1.": 0.0})
    scenes = [_scene("scene_001", visual="Visual 1.", props={"requires_motion": True})]

    await _classify_scenes_with_jev(scenes, client=client)

    assert scenes[0].props["requires_motion"] is True
    assert scenes[0].props["shot_type"] == "ai-still"


@pytest.mark.asyncio
async def test_jev_failure_never_breaks_planning() -> None:
    """A raised classify error is swallowed and props stay unchanged."""

    class _BoomClient(_FakeClassifyClient):
        async def classify(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("boom")

    scenes = [_scene("scene_001", visual="Visual 1.")]

    await _classify_scenes_with_jev(scenes, client=_BoomClient())

    assert scenes[0].props == {}


@pytest.mark.asyncio
async def test_build_scene_plan_annotates_each_scene(tmp_path: Path) -> None:
    """End-to-end: build_scene_plan passes the client through to every scene."""
    script = _write_script(tmp_path)

    class _Router:
        async def chat(self, **kwargs: Any) -> str:
            return _router_response(2)

    client = _FakeClassifyClient(
        choices={"Visual 1.": "stock", "Visual 2.": "text-card"},
        motion={"Visual 1.": 0.7, "Visual 2.": 0.2},
    )

    plan = await build_scene_plan(
        "job-jev",
        script,
        _Router(),  # type: ignore[arg-type]
        classify_client=client,
    )

    assert [s.props["shot_type"] for s in plan.scenes] == ["stock", "text-card"]
    assert plan.scenes[0].props["requires_motion"] is True
    assert plan.scenes[1].props["requires_motion"] is False
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_build_scene_plan_unchanged_without_classifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no client and no key, props stay empty (zero regressions)."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    script = _write_script(tmp_path)

    class _Router:
        async def chat(self, **kwargs: Any) -> str:
            return _router_response(2)

    plan = await build_scene_plan("job-plain", script, _Router())  # type: ignore[arg-type]

    assert [s.props for s in plan.scenes] == [{}, {}]
