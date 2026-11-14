"""Real-agy smoke test. Skipped unless explicitly selected.

Run with: VE_AGY_INTEGRATION=1 pytest tests/test_agy_image_integration.py -m integration -v
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.visual.agy_image import (
    AGYImageProvider,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _require_agy() -> None:
    """Real generations cost quota and tens of seconds: opt in explicitly."""
    if os.environ.get("VE_AGY_INTEGRATION") != "1":
        pytest.skip("set VE_AGY_INTEGRATION=1 to run the real-agy test")
    if shutil.which("agy") is None:
        pytest.skip("agy not installed")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("aspect", "width", "height"),
    [("16:9", 512, 288), ("9:16", 288, 512)],
)
async def test_real_agy_generates_a_readable_png(
    tmp_path: Path, aspect: str, width: int, height: int
) -> None:
    provider = AGYImageProvider(
        ProviderConfig(
            timeout=300.0,
            provider_options={
                "agy_bin": "agy",
                "profile_dir": str(tmp_path / "profile"),
                "session": True,
            },
        )
    )
    request = AssetRequest(
        asset_id=f"img-real-{aspect}",
        job_id="job-real",
        scene_id="s1",
        asset_type=AssetType.IMAGE,
        prompt="a plain red circle centred on a white background",
        output_ext="png",
        params={"width": width, "height": height},
    )
    output = tmp_path / f"real-{aspect.replace(':', 'x')}.png"
    try:
        result = await provider.generate(request, output)
    finally:
        await provider.aclose()

    assert output.exists() and output.stat().st_size > 1000
    assert result.metadata["width"] > 0
    assert result.metadata["height"] > 0
    assert result.metadata["agy_version"]
    assert result.metadata["image_model"]
    expected = width / height
    assert abs((result.metadata["width"] / result.metadata["height"]) / expected - 1) <= 0.03
