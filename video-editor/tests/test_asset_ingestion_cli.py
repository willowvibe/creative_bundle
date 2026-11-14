"""CLI integration tests for the generative asset ingestion pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner
from video_editor_cli.main import app


def test_cli_assets_command(tmp_path: Path) -> None:
    """Run the ``ve assets`` command end-to-end against a synthetic scene plan."""
    job_dir = tmp_path / "job_cli"
    plan_dir = job_dir / "plan"
    plan_dir.mkdir(parents=True)

    plan = {
        "job_id": "job_cli",
        "script": "CLI test script.",
        "scenes": [
            {
                "id": "scene_cli_1",
                "type": "narration",
                "duration_seconds": 3.0,
                "narration": "CLI asset ingestion works.",
                "visual_description": "A simple test image.",
                "asset_query": "",
                "props": {},
            }
        ],
    }
    (plan_dir / "scene_plan.json").write_text(json.dumps(plan), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "assets",
            "job_cli",
            "--work-root",
            str(tmp_path),
            "--output-root",
            str(tmp_path),
            "--tts-provider",
            "stub_tts",
            "--image-provider",
            "stub_image",
        ],
    )
    assert result.exit_code == 0, result.output

    out_path = plan_dir / "scene_plan_with_assets.json"
    assert out_path.exists()
    enriched = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(enriched["scenes"][0]["assets"]) == 2
    assert any(a["asset_type"] == "tts" for a in enriched["scenes"][0]["assets"])
    assert any(a["asset_type"] == "image" for a in enriched["scenes"][0]["assets"])


def test_cli_assets_command_with_video_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Session R: ``--video-provider fal_ai`` exercises the VIDEO asset path.

    Mocks the FAL provider's ``generate`` so the test stays
    offline (no FAL key needed, no real HTTP). The FAL mock writes a
    small stub MP4-like file; the stub IMAGE provider handles the
    parallel IMAGE request. Asserts that:

    - The scene plan ends up with TTS, IMAGE, and VIDEO assets.
    - The VIDEO asset is appended BEFORE the IMAGE asset in
      ``scene.assets`` so the renderer's
      ``scene.assets.find(image|video|data_viz)`` picks the VIDEO.
    """
    job_dir = tmp_path / "job_cli_vid"
    plan_dir = job_dir / "plan"
    plan_dir.mkdir(parents=True)

    plan = {
        "job_id": "job_cli_vid",
        "script": "CLI test script with video.",
        "scenes": [
            {
                "id": "scene_cli_vid_1",
                "type": "narration",
                "duration_seconds": 3.0,
                "narration": "A video reel.",
                "visual_description": "A short moving clip of waves.",
                "asset_query": "",
                "props": {"requires_motion": True},
            }
        ],
    }
    (plan_dir / "scene_plan.json").write_text(json.dumps(plan), encoding="utf-8")

    # Monkeypatch FalAIProvider.generate so the test stays offline.
    # We don't care about real video bytes; just write any small
    # file at the expected output path so the manager can wrap it
    # in an AssetResult.
    from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
    from video_editor_asset_ingestion.providers.visual.fal_ai import FalAIProvider
    from video_editor_asset_ingestion.providers.visual.stub import StubImageProvider

    real_stub_generate = StubImageProvider.generate

    async def fake_fal_generate(
        self: FalAIProvider, request: object, output_path: Path
    ) -> AssetResult:
        # ``request`` is an AssetRequest — narrow via attributes.
        assert isinstance(request, AssetRequest)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Write a tiny placeholder so the file exists; the metadata
        # is what the test actually checks.
        output_path.write_bytes(b"\x00\x00\x00\x00 fakemp4")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.VIDEO,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=int(request.params.get("duration", 5.0) * 1000),
            metadata={"provider": "fal_ai", "model": "fake-luma"},
        )

    async def tracking_stub_generate(
        self: StubImageProvider, request: object, output_path: Path
    ) -> AssetResult:
        # Only stub IMAGE requests; let everything else through.
        assert isinstance(request, AssetRequest)
        if request.asset_type == AssetType.IMAGE:
            return await real_stub_generate(self, request, output_path)
        raise RuntimeError("stub should not see non-IMAGE")

    monkeypatch.setattr(FalAIProvider, "generate", fake_fal_generate)
    monkeypatch.setattr(StubImageProvider, "generate", tracking_stub_generate)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "assets",
            "job_cli_vid",
            "--work-root",
            str(tmp_path),
            "--output-root",
            str(tmp_path),
            "--tts-provider",
            "stub_tts",
            "--image-provider",
            "stub_image",
            "--video-provider",
            "fal_ai",
        ],
    )
    assert result.exit_code == 0, result.output

    out_path = plan_dir / "scene_plan_with_assets.json"
    assert out_path.exists()
    enriched = json.loads(out_path.read_text(encoding="utf-8"))
    assets = enriched["scenes"][0]["assets"]
    asset_types = {a["asset_type"] for a in assets}
    # All three asset types must be present.
    assert "tts" in asset_types
    assert "image" in asset_types
    assert "video" in asset_types
    # VIDEO must be appended before IMAGE in scene.assets so the
    # renderer picks it via .find(image|video|data_viz).
    type_order = [a["asset_type"] for a in assets]
    assert type_order.index("video") < type_order.index("image")


