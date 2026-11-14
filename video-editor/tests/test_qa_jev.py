"""Tests for the Jev content-consistency checks in QASkill — no network/ffmpeg.

The new checks are advisory: they never raise, add nothing when the classifier
is unavailable, and batch every question into one ``classify`` call.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from qa.models import QACheck
from qa.qa import QASkill
from video_editor_state.job import JobContext

_ALL_CHECKS = {"jev_content_complete", "jev_duration_matches", "jev_render_state"}


class _FakeClassifyClient:
    """Minimal stand-in for ``ClassifyClient`` returning canned answers."""

    def __init__(
        self,
        answers: dict[str, Any] | None = None,
        *,
        available: bool = True,
        raises: bool = False,
    ) -> None:
        self._answers = answers or {}
        self._available = available
        self._raises = raises
        self.calls = 0
        self.last_questions: dict[str, Any] = {}

    def available(self) -> bool:
        return self._available

    async def classify(
        self,
        state: Any,
        questions: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        self.last_questions = questions
        if self._raises:
            raise RuntimeError("classifier boom")
        return {"answers": self._answers}

    async def close(self) -> None:  # pragma: no cover
        pass


def _ctx(tmp_path: Path) -> JobContext:
    return JobContext(job_id="qa-jev", work_root=tmp_path / "work", output_root=tmp_path / "out")


def _info(
    duration: float = 12.0,
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    has_audio: bool = True,
) -> Any:
    return SimpleNamespace(
        duration=duration, width=width, height=height, fps=fps, has_audio=has_audio
    )


def _checks() -> list[QACheck]:
    return [
        QACheck(name="file_present", passed=True, message="ok"),
        QACheck(name="valid_duration", passed=True, message="ok"),
    ]


def _write_plan(ctx: JobContext, scenes: list[dict[str, Any]]) -> None:
    plan_path = ctx.artifact("plan", "scene_plan.json")
    plan_path.write_text(
        json.dumps({"job_id": "qa-jev", "script": "s", "scenes": scenes}),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_jev_checks_batch_all_questions_in_one_call(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write_plan(
        ctx,
        [
            {
                "id": "s1",
                "type": "narration",
                "duration_seconds": 6.0,
                "narration": "Intro",
                "visual_description": "logo",
            },
            {
                "id": "s2",
                "type": "broll",
                "duration_seconds": 6.0,
                "narration": "Body",
                "visual_description": "city",
            },
        ],
    )
    client = _FakeClassifyClient(
        {
            "content_complete": {"noul": 0.9},
            "duration_matches": {"noul": 0.8},
            "render_state": {"choice": "complete", "confidence": 0.7},
        }
    )
    skill = QASkill(ctx, classify_client=client)

    results = await skill._jev_content_checks(_info(), _checks())

    # One batched call carrying every question.
    assert client.calls == 1
    assert set(client.last_questions) == {
        "content_complete",
        "duration_matches",
        "render_state",
    }
    assert {c.name for c in results} == _ALL_CHECKS
    assert all(c.passed for c in results)


@pytest.mark.asyncio
async def test_jev_checks_unavailable_adds_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    client = _FakeClassifyClient(available=False)
    skill = QASkill(ctx, classify_client=client)

    results = await skill._jev_content_checks(_info(), _checks())

    assert results == []
    assert client.calls == 0


@pytest.mark.asyncio
async def test_jev_checks_classifier_error_adds_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    client = _FakeClassifyClient(raises=True)
    skill = QASkill(ctx, classify_client=client)

    # Never raises — advisory only.
    results = await skill._jev_content_checks(_info(), _checks())

    assert results == []
    assert client.calls == 1


@pytest.mark.asyncio
async def test_jev_duration_check_only_with_plan(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    client = _FakeClassifyClient(
        {
            "content_complete": {"noul": 0.9},
            "render_state": {"choice": "complete"},
        }
    )
    skill = QASkill(ctx, classify_client=client)

    results = await skill._jev_content_checks(_info(), _checks())

    assert "duration_matches" not in client.last_questions
    names = {c.name for c in results}
    assert names == {"jev_content_complete", "jev_render_state"}
    assert "jev_duration_matches" not in names


@pytest.mark.asyncio
async def test_jev_low_probability_flags_advisory_failure(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    client = _FakeClassifyClient(
        {
            "content_complete": {"noul": 0.1},
            "render_state": {"choice": "placeholder"},
        }
    )
    skill = QASkill(ctx, classify_client=client)

    results = await skill._jev_content_checks(_info(), _checks())

    by_name = {c.name: c for c in results}
    assert by_name["jev_content_complete"].passed is False
    assert by_name["jev_content_complete"].value == 0.1
    assert by_name["jev_render_state"].passed is False


def test_jev_context_has_facts_checks_and_plan(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write_plan(
        ctx,
        [
            {
                "id": "s1",
                "type": "narration",
                "duration_seconds": 5.0,
                "narration": "A short intro line",
                "visual_description": "brand logo",
            },
            {
                "id": "s2",
                "type": "broll",
                "duration_seconds": 7.0,
                "narration": "The main body",
                "visual_description": "city skyline",
            },
        ],
    )
    skill = QASkill(ctx, classify_client=_FakeClassifyClient())

    context = skill._build_jev_context(_info(), _checks())

    assert "12.00s" in context
    assert "1920x1080" in context
    assert "Has audio: True" in context
    assert "file_present=pass" in context
    assert "2 scene(s), planned total 12.00s" in context
    assert "A short intro line" in context
    assert "city skyline" in context
    assert len(context) <= 4000


def test_jev_context_truncates_large_plan(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write_plan(
        ctx,
        [
            {
                "id": "s1",
                "type": "narration",
                "duration_seconds": 5.0,
                "narration": "x" * 5000,
                "visual_description": "y" * 5000,
            }
        ],
    )
    skill = QASkill(ctx, classify_client=_FakeClassifyClient())

    context = skill._build_jev_context(_info(), _checks())

    # The whole context stays under the hard cap and per-scene snippets are
    # clamped, so huge fields never leak into the classifier state verbatim.
    assert len(context) <= 4000
    assert "x" * 200 not in context
    assert "y" * 200 not in context
    assert "\u2026" in context


def test_jev_context_tolerates_missing_plan(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    skill = QASkill(ctx, classify_client=_FakeClassifyClient())

    context = skill._build_jev_context(_info(), _checks())

    assert "Scene plan" not in context
