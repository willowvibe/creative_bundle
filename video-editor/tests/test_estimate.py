"""Integration tests for the ve estimate command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def temp_work_dir(tmp_path: Path) -> Path:
    """Create a minimal work directory with a scene plan."""
    work = tmp_path / "test-job"
    plan_dir = work / "plan"
    plan_dir.mkdir(parents=True)

    plan = {
        "job_id": "test-job",
        "script": "# Test\n\n## Scene 1\n- Narration: Hello world\n- Visual: A test image\n",
        "scenes": [
            {
                "id": "scene_001",
                "type": "narration",
                "duration_seconds": 5.0,
                "narration": "Hello world",
                "visual_description": "A test image",
                "asset_query": "test",
                "props": {},
                "assets": [],
            }
        ],
        "aspect_ratio": "16:9",
        "music_mood": "cinematic",
        "music_path": "",
    }
    (plan_dir / "scene_plan.json").write_text(json.dumps(plan))
    return work


class TestEstimateCommand:
    def test_auto_image_pricing_provider_picks_the_first_paid_chain_member(self) -> None:
        """``auto`` has no price row; price the chain member that is billed."""
        from video_editor_asset_ingestion.pricing import PricingRegistry
        from video_editor_cli.commands.estimate import auto_image_pricing_provider

        registry = PricingRegistry.load()
        # The head (agy_image) is quota-based and unpriced.
        assert auto_image_pricing_provider(["agy_image", "fal_ai", "poyo_ai"], registry) == "fal_ai"
        # With no configured chain, or no paid member, keep the old default.
        assert auto_image_pricing_provider([], registry) == "poyo_ai"
        assert auto_image_pricing_provider(["agy_image"], registry) == "poyo_ai"

    def test_estimate_requires_job_id(self, runner: CliRunner) -> None:
        """ve estimate without a job-id should fail."""
        # We test the command function directly since we can't easily
        # test the registered Typer app in isolation.
        from video_editor_cli.commands.estimate import estimate_command

        # Missing scene plan should raise
        with pytest.raises(typer.Exit):
            estimate_command(
                job_id="nonexistent",
                work_root="/tmp/nonexistent",
                output_root="/tmp/nonexistent",
                config_path=None,
                auto_approve=False,
                tts_provider="elevenlabs_tts",
                image_provider="poyo_ai",
                video_provider="poyo_ai",
                music_provider="poyo_music",
                voice="instant",
            )

    def test_estimate_skips_music_when_the_plan_asks_for_none(self, temp_work_dir: Path) -> None:
        """#266: a voice-only plan must not be charged for a track."""
        from video_editor_cli.commands.estimate import estimate_command

        plan_path = temp_work_dir / "plan" / "scene_plan.json"
        plan = json.loads(plan_path.read_text())
        plan["music_mood"] = ""
        plan["music_path"] = ""
        plan_path.write_text(json.dumps(plan))

        estimate_command(
            job_id="test-job",
            work_root=str(temp_work_dir.parent),
            output_root=str(temp_work_dir.parent),
            config_path=None,
            auto_approve=True,
            tts_provider="elevenlabs_tts",
            image_provider="poyo_ai",
            video_provider="poyo_ai",
            music_provider="poyo_music",
            voice="instant",
        )

        data = json.loads((temp_work_dir / "plan" / "cost_estimate.json").read_text())
        assert not [i for i in data["line_items"] if i["task_type"] == "music"]

    def test_estimate_skips_music_when_the_plan_supplies_a_bed(self, temp_work_dir: Path) -> None:
        """#266: a plan with its own ``music_path`` buys no track."""
        from video_editor_cli.commands.estimate import estimate_command

        plan_path = temp_work_dir / "plan" / "scene_plan.json"
        plan = json.loads(plan_path.read_text())
        plan["music_mood"] = "cinematic"
        plan["music_path"] = "/tmp/bed.m4a"
        plan_path.write_text(json.dumps(plan))

        estimate_command(
            job_id="test-job",
            work_root=str(temp_work_dir.parent),
            output_root=str(temp_work_dir.parent),
            config_path=None,
            auto_approve=True,
            tts_provider="elevenlabs_tts",
            image_provider="poyo_ai",
            video_provider="poyo_ai",
            music_provider="poyo_music",
            voice="instant",
        )

        data = json.loads((temp_work_dir / "plan" / "cost_estimate.json").read_text())
        assert not [i for i in data["line_items"] if i["task_type"] == "music"]

    def test_estimate_with_auto_approve(self, temp_work_dir: Path) -> None:
        """ve estimate --yes should write cost_estimate.json without prompting."""
        from video_editor_cli.commands.estimate import estimate_command

        estimate_command(
            job_id="test-job",
            work_root=str(temp_work_dir.parent),
            output_root=str(temp_work_dir.parent),
            config_path=None,
            auto_approve=True,
            tts_provider="elevenlabs_tts",
            image_provider="poyo_ai",
            video_provider="poyo_ai",
            music_provider="poyo_music",
            voice="instant",
        )

        estimate_path = temp_work_dir / "plan" / "cost_estimate.json"
        assert estimate_path.exists()

        data = json.loads(estimate_path.read_text())
        assert data["job_id"] == "test-job"
        assert data["voice_id"] == "32EwRSV41WufKgzh80Ri"
        assert data["voice_name"] == "instant"
        assert "line_items" in data
        assert "total_cost" in data
        assert data["currency"] == "USD"

    def test_estimate_writes_manifest_stage(self, temp_work_dir: Path) -> None:
        """ve estimate should mark the estimate stage in the manifest."""
        from video_editor_cli.commands.estimate import estimate_command

        # Create a manifest first
        manifest_path = temp_work_dir / "manifest.json"
        manifest = {
            "job_id": "test-job",
            "tenant_id": "default",
            "created_at": "2026-08-02T00:00:00Z",
            "work_root": str(temp_work_dir),
            "output_root": str(temp_work_dir.parent),
            "stages": {},
            "operations": [],
        }
        manifest_path.write_text(json.dumps(manifest))

        estimate_command(
            job_id="test-job",
            work_root=str(temp_work_dir.parent),
            output_root=str(temp_work_dir.parent),
            config_path=None,
            auto_approve=True,
            tts_provider="elevenlabs_tts",
            image_provider="poyo_ai",
            video_provider="poyo_ai",
            music_provider="poyo_music",
            voice="instant",
        )

        updated = json.loads(manifest_path.read_text())
        assert "estimate" in updated["stages"]
        assert "completed_at" in updated["stages"]["estimate"]


