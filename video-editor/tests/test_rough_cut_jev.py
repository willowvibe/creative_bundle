"""Tests for the Jev semantic low-value pass in rough_cut — no network/ffmpeg."""

from __future__ import annotations

from typing import Any

from rough_cut.rough_cut import _build_kept_segments, _detect_semantic_low_value


class _FakeClassifyClient:
    def __init__(
        self,
        low_value: set[str] | None = None,
        available: bool = True,
        scores: dict[str, float] | None = None,
    ) -> None:
        self._low = low_value or set()
        self._scores = scores or {}
        self._available = available
        self.batches: list[int] = []

    def available(self) -> bool:
        return self._available

    async def classify_batch(
        self, items: list[Any], build: Any, *, batch_size: int = 40, budget_chars: int = 24_000
    ) -> list[dict[str, Any]]:
        self.batches.append(len(items))
        return [
            {
                "type": "noul",
                "noul": self._scores.get(str(t), 0.9 if str(t) in self._low else 0.1),
            }
            for t in items
        ]

    async def close(self) -> None:  # pragma: no cover
        pass


def _transcript() -> dict[str, Any]:
    return {
        "segments": [
            {"start": 0.0, "end": 3.0, "text": "Welcome back to the channel."},
            {"start": 3.0, "end": 7.0, "text": "um so anyway, where was I, sorry about that"},
            {"start": 7.0, "end": 12.0, "text": "Here is the exact system I used to grow."},
        ]
    }


def test_flags_only_low_value_segments():
    fake = _FakeClassifyClient(low_value={"um so anyway, where was I, sorry about that"})
    flagged = _detect_semantic_low_value(_transcript(), client=fake)
    assert flagged == [(3.0, 7.0)]


def test_batches_all_segments_in_one_call():
    fake = _FakeClassifyClient()
    _detect_semantic_low_value(_transcript(), client=fake)
    assert fake.batches == [3]  # one call, three segments


def test_unavailable_client_flags_nothing():
    fake = _FakeClassifyClient(available=False)
    assert _detect_semantic_low_value(_transcript(), client=fake) == []


def test_short_segments_are_skipped():
    fake = _FakeClassifyClient(low_value={"tiny"})
    transcript = {"segments": [{"start": 0.0, "end": 0.3, "text": "tiny"}]}
    assert _detect_semantic_low_value(transcript, client=fake) == []
    assert fake.batches == []  # nothing eligible → no call


def test_build_kept_segments_includes_semantic_removals():
    kept = _build_kept_segments(
        duration=20.0,
        silence_regions=[],
        filler_regions=[],
        pause_regions=[],
        semantic_regions=[(5.0, 10.0)],
    )
    assert (5.0, 10.0) not in [(a, b) for a, b, _ in kept]
    assert (0.0, 5.0, "keep speech") in kept
    assert (10.0, 20.0, "keep speech") in kept


# --- #256: the semantic pass must not gut the take -----------------------


def _demo_transcript() -> dict[str, Any]:
    """The shape that exposed #256: 71 % of the take flagged for removal."""
    return {
        "segments": [
            {"start": 0.0, "end": 11.4, "text": "a very long rambling intro"},
            {"start": 11.4, "end": 12.0, "text": "and then"},
            {"start": 12.0, "end": 13.3, "text": "false start here"},
            {"start": 13.3, "end": 16.4, "text": "the actual point"},
        ]
    }


def test_skips_a_region_longer_than_the_region_ceiling():
    """A single 11.4 s 'low value' region is distrusted, not deleted."""
    fake = _FakeClassifyClient(
        scores={
            "a very long rambling intro": 0.95,
            "false start here": 0.8,
        }
    )
    flagged = _detect_semantic_low_value(_demo_transcript(), client=fake)
    assert flagged == [(12.0, 13.3)]
    assert 11.4 - 0.0 not in [b - a for a, b in flagged]


def test_total_removal_is_capped_to_a_fraction_of_the_take():
    """Even several short regions cannot remove more than the cap."""
    fake = _FakeClassifyClient(scores={f"region {i}": 0.9 for i in range(5)})
    transcript = {
        "segments": [
            {"start": float(i * 3), "end": float(i * 3 + 2), "text": f"region {i}"}
            for i in range(5)
        ]
    }
    # 5 × 2 s flagged over a 14 s take = 71 %, the #256 scenario in miniature.
    flagged = _detect_semantic_low_value(
        transcript, client=fake, max_removal_fraction=0.35, max_region_seconds=60.0
    )
    removed = sum(b - a for a, b in flagged)
    assert removed <= 0.35 * 14.0 + 1e-9
    assert 0 < len(flagged) < 5


def test_highest_confidence_regions_win_when_the_budget_is_tight():
    fake = _FakeClassifyClient(scores={"meh": 0.7, "obvious": 0.95})
    transcript = {
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "meh"},
            {"start": 2.0, "end": 5.0, "text": "keep this"},
            {"start": 5.0, "end": 7.0, "text": "obvious"},
            {"start": 7.0, "end": 10.0, "text": "keep that"},
        ]
    }
    flagged = _detect_semantic_low_value(
        transcript, client=fake, max_removal_fraction=0.35, max_region_seconds=60.0
    )
    # Budget is 3.5 s; both regions are 2 s, so only the stronger one fits.
    assert flagged == [(5.0, 7.0)]


def test_fraction_of_one_disables_the_cap():
    """Escape hatch: an explicit 100 % cap restores the old behaviour."""
    fake = _FakeClassifyClient(scores={"a very long rambling intro": 0.95, "false start here": 0.8})
    flagged = _detect_semantic_low_value(
        _demo_transcript(),
        client=fake,
        max_removal_fraction=1.0,
        max_region_seconds=100.0,
    )
    assert flagged == [(0.0, 11.4), (12.0, 13.3)]


def test_zero_fraction_keeps_everything():
    fake = _FakeClassifyClient(scores={"false start here": 0.9})
    assert (
        _detect_semantic_low_value(_demo_transcript(), client=fake, max_removal_fraction=0.0) == []
    )
