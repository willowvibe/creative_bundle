"""Tests for the provider resolvers used by the SFX bank."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from sfx_placement.resolvers import SFX_PROMPTS, SFX_SEARCH_QUERIES, manager_resolver
from video_editor_asset_ingestion.models import AssetResult, AssetType, BatchResult


def _result(metadata: dict | None = None) -> AssetResult:
    return AssetResult(
        asset_id="sfx_hit",
        asset_type=AssetType.SFX,
        local_path="/tmp/hit.mp3",
        metadata=metadata or {},
    )


def test_the_chain_tries_providers_in_order_and_keeps_provenance() -> None:
    calls: list[str] = []

    async def resolve_partial(requests):
        provider = requests[0].provider
        calls.append(provider)
        if provider == "freesound_sfx":
            return BatchResult(failures=[("sfx_hit", "404")])
        return BatchResult(
            results=[
                _result(
                    {
                        "title": "Hit",
                        "creator": "Someone",
                        "source_url": "https://freesound.org/s/1",
                        "license": "CC0 1.0",
                        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                    }
                )
            ]
        )

    manager = AsyncMock()
    manager.resolve_partial = resolve_partial
    resolver = manager_resolver(manager, ["freesound_sfx", "openverse_sfx"], job_id="job")
    resolved = asyncio.run(resolver("hit"))

    assert calls == ["freesound_sfx", "openverse_sfx"]
    assert resolved is not None
    assert resolved.provider == "openverse_sfx"
    assert resolved.licence.title == "Hit"
    assert resolved.licence.creator == "Someone"
    assert resolved.licence.license == "CC0 1.0"
    assert resolved.path == Path("/tmp/hit.mp3")


def test_the_request_carries_the_cue_prompt_and_type() -> None:
    seen = []

    async def resolve_partial(requests):
        seen.append(requests[0])
        return BatchResult(results=[_result()])

    manager = AsyncMock()
    manager.resolve_partial = resolve_partial
    resolver = manager_resolver(manager, ["freesound_sfx"], job_id="job", duration_seconds=1.5)
    asyncio.run(resolver("card_flip"))

    request = seen[0]
    assert request.asset_type is AssetType.SFX
    assert request.provider == "freesound_sfx"
    assert request.prompt == SFX_SEARCH_QUERIES["card_flip"]
    assert request.params["duration_seconds"] == 1.5
    assert request.params["page"] == 1
    assert request.output_ext == "mp3"
    assert request.params["cue_sound"] == "card_flip"
    assert request.asset_id.startswith("sfx_card_flip_")


def test_a_generated_provider_gets_the_generation_prompt() -> None:
    seen = []

    async def resolve_partial(requests):
        seen.append(requests[0])
        return BatchResult(results=[_result()])

    manager = AsyncMock()
    manager.resolve_partial = resolve_partial
    resolver = manager_resolver(manager, ["elevenlabs_sfx"], job_id="job")
    asyncio.run(resolver("hit"))
    assert seen[0].prompt == SFX_PROMPTS["hit"]


def test_repeated_cues_advance_the_search_page() -> None:
    pages: list[int] = []

    async def resolve_partial(requests):
        pages.append(requests[0].params["page"])
        return BatchResult(results=[_result()])

    manager = AsyncMock()
    manager.resolve_partial = resolve_partial
    resolver = manager_resolver(manager, ["freesound_sfx"], job_id="job")
    asyncio.run(resolver("whoosh"))
    asyncio.run(resolver("whoosh"))
    asyncio.run(resolver("whoosh"))
    assert pages == [1, 2, 3]


def test_a_provider_exception_falls_through_to_the_next() -> None:
    async def resolve_partial(requests):
        if requests[0].provider == "freesound_sfx":
            raise RuntimeError("network down")
        return BatchResult(results=[_result({"license": "generated"})])

    manager = AsyncMock()
    manager.resolve_partial = resolve_partial
    resolver = manager_resolver(manager, ["freesound_sfx", "elevenlabs_sfx"], job_id="job")
    resolved = asyncio.run(resolver("hit"))
    assert resolved is not None
    assert resolved.provider == "elevenlabs_sfx"
    assert resolved.licence.license == "generated"


def test_no_provider_finding_a_sound_returns_none() -> None:
    manager = AsyncMock()
    manager.resolve_partial = AsyncMock(return_value=BatchResult(failures=[("x", "404")]))
    resolver = manager_resolver(manager, ["freesound_sfx", "openverse_sfx"], job_id="job")
    assert asyncio.run(resolver("whoosh")) is None


def test_earshot_gets_a_wav_and_the_cue_sound() -> None:
    seen = []

    async def resolve_partial(requests):
        seen.append(requests[0])
        return BatchResult(results=[_result()])

    manager = AsyncMock()
    manager.resolve_partial = resolve_partial
    resolver = manager_resolver(manager, ["earshot_sfx"], job_id="job")
    asyncio.run(resolver("latch"))
    assert seen[0].output_ext == "wav"
    assert seen[0].params["cue_sound"] == "latch"
    assert "duration_seconds" not in seen[0].params