def _write_two_scene_plan(
    work_root: Path,
    image_model: str,
    resolution: str | None = None,
    quality: str | None = None,
) -> Path:
    """Two image scenes that name their own image model in ``props``.

    ``resolution`` and ``quality`` mirror the tiered planners'
    ``image_resolution`` / ``image_quality`` props: the plan declares the tier
    it will bill at (#269, #270).
    """
    work = work_root / "saju"
    plan_dir = work / "plan"
    plan_dir.mkdir(parents=True)
    props: dict[str, str] = {"image_model": image_model}
    if resolution:
        props["image_resolution"] = resolution
    if quality:
        props["image_quality"] = quality
    scenes = [
        {
            "id": f"scene_{n:03d}",
            "type": "narration",
            "duration_seconds": 5.0,
            "narration": "Hello world.",
            "visual_description": "A neon-lit alley.",
            "asset_query": "",
            "props": dict(props),
            "assets": [],
        }
        for n in (1, 2)
    ]
    plan = {
        "job_id": "saju",
        "script": "# Saju\n\nTwo scenes.\n",
        "scenes": scenes,
        "aspect_ratio": "9:16",
        "music_mood": "",
        "music_path": "",
    }
    (plan_dir / "scene_plan.json").write_text(json.dumps(plan))
    return work


