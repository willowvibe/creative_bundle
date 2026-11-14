"""The estimate prices the model each provider will actually call (malacca-3: $0.265 vs $0.305)."""

from __future__ import annotations

import pytest
from video_editor_asset_ingestion.pricing import PricingRegistry, estimate_tts_cost
from video_editor_asset_ingestion.providers.tts import elevenlabs_tts, sarvam_tts
from video_editor_asset_ingestion.providers.visual import poyo_ai
from video_editor_pipelines.pricing import resolve_model


@pytest.mark.parametrize(
    ("provider", "asset_type", "expected"),
    [
        ("elevenlabs_tts", "tts", elevenlabs_tts.DEFAULT_MODEL),
        ("sarvam_tts", "tts", sarvam_tts.DEFAULT_MODEL),
        ("poyo_ai", "image", poyo_ai.DEFAULT_IMAGE_MODEL),
    ],
)
def test_estimate_default_model_follows_the_provider(provider, asset_type, expected):
    assert resolve_model(provider, asset_type) == expected


def test_elevenlabs_narration_is_estimated_at_the_v4_rate():
    model = resolve_model("elevenlabs_tts", "tts")
    cost = estimate_tts_cost("elevenlabs_tts", model, 1500, PricingRegistry.load())
    assert cost == pytest.approx(0.12)  # 1,500 characters x $0.08 per 1k
