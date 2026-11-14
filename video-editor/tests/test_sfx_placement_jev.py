"""Tests for the Jev-backed SFX placement filter — no network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sfx_placement.mapper import extract_moments_from_words
from sfx_placement.sfx_placement import SfxPlacementSkill
from video_editor_state.job import JobContext


class _FakeClassifyClient:
    """Minimal stand-in for ``ClassifyClient`` returning noul probabilities."""

    def __init__(self, keep: set[str] | None = None, available: bool = True) -> None:
        self._keep = keep or set()
        self._available = available
        self.calls = 0

    def available(self) -> bool:
        return self._available

    async def classify(
        self, state: Any, questions: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        answers: dict[str, Any] = {}
        for key, question in questions.items():
            context = str((question.get("instructions") or {}).get("context", ""))
            hit = any(token in context for token in self._keep)
            answers[key] = {"noul": 0.95 if hit else 0.05}
        return {"answers": answers}

    async def close(self) -> None:  # pragma: no cover
        pass


def _skill(tmp_path: Path, client: Any) -> SfxPlacementSkill:
    ctx = JobContext(job_id="sfx-job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    return SfxPlacementSkill(ctx, classify_client=client)


def _words() -> list[dict[str, Any]]:
    return [
        {"word": "whoosh", "start": 1.0, "end": 1.3},
        {"word": "explosion", "start": 2.0, "end": 2.4},
    ]


@pytest.mark.asyncio
async def test_jev_filter_keeps_only_selected(tmp_path: Path) -> None:
    skill = _skill(tmp_path, _FakeClassifyClient(keep={"whoosh"}))
    moments = extract_moments_from_words(_words())
    kept = await skill._filter_moments_with_jev(moments)

    assert [m.keyword for m in kept] == ["whoosh"]


@pytest.mark.asyncio
async def test_jev_filter_unavailable_passes_through(tmp_path: Path) -> None:
    client = _FakeClassifyClient(available=False)
    skill = _skill(tmp_path, client)
    moments = extract_moments_from_words(_words())
    kept = await skill._filter_moments_with_jev(moments)

    assert len(kept) == 2
    assert client.calls == 0


@pytest.mark.asyncio
async def test_jev_filter_caps_placements(tmp_path: Path) -> None:
    skill = _skill(tmp_path, _FakeClassifyClient(keep={"whoosh", "explosion"}))
    skill.jev_max_placements = 1
    moments = extract_moments_from_words(_words())
    kept = await skill._filter_moments_with_jev(moments)

    assert len(kept) == 1


@pytest.mark.asyncio
async def test_jev_filter_empty_input(tmp_path: Path) -> None:
    skill = _skill(tmp_path, _FakeClassifyClient())
    assert await skill._filter_moments_with_jev([]) == []