def test_estimate_defaults_poyo_images_to_nano_banana_lite(tmp_path: Path) -> None:
    """A plan that names no image model is quoted at PoYo's default.

    ``nano-banana-2-lite`` is the flat 5-credit still; ``gpt-image-2`` only
    matches that price at its low/1K cell and costs far more elsewhere, so
    defaulting to it makes the gate silently depend on two other props.
    """
    from video_editor_cli.commands.estimate import estimate_command

    _write_two_scene_plan(tmp_path, "")
    estimate_command(
        job_id="saju",
        work_root=str(tmp_path),
        output_root=str(tmp_path),
        config_path=None,
        auto_approve=True,
        tts_provider="elevenlabs_tts",
        image_provider="poyo_ai",
        video_provider="poyo_ai",
        music_provider="",
        voice="instant",
    )

    data = json.loads((tmp_path / "saju" / "plan" / "cost_estimate.json").read_text())
    image_items = [i for i in data["line_items"] if i["task_type"] == "image"]
    assert len(image_items) == 1
    assert image_items[0]["model"] == "nano-banana-2-lite"
    assert image_items[0]["total_cost"] == 0.05  # 2 x $0.025


def test_estimate_prices_the_model_the_plan_chose(tmp_path: Path) -> None:
    """The estimate must quote the plan's model, not the provider default.

    A storyteller/tiered plan sets ``props.image_model``; pricing it as
    ``gpt-image-2`` misreports the gate by a factor of 2.5 (#262).
    """
    from video_editor_cli.commands.estimate import estimate_command

    _write_two_scene_plan(tmp_path, "nano-banana-2-lite")
    estimate_command(
        job_id="saju",
        work_root=str(tmp_path),
        output_root=str(tmp_path),
        config_path=None,
        auto_approve=True,
        tts_provider="elevenlabs_tts",
        image_provider="poyo_ai",
        video_provider="poyo_ai",
        music_provider="",
        voice="instant",
    )

    data = json.loads((tmp_path / "saju" / "plan" / "cost_estimate.json").read_text())
    image_items = [i for i in data["line_items"] if i["task_type"] == "image"]
    assert len(image_items) == 1
    assert image_items[0]["model"] == "nano-banana-2-lite"
    assert image_items[0]["total_cost"] == 0.05  # 2 x $0.025


def test_estimate_prices_the_resolution_the_plan_chose(tmp_path: Path) -> None:
    """A 2K plan must be quoted at the 2K rate, not the model's 1K rate.

    ``gpt-image-2.5-fixed`` bills 1.7 credits at 1K and 6 at 2K; the registry
    held one scalar, so a five-still 2K job was approved at $0.0425 against a
    $0.15 bill (#269).
    """
    from video_editor_cli.commands.estimate import estimate_command

    _write_two_scene_plan(tmp_path, "gpt-image-2.5-fixed", resolution="2K")
    estimate_command(
        job_id="saju",
        work_root=str(tmp_path),
        output_root=str(tmp_path),
        config_path=None,
        auto_approve=True,
        tts_provider="elevenlabs_tts",
        image_provider="poyo_ai",
        video_provider="poyo_ai",
        music_provider="",
        voice="instant",
    )

    data = json.loads((tmp_path / "saju" / "plan" / "cost_estimate.json").read_text())
    image_items = [i for i in data["line_items"] if i["task_type"] == "image"]
    assert len(image_items) == 1
    assert image_items[0]["unit_cost"] == 0.03
    assert image_items[0]["total_cost"] == 0.06  # 2 x $0.030


def test_estimate_prices_the_quality_the_plan_chose(tmp_path: Path) -> None:
    """A quality x resolution plan is quoted that matrix cell, not the base (#270).

    ``gpt-image-2`` is the default image model and bills 64 credits
    ($0.321) for ``quality: high`` at ``4K``; the registry held only the
    1K-low scalar, so a hero frame was approved at 3% of its bill.
    """
    from video_editor_cli.commands.estimate import estimate_command

    _write_two_scene_plan(tmp_path, "gpt-image-2", resolution="4K", quality="high")
    estimate_command(
        job_id="saju",
        work_root=str(tmp_path),
        output_root=str(tmp_path),
        config_path=None,
        auto_approve=True,
        tts_provider="elevenlabs_tts",
        image_provider="poyo_ai",
        video_provider="poyo_ai",
        music_provider="",
        voice="instant",
    )

    data = json.loads((tmp_path / "saju" / "plan" / "cost_estimate.json").read_text())
    image_items = [i for i in data["line_items"] if i["task_type"] == "image"]
    assert len(image_items) == 1
    assert image_items[0]["unit_cost"] == 0.321
    assert image_items[0]["total_cost"] == 0.642  # 2 x $0.321


