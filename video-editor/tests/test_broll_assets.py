"""Tests for the b-roll clip generator and its provider resolution.

The b-roll pipeline has three paths:
- placeholder (default, no API needed)
- a real generative provider (FAL.AI, etc.) via AssetIngestionManager
- a fallback to placeholder when generation fails

These tests guard the dispatch logic and the placeholder path
without requiring API keys or network access.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_core.models.edit_plan import BRollInsert
from video_editor_engine.broll_assets import (
    DEFAULT_BROLL_PROVIDER,
    _format_duration,
    _resolve_provider,
    generate_broll_clip,
)
from video_editor_state.job import JobContext


def _make_insert(**overrides: object) -> BRollInsert:
    """Build a default BRollInsert for tests."""
    defaults: dict[str, object] = {
        "start": 1.0,
        "end": 3.0,
        "description": "A scenic mountain",
        "asset_query": "",
        "provider": "",
    }
    defaults.update(overrides)
    return BRollInsert(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="broll-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_format_duration_sub_second() -> None:
    """Durations under 1s are formatted in milliseconds."""
    assert _format_duration(0.5) == "500ms"
    assert _format_duration(0.05) == "50ms"


def test_format_duration_seconds() -> None:
    """Durations >= 1s are formatted with one decimal place."""
    assert _format_duration(1.0) == "1.0s"
    assert _format_duration(3.14) == "3.1s"


def test_resolve_provider_defaults_to_placeholder() -> None:
    """An empty / missing provider resolves to ``placeholder``."""
    assert _resolve_provider(_make_insert(provider="")) == DEFAULT_BROLL_PROVIDER
    assert _resolve_provider(_make_insert(provider="placeholder")) == DEFAULT_BROLL_PROVIDER


def test_resolve_provider_preserves_explicit_choice() -> None:
    """A non-placeholder provider is preserved (after lowercasing + strip)."""
    assert _resolve_provider(_make_insert(provider="fal_ai")) == "fal_ai"
    assert _resolve_provider(_make_insert(provider="  FAL_AI  ")) == "fal_ai"


# ---------------------------------------------------------------------------
# generate_broll_clip: placeholder path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_broll_clip_uses_placeholder_by_default(
    ctx: JobContext,
    tmp_path: Path,
) -> None:
    """When no provider is set (or provider=placeholder), a colored
    placeholder video is written to the output path."""
    insert = _make_insert(start=0.0, end=0.5)  # 0.5s
    out = tmp_path / "broll.mp4"

    result = await generate_broll_clip(ctx, insert, out)

    assert result == out
    assert out.exists()
    assert out.stat().st_size > 0


@pytest.mark.asyncio
async def test_generate_broll_clip_uses_placeholder_when_explicit(
    ctx: JobContext,
    tmp_path: Path,
) -> None:
    """An explicit ``provider="placeholder"`` also writes a placeholder."""
    insert = _make_insert(start=0.0, end=0.5, provider="placeholder")
    out = tmp_path / "broll2.mp4"

    result = await generate_broll_clip(ctx, insert, out, fallback_label="Custom")

    assert result == out
    assert out.exists()


@pytest.mark.asyncio
async def test_generate_broll_clip_placeholder_escapes_special_label_chars(
    ctx: JobContext,
    tmp_path: Path,
) -> None:
    """C43 regression: placeholder labels containing drawtext special chars
    (apostrophes, percent signs, braces, commas, colons, backslashes) must
    not break FFmpeg. The fix uses ``textfile=`` instead of inline text."""
    insert = _make_insert(start=0.0, end=0.5, provider="placeholder")
    out = tmp_path / "broll_escaped.mp4"

    result = await generate_broll_clip(ctx, insert, out, fallback_label="it's 100% {ok} : a,b \\ c")

    assert result == out
    assert out.exists()
    assert out.stat().st_size > 0


# ---------------------------------------------------------------------------
# generate_broll_clip: failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_broll_clip_falls_back_when_provider_fails(
    ctx: JobContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the configured provider raises, the wrapper falls back to a
    placeholder so the downstream compositor never breaks."""
    from video_editor_asset_ingestion import manager as manager_module

    async def always_fail(
        self: object,
        requests: list[object],
    ) -> list[object]:
        raise RuntimeError("simulated provider outage")

    monkeypatch.setattr(
        manager_module.AssetIngestionManager,
        "resolve",
        always_fail,
    )

    insert = _make_insert(start=0.0, end=0.5, provider="fal_ai")
    out = tmp_path / "broll_fallback.mp4"

    result = await generate_broll_clip(ctx, insert, out)

    # Fallback placeholder was written.
    assert result == out
    assert out.exists()
    assert out.stat().st_size > 0
