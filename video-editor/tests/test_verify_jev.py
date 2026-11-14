"""Tests for the Jev (TypeSafe) verify judge path — no network, no ffmpeg."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from verify.models import DurationRange, RequirementsSpec
from verify.verify import VerifySkill
from video_editor_state.job import JobContext


class _FakeClassifyClient:
    def __init__(
        self, *, overall: str = "PASS", item_result: str = "PASS", available: bool = True
    ) -> None:
        self._overall = overall
        self._item = item_result
        self._available = available
        self.calls: list[dict[str, Any]] = []

    def available(self) -> bool:
        return self._available

    async def classify(self, state: Any, questions: dict[str, Any], **kw: Any) -> dict[str, Any]:
        self.calls.append(questions)
        answers: dict[str, Any] = {}
        for key in questions:
            choice = self._overall if key == "overall" else self._item
            answers[key] = {"type": "choice", "choice": choice, "confidence": 0.9}
        return {"model": "jev-test", "answers": answers, "usage": {}}

    async def close(self) -> None:  # pragma: no cover - injected client isn't closed
        pass


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="jev-verify",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


def _skill(ctx: JobContext, client: _FakeClassifyClient) -> VerifySkill:
    return VerifySkill(ctx, classify_client=client)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_jev_judge_batches_all_items_into_one_call(ctx: JobContext, tmp_path: Path) -> None:
    client = _FakeClassifyClient()
    skill = _skill(ctx, client)
    req = RequirementsSpec(job_id="jev-verify", judge_checklist=["a", "b", "c"])

    check = await skill._run_jev_judge(tmp_path / "final.mp4", req, [])

    assert check is not None
    assert check.requirement == "llm_judge"
    assert check.result == "PASS"
    assert len(client.calls) == 1
    assert set(client.calls[0]) == {"item0", "item1", "item2", "overall"}
    assert len(check.actual["item_checks"]) == 3


@pytest.mark.asyncio
async def test_jev_judge_item_fail_forces_overall_fail(ctx: JobContext, tmp_path: Path) -> None:
    client = _FakeClassifyClient(overall="PASS", item_result="FAIL")
    skill = _skill(ctx, client)
    req = RequirementsSpec(job_id="jev-verify", judge_checklist=["bad item"])

    check = await skill._run_jev_judge(tmp_path / "final.mp4", req, [])

    assert check is not None
    assert check.result == "FAIL"


@pytest.mark.asyncio
async def test_jev_judge_returns_none_when_unavailable(ctx: JobContext, tmp_path: Path) -> None:
    client = _FakeClassifyClient(available=False)
    skill = _skill(ctx, client)
    req = RequirementsSpec(job_id="jev-verify", judge_checklist=["a"])

    assert await skill._run_jev_judge(tmp_path / "final.mp4", req, []) is None


@pytest.mark.asyncio
async def test_llm_judge_no_checklist_still_passes(ctx: JobContext) -> None:
    skill = _skill(ctx, _FakeClassifyClient())
    req = RequirementsSpec(
        job_id="jev-verify", target_duration_range=DurationRange(min_seconds=0.0, max_seconds=10.0)
    )
    check = await skill._run_llm_judge(Path("x.mp4"), req, [])
    assert check.result == "PASS"
    assert "no judge checklist" in check.reason
