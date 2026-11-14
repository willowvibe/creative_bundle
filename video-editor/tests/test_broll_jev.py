"""Tests for the Jev (TypeSafe) B-roll moment planner — no network."""

from __future__ import annotations

from typing import Any

import pytest
from broll.planner import _extract_with_jev, extract_moments_async


class _FakeClassifyClient:
    def __init__(self, strong: set[str] | None = None, available: bool = True) -> None:
        self._strong = strong or set()
        self._available = available
        self.batches: list[int] = []

    def available(self) -> bool:
        return self._available

    async def classify_batch(
        self, items: list[Any], build: Any, *, batch_size: int = 40, budget_chars: int = 24_000
    ) -> list[dict[str, Any]]:
        self.batches.append(len(items))
        return [{"type": "score", "score": 4.0 if str(t) in self._strong else 0.0} for t in items]

    async def close(self) -> None:  # pragma: no cover
        pass


def _transcript() -> dict[str, Any]:
    return {
        "segments": [
            {"start": 0.0, "end": 4.0, "text": "Welcome back everyone."},
            {"start": 4.0, "end": 9.0, "text": "I flew to Tokyo and toured the factory floor."},
            {"start": 9.0, "end": 12.0, "text": "So that is basically it."},
        ]
    }


@pytest.mark.asyncio
async def test_jev_picks_strong_segment_only():
    fake = _FakeClassifyClient(strong={"I flew to Tokyo and toured the factory floor."})
    moments = await _extract_with_jev(
        _transcript(), client=fake, min_duration=2.0, max_duration=8.0
    )

    assert moments is not None
    assert len(moments) == 1
    m = moments[0]
    assert m.start == pytest.approx(4.0)
    assert m.query  # deterministic _clean_query produced something
    assert fake.batches == [3]  # one batched call


@pytest.mark.asyncio
async def test_jev_returns_none_when_unavailable():
    fake = _FakeClassifyClient(available=False)
    assert (
        await _extract_with_jev(_transcript(), client=fake, min_duration=2.0, max_duration=8.0)
        is None
    )


@pytest.mark.asyncio
async def test_extract_async_prefers_jev_over_router():
    fake = _FakeClassifyClient(strong={"I flew to Tokyo and toured the factory floor."})

    class _Router:
        async def generate(self, *a: Any, **k: Any) -> str:  # pragma: no cover
            raise AssertionError("router must not be used when Jev is available")

    moments = await extract_moments_async(
        _transcript(),
        router=_Router(),
        classify_client=fake,  # type: ignore[arg-type]
        min_duration=2.0,
        max_duration=8.0,
    )
    assert len(moments) == 1


@pytest.mark.asyncio
async def test_extract_async_falls_back_to_heuristic_when_no_jev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No client, no key configured → classify unavailable → deterministic path.
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    moments = await extract_moments_async(_transcript(), min_duration=1.0, max_duration=6.0)
    assert isinstance(moments, list)
