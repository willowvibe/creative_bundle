"""Regression tests for the HyperFrames video provider and stub."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.visual.hyperframes_video import (
    HyperFramesVideoProvider,
    StubHyperFramesVideoProvider,
    _build_html,
)


def _request(
    asset_id: str = "hf_1",
    scene_id: str = "scene_1",
    prompt: str = "a drifting cloud",
    duration_seconds: float = 3.0,
) -> AssetRequest:
    return AssetRequest(
        asset_id=asset_id,
        job_id="job_1",
        scene_id=scene_id,
        asset_type=AssetType.VIDEO,
        prompt=prompt,
        provider="hyperframes_video",
        output_ext="mp4",
        params={"duration_seconds": duration_seconds},
    )


def test_build_html_contains_prompt_and_dimensions() -> None:
    """The generated composition embeds the prompt and scene size."""
    html = _build_html("drifting clouds", 5.0, 1280, 720)
    assert "drifting clouds" in html
    assert "width: 1280px" in html
    assert "height: 720px" in html
    assert "@keyframes breathe" in html


def test_hyperframes_video_rejects_non_video_request() -> None:
    """The provider refuses asset types other than VIDEO."""
    provider = HyperFramesVideoProvider()
    image_request = AssetRequest(
        asset_id="img_1",
        job_id="job_1",
        scene_id="scene_1",
        asset_type=AssetType.IMAGE,
        prompt="a mountain",
        provider="hyperframes_video",
        output_ext="png",
    )
    with pytest.raises(ValueError, match="expected video"):
        # generate is async; run it through asyncio for the synchronous test.
        import asyncio

        asyncio.run(provider.generate(image_request, Path("/tmp/out.mp4")))


@pytest.mark.skipif(
    not pytest.importorskip("subprocess") or Path("/dev/null").exists() is False,
    reason="requires a POSIX path environment",
)
@pytest.mark.asyncio
async def test_stub_hyperframes_video_generates_valid_mp4(tmp_path: Path) -> None:
    """The stub provider writes a probeable MP4 with audio and metadata."""
    pytest.importorskip("shutil")
    import shutil

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")

    output_path = tmp_path / "scene.mp4"
    provider = StubHyperFramesVideoProvider()
    result = await provider.generate(_request(duration_seconds=2.0), output_path)

    assert isinstance(result, AssetResult)
    assert result.asset_type == AssetType.VIDEO
    assert Path(result.local_path) == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0
    assert result.duration_ms is not None
    assert result.duration_ms >= 1500  # at least 1.5s of the requested 2s
    assert result.metadata.get("provider") == "stub_hyperframes_video"
    assert result.metadata.get("width") == 1920
    assert result.metadata.get("height") == 1080


@pytest.mark.asyncio
async def test_hyperframes_video_uses_cli_render(monkeypatch: Any, tmp_path: Path) -> None:
    """The real provider invokes the HyperFrames CLI wrapper and probes output."""
    output_path = tmp_path / "motion.mp4"
    called: dict[str, Any] = {}

    def _fake_render(
        input_plan: Path | str,
        out: Path | str,
        cli_path: str,
        timeout: int,
        extra_args: dict[str, Any] | None = None,
    ) -> Path:
        called["input_plan"] = input_plan
        called["output"] = out
        called["cli_path"] = cli_path
        called["timeout"] = timeout
        called["extra_args"] = extra_args
        # Write a minimal valid MP4 so probe_media succeeds.
        import shutil
        import subprocess

        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            pytest.skip("ffmpeg/ffprobe not on PATH")
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=640x480:d=1:r=30",
                "-pix_fmt",
                "yuv420p",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
        return Path(out)

    monkeypatch.setattr(
        "video_editor_asset_ingestion.providers.visual.hyperframes_video.hyperframes_cli.render_segment",
        _fake_render,
    )

    provider = HyperFramesVideoProvider()
    request = _request(prompt="red screen", duration_seconds=1.0)
    result = await provider.generate(request, output_path)

    assert result.asset_type == AssetType.VIDEO
    assert result.local_path == str(output_path)
    assert called["output"] == output_path
    assert called["cli_path"] == "hyperframes"
    assert called["timeout"] == 600
    assert called["extra_args"] == {"fps": 30}
    project_dir = tmp_path / f"hf_project_{request.asset_id}"
    assert called["input_plan"] == project_dir
    assert (project_dir / "index.html").exists()


@pytest.mark.asyncio
async def test_hyperframes_video_respects_provider_options() -> None:
    """Width, height, fps, cli_path, and timeout are read from provider_options."""
    from video_editor_asset_ingestion.config import ProviderConfig

    config = ProviderConfig(
        provider_options={
            "width": 1280,
            "height": 720,
            "fps": 24,
            "cli_path": "./node_modules/.bin/hyperframes",
            "timeout": 120,
        }
    )
    provider = HyperFramesVideoProvider(config)
    assert provider.width == 1280
    assert provider.height == 720
    assert provider.fps == 24
    assert provider.cli_path == "./node_modules/.bin/hyperframes"
    assert provider.timeout == 120
