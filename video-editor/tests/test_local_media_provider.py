"""Tests for the local-media asset provider."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.visual.local_media import (
    LocalMediaProvider,
    StubLocalMediaProvider,
)


def _minimal_png() -> bytes:
    return bytes(
        [
            0x89,
            0x50,
            0x4E,
            0x47,
            0x0D,
            0x0A,
            0x1A,
            0x0A,
            0x00,
            0x00,
            0x00,
            0x0D,
            0x49,
            0x48,
            0x44,
            0x52,
            0x00,
            0x00,
            0x00,
            0x01,
            0x00,
            0x00,
            0x00,
            0x01,
            0x08,
            0x02,
            0x00,
            0x00,
            0x00,
            0x90,
            0x77,
            0x53,
            0xDE,
            0x00,
            0x00,
            0x00,
            0x0C,
            0x49,
            0x44,
            0x41,
            0x54,
            0x08,
            0xD7,
            0x63,
            0xF8,
            0x0F,
            0x00,
            0x00,
            0x01,
            0x01,
            0x00,
            0x05,
            0xDD,
            0xDD,
            0xA4,
            0x0E,
            0x00,
            0x00,
            0x00,
            0x00,
            0x49,
            0x45,
            0x4E,
            0x44,
            0xAE,
            0x42,
            0x60,
            0x82,
        ]
    )


@pytest.mark.asyncio
async def test_local_media_provider_copies_image(tmp_path: Path) -> None:
    """An IMAGE request copies the source file into the output path."""
    source = tmp_path / "photo.png"
    source.write_bytes(_minimal_png())
    output = tmp_path / "out" / "cached.png"

    provider = LocalMediaProvider()
    request = AssetRequest(
        asset_id="scene_001_local_media",
        job_id="j",
        scene_id="scene_001",
        asset_type=AssetType.IMAGE,
        prompt=str(source),
        provider="local_media",
        output_ext="png",
    )
    result = await provider.generate(request, output)

    assert isinstance(result, AssetResult)
    assert result.asset_id == request.asset_id
    assert result.asset_type == AssetType.IMAGE
    assert result.local_path == str(output.resolve())
    assert result.duration_ms == 0
    assert output.exists()
    assert output.read_bytes() == source.read_bytes()
    assert result.metadata["source"] == str(source.resolve())


@pytest.mark.asyncio
async def test_local_media_provider_missing_source(tmp_path: Path) -> None:
    """A missing source file raises FileNotFoundError."""
    provider = LocalMediaProvider()
    request = AssetRequest(
        asset_id="scene_001_local_media",
        job_id="j",
        scene_id="scene_001",
        asset_type=AssetType.IMAGE,
        prompt=str(tmp_path / "does_not_exist.png"),
        provider="local_media",
        output_ext="png",
    )
    with pytest.raises(FileNotFoundError):
        await provider.generate(request, tmp_path / "out.png")


@pytest.mark.asyncio
async def test_stub_local_media_provider_writes_png(tmp_path: Path) -> None:
    """The stub provider writes a loadable PNG even without a real source."""
    output = tmp_path / "stub.png"
    provider = StubLocalMediaProvider()
    request = AssetRequest(
        asset_id="scene_001_local_media",
        job_id="j",
        scene_id="scene_001",
        asset_type=AssetType.IMAGE,
        prompt="",
        provider="stub_local_media",
        output_ext="png",
    )
    result = await provider.generate(request, output)
    assert result.asset_type == AssetType.IMAGE
    assert output.exists()
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_local_media_provider_rejects_symlink_source(tmp_path: Path) -> None:
    """Bug #198 (provider-side): a symlink ``source`` is rejected outright.
    The planner stores the resolved real path in ``request.prompt``, so a
    symlink here means the request bypassed the planner's containment check
    (e.g. a symlink planted in ``media_dir`` pointing at another tenant's
    file). ``shutil.copy2`` would otherwise follow it and pull the out-of-tree
    file into the tenant's asset cache."""
    target = tmp_path / "real.png"
    target.write_bytes(_minimal_png())
    link = tmp_path / "link.png"
    link.symlink_to(target)

    provider = LocalMediaProvider()
    request = AssetRequest(
        asset_id="scene_001_local_media",
        job_id="j",
        scene_id="scene_001",
        asset_type=AssetType.IMAGE,
        prompt=str(link),
        provider="local_media",
        output_ext="png",
    )
    with pytest.raises(ValueError, match="symlink"):
        await provider.generate(request, tmp_path / "out.png")


@pytest.mark.asyncio
async def test_stub_local_media_provider_writes_mp4_for_video(tmp_path: Path) -> None:
    """Bug #214: a VIDEO request must produce a loadable MP4, not a PNG at a
    ``.mp4`` path. The stub writes a tiny valid MP4 via ffmpeg; the result
    carries ``asset_type=VIDEO`` and a non-PNG (MP4 box) header."""
    if __import__("shutil").which("ffmpeg") is None:
        pytest.skip("ffmpeg not available on PATH")
    output = tmp_path / "stub.mp4"
    provider = StubLocalMediaProvider()
    request = AssetRequest(
        asset_id="scene_001_local_media",
        job_id="j",
        scene_id="scene_001",
        asset_type=AssetType.VIDEO,
        prompt="",
        provider="stub_local_media",
        output_ext="mp4",
    )
    result = await provider.generate(request, output)
    assert result.asset_type == AssetType.VIDEO
    assert output.exists()
    # MP4 files begin with a ``ftyp`` box, not a PNG signature.
    assert not output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    # Probe confirms it is a real, loadable video (duration > 0).
    from video_editor_ffmpeg.probe import probe_media

    assert probe_media(output).duration_ms() > 0


@pytest.mark.asyncio
async def test_stub_local_media_provider_image_still_writes_png(tmp_path: Path) -> None:
    """Bug #214 regression guard: the IMAGE branch is unchanged — still a PNG,
    not accidentally routed through the MP4 path."""
    output = tmp_path / "stub.png"
    provider = StubLocalMediaProvider()
    request = AssetRequest(
        asset_id="scene_001_local_media",
        job_id="j",
        scene_id="scene_001",
        asset_type=AssetType.IMAGE,
        prompt="",
        provider="stub_local_media",
        output_ext="png",
    )
    result = await provider.generate(request, output)
    assert result.asset_type == AssetType.IMAGE
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
