"""The footage pass: stock clips, judged, then AI stills for what is left."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_core.models.asset import BatchResult
from video_editor_pipelines.explainer_footage import (
    FootageVerdict,
    parse_verdict,
    resolve_with_footage,
)
from video_editor_pipelines.explainer_pipeline import explainer_job_requests
from video_editor_pipelines.explainer_plan import build_explainer_plan
from video_editor_pipelines.explainer_script import parse_explainer_script

SCRIPT = """---
title: Chai stall
aspect_ratio: "16:9"
---

## Scene 1 — Morning
visual: footage
query: tea stall india
image: a roadside chai stall at dawn
narration: Five in the morning, the first boil of milk.

## Scene 2 — Queue
visual: footage
query: people drinking tea
image: office workers drinking chai
narration: By nine the queue is ten deep.
"""

CHAIN = ["pexels", "pixabay"]


def _requests() -> list[AssetRequest]:
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job")
    return explainer_job_requests(plan, script, "job", music_provider="")


class FakeManager:
    """Serves requests by rule; records every batch it was asked for."""

    def __init__(self, serve, calls: list[list[str]], tmp: Path) -> None:  # type: ignore[no-untyped-def]
        self.serve = serve
        self.calls = calls
        self.tmp = tmp

    def _result(self, request: AssetRequest, provider: str) -> AssetResult:
        path = self.tmp / f"{request.asset_id}.{request.output_ext}"
        path.write_bytes(b"x")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=request.asset_type,
            scene_id=request.scene_id,
            local_path=str(path),
            metadata={"provider": provider},
        )

    async def resolve_partial(self, requests: list[AssetRequest]) -> BatchResult:
        self.calls.append([r.asset_id for r in requests])
        batch = BatchResult()
        for request in requests:
            provider = self.serve(request)
            if provider is None:
                batch.failures.append((request.asset_id, "no usable results"))
            else:
                batch.results.append(self._result(request, provider))
        return batch


def _factory(serve, calls, tmp):  # type: ignore[no-untyped-def]
    return lambda: FakeManager(serve, calls, tmp)


def _serve_all(request: AssetRequest) -> str | None:
    if request.provider == "stock":
        return "pexels"
    return request.provider or "auto"


async def _accept(clip: Path, query: str) -> FootageVerdict:
    return FootageVerdict(ok=True, reason="shows it")


@pytest.mark.asyncio
async def test_accepted_clips_need_no_fallback_stills(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    results, report = await resolve_with_footage(
        _factory(_serve_all, calls, tmp_path), _requests(), judge=_accept, stock_chain=CHAIN
    )

    assert len(calls) == 1
    assert {r.asset_id for r in results if r.asset_type is AssetType.VIDEO} == {
        "scene_1_footage",
        "scene_2_footage",
    }
    assert not any(r.asset_id.endswith("_fallback_image") for r in results)
    assert [row["source"] for row in report] == ["stock", "stock"]


@pytest.mark.asyncio
async def test_a_rejected_clip_tries_the_next_library_before_falling_back(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    async def judge(clip: Path, query: str) -> FootageVerdict:
        # Pexels' clip for scene 1 shows the wrong thing; Pixabay's is right.
        return FootageVerdict(ok="scene_1_footage." not in clip.name, reason="checked")

    results, report = await resolve_with_footage(
        _factory(_serve_all, calls, tmp_path), _requests(), judge=judge, stock_chain=CHAIN
    )

    retry = calls[1]
    assert retry == ["scene_1_footage_pixabay"]
    videos = {r.scene_id: r.asset_id for r in results if r.asset_type is AssetType.VIDEO}
    assert videos == {"scene_1": "scene_1_footage_pixabay", "scene_2": "scene_2_footage"}
    assert report[0]["provider"] == "pixabay"
    assert report[0]["rejected"] == [{"provider": "pexels", "reason": "checked"}]


@pytest.mark.asyncio
async def test_no_acceptable_clip_falls_back_to_the_ai_still(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def serve(request: AssetRequest) -> str | None:
        if request.scene_id == "scene_2" and request.asset_type is AssetType.VIDEO:
            return None  # every library came back empty
        return _serve_all(request)

    results, report = await resolve_with_footage(
        _factory(serve, calls, tmp_path), _requests(), judge=_accept, stock_chain=CHAIN
    )

    assert calls[-1] == ["scene_2_fallback_image"]
    assert "scene_2_fallback_image" in {r.asset_id for r in results}
    assert report[1]["source"] == "ai_fallback"


@pytest.mark.asyncio
async def test_a_non_footage_failure_still_fails_the_job(tmp_path: Path) -> None:
    def serve(request: AssetRequest) -> str | None:
        return None if request.asset_id == "job_narration" else _serve_all(request)

    with pytest.raises(RuntimeError, match="job_narration"):
        await resolve_with_footage(
            _factory(serve, [], tmp_path), _requests(), judge=_accept, stock_chain=CHAIN
        )


@pytest.mark.asyncio
async def test_an_unjudgeable_clip_is_kept_and_reported(tmp_path: Path) -> None:
    async def broken(clip: Path, query: str) -> FootageVerdict:
        raise OSError("vision model unreachable")

    results, report = await resolve_with_footage(
        _factory(_serve_all, [], tmp_path), _requests(), judge=broken, stock_chain=CHAIN
    )

    assert [row["source"] for row in report] == ["stock", "stock"]
    assert all(row["judged"] is False for row in report)


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ('{"shows_query": true, "brand_visible": false, "reason": "tea stall"}', True),
        ('{"shows_query": false, "brand_visible": false, "reason": "a beach"}', False),
        ('{"shows_query": true, "brand_visible": true, "reason": "Coca-Cola sign"}', False),
        ('```json\n{"shows_query": true, "brand_visible": false, "reason": "ok"}\n```', True),
    ],
)
def test_verdict_rejects_off_topic_clips_and_visible_brands(text: str, ok: bool) -> None:
    assert parse_verdict(text).ok is ok


def test_an_unreadable_verdict_is_a_rejection() -> None:
    verdict = parse_verdict("I think it is fine")
    assert verdict.ok is False