def test_cli_assets_command_with_stub_hyperframes_video_provider(
    tmp_path: Path,
) -> None:
    """Hybrid motion path: ``--video-provider stub_hyperframes_video`` stays offline.

    Verifies that the local HyperFrames stub produces a real VIDEO asset before
    the IMAGE fallback, so the renderer will use the generated clip.
    """
    job_dir = tmp_path / "job_cli_stub_vid"
    plan_dir = job_dir / "plan"
    plan_dir.mkdir(parents=True)

    plan = {
        "job_id": "job_cli_stub_vid",
        "script": "CLI test script with stub HyperFrames motion.",
        "scenes": [
            {
                "id": "scene_cli_stub_vid_1",
                "type": "narration",
                "duration_seconds": 2.0,
                "narration": "A generated motion clip.",
                "visual_description": "Abstract moving color field.",
                "asset_query": "",
                "props": {"requires_motion": True},
            }
        ],
    }
    (plan_dir / "scene_plan.json").write_text(json.dumps(plan), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "assets",
            "job_cli_stub_vid",
            "--work-root",
            str(tmp_path),
            "--output-root",
            str(tmp_path),
            "--tts-provider",
            "stub_tts",
            "--image-provider",
            "stub_image",
            "--video-provider",
            "stub_hyperframes_video",
        ],
    )
    assert result.exit_code == 0, result.output

    out_path = plan_dir / "scene_plan_with_assets.json"
    assert out_path.exists()
    enriched = json.loads(out_path.read_text(encoding="utf-8"))
    assets = enriched["scenes"][0]["assets"]
    asset_types = {a["asset_type"] for a in assets}
    assert "tts" in asset_types
    assert "image" in asset_types
    assert "video" in asset_types
    type_order = [a["asset_type"] for a in assets]
    assert type_order.index("video") < type_order.index("image")
    video_asset = next(a for a in assets if a["asset_type"] == "video")
    assert Path(video_asset["local_path"]).exists()


def test_cli_assets_command_with_poyo_video_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PoYo.ai ``--video-provider poyo_ai`` exercises the VIDEO asset path offline.

    Mirrors the FAL video CLI test but targets the new primary provider.
    Mocks ``PoyoAIProvider.generate`` so no real PoYo key or HTTP is needed.
    """
    job_dir = tmp_path / "job_cli_poyo_vid"
    plan_dir = job_dir / "plan"
    plan_dir.mkdir(parents=True)

    plan = {
        "job_id": "job_cli_poyo_vid",
        "script": "CLI test script with PoYo video.",
        "scenes": [
            {
                "id": "scene_cli_poyo_vid_1",
                "type": "narration",
                "duration_seconds": 3.0,
                "narration": "A PoYo video reel.",
                "visual_description": "A short moving clip of waves.",
                "asset_query": "",
                "props": {"requires_motion": True},
            }
        ],
    }
    (plan_dir / "scene_plan.json").write_text(json.dumps(plan), encoding="utf-8")

    from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
    from video_editor_asset_ingestion.providers.visual.poyo_ai import PoyoAIProvider
    from video_editor_asset_ingestion.providers.visual.stub import StubImageProvider

    real_stub_generate = StubImageProvider.generate

    async def fake_poyo_generate(
        self: PoyoAIProvider, request: object, output_path: Path
    ) -> AssetResult:
        assert isinstance(request, AssetRequest)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x00\x00\x00\x00 fakepoyo")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.VIDEO,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=int(request.params.get("duration_seconds", 5.0) * 1000),
            metadata={"provider": "poyo_ai", "model": "seedance-2-fast"},
        )

    async def tracking_stub_generate(
        self: StubImageProvider, request: object, output_path: Path
    ) -> AssetResult:
        assert isinstance(request, AssetRequest)
        if request.asset_type == AssetType.IMAGE:
            return await real_stub_generate(self, request, output_path)
        raise RuntimeError("stub should not see non-IMAGE")

    monkeypatch.setenv("POYO_API_KEY", "test-poyo-key")
    monkeypatch.setattr(PoyoAIProvider, "generate", fake_poyo_generate)
    monkeypatch.setattr(StubImageProvider, "generate", tracking_stub_generate)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "assets",
            "job_cli_poyo_vid",
            "--work-root",
            str(tmp_path),
            "--output-root",
            str(tmp_path),
            "--tts-provider",
            "stub_tts",
            "--image-provider",
            "stub_image",
            "--video-provider",
            "poyo_ai",
        ],
    )
    assert result.exit_code == 0, result.output

    out_path = plan_dir / "scene_plan_with_assets.json"
    assert out_path.exists()
    enriched = json.loads(out_path.read_text(encoding="utf-8"))
    assets = enriched["scenes"][0]["assets"]
    asset_types = {a["asset_type"] for a in assets}
    assert "tts" in asset_types
    assert "image" in asset_types
    assert "video" in asset_types
    type_order = [a["asset_type"] for a in assets]
    assert type_order.index("video") < type_order.index("image")
    video_asset = next(a for a in assets if a["asset_type"] == "video")
    assert Path(video_asset["local_path"]).exists()