def test_estimate_prices_a_reused_still_once(tmp_path: Path) -> None:
    """A scene showing another scene's still must not be billed twice (#271).

    ``saju-zodiac-animal`` shows five 2K stills across eight scenes by pointing
    the reused scenes at an owner via ``props.image_scene``; the gate emitted an
    image request per scene, so it quoted eight renders for a five-still job.
    """
    from video_editor_cli.commands.estimate import estimate_command

    work = tmp_path / "saju"
    plan_dir = work / "plan"
    plan_dir.mkdir(parents=True)
    props = {"image_model": "gpt-image-2.5-fixed", "image_resolution": "2K"}
    scenes = [
        {
            "id": scene_id,
            "type": "narration",
            "duration_seconds": 5.0,
            "narration": "Hello world.",
            "visual_description": "A neon-lit alley.",
            "asset_query": "",
            "props": {**props, **reuse},
            "assets": [],
        }
        for scene_id, reuse in (
            ("scene_001", {}),
            ("scene_002", {"image_scene": "scene_001"}),
            ("scene_003", {}),
            ("scene_004", {"image_scene": "scene_003"}),
        )
    ]
    plan = {
        "job_id": "saju",
        "script": "# Saju\n\nFour scenes.\n",
        "scenes": scenes,
        "aspect_ratio": "9:16",
        "music_mood": "",
        "music_path": "",
    }
    (plan_dir / "scene_plan.json").write_text(json.dumps(plan))

    estimate_command(
        job_id="saju",
        work_root=str(tmp_path),
        output_root=str(tmp_path),
        config_path=None,
        auto_approve=True,
        tts_provider="elevenlabs_tts",
        image_provider="poyo_ai",
        video_provider="poyo_ai",
        music_provider="",
        voice="instant",
    )

    data = json.loads((work / "plan" / "cost_estimate.json").read_text())
    image_items = [i for i in data["line_items"] if i["task_type"] == "image"]
    assert len(image_items) == 1
    assert image_items[0]["total_cost"] == 0.06  # 2 unique stills x $0.03


def test_estimate_prices_the_narration_once_not_per_scene(
    tmp_path: Path,
) -> None:
    """One TTS call for the whole script, however many scenes carry a tier.

    Grouping by the request's price params splits per-scene TTS requests into
    their own lines (each scene has its own duration), and each line priced the
    *whole* narration — the observed regression was eight lines at $0.023 and a
    $0.424 total for a $0.17 job. Sarvam bills per call, and the pipeline makes
    one call (#263), so the estimate must too.
    """
    from video_editor_cli.commands.estimate import estimate_command

    _write_two_scene_plan(tmp_path, "gpt-image-2.5-fixed", resolution="2K")
    estimate_command(
        job_id="saju",
        work_root=str(tmp_path),
        output_root=str(tmp_path),
        config_path=None,
        auto_approve=True,
        tts_provider="sarvam_tts",
        image_provider="poyo_ai",
        video_provider="poyo_ai",
        music_provider="",
        voice="instant",
    )

    data = json.loads((tmp_path / "saju" / "plan" / "cost_estimate.json").read_text())
    tts_items = [i for i in data["line_items"] if i["task_type"] == "tts"]
    assert len(tts_items) == 1
    assert tts_items[0]["total_cost"] == 0.023  # 24 chars -> one 1K block


def test_estimate_warns_about_an_unpriced_model(tmp_path: Path, capsys) -> None:
    """An unknown model must not pass the cost gate as $0.00 in silence."""
    from video_editor_cli.commands.estimate import estimate_command

    _write_two_scene_plan(tmp_path, "not-a-real-image-model")
    estimate_command(
        job_id="saju",
        work_root=str(tmp_path),
        output_root=str(tmp_path),
        config_path=None,
        auto_approve=True,
        tts_provider="elevenlabs_tts",
        image_provider="poyo_ai",
        video_provider="poyo_ai",
        music_provider="",
        voice="instant",
    )

    err = capsys.readouterr().err.lower()
    assert "not-a-real-image-model" in err
    assert "no price" in err
