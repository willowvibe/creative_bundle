"""Tests for the skills-first end-to-end pipeline."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from rough_cut.rough_cut import RoughCutSkill
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_ffmpeg.probe import probe_media
from video_editor_pipelines import pipeline as pipeline_module
from video_editor_pipelines.pipeline import SkillsPipeline
from video_editor_state.job import JobContext


def _build_transcript(words: list[tuple[str, float, float]]) -> dict[str, object]:
    return {
        "source_audio": "",
        "language": "en",
        "language_probability": 1.0,
        "word_timestamps": True,
        "segments": [],
        "words": [{"word": w, "start": s, "end": e, "probability": 0.95} for w, s, e in words],
    }


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="pipeline-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


@pytest.fixture
def transcript_with_filler(tmp_path: Path) -> Path:
    transcript = _build_transcript(
        [
            ("Hello", 0.0, 0.4),
            ("um", 0.5, 0.7),
            ("world", 0.8, 1.2),
            ("pause", 2.0, 2.5),
            ("Again", 3.0, 3.4),
        ]
    )
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(transcript), encoding="utf-8")
    return path


def test_skills_pipeline_runs_without_music(
    tmp_media: Path,
    ctx: JobContext,
    transcript_with_filler: Path,
) -> None:
    """Run rough-cut → graphics → captions → export without a music file."""
    pipeline = SkillsPipeline(ctx)
    # The repo config names an installed default bed (``audio.default_music_path``
    # → ``assets/music/default.mp3``) and a shared music *directory*
    # (``paths.music``); ``__init__`` resolves the former into ``music_path``.
    # This test is about the no-music path, so it must inherit neither: a
    # checkout with the bed installed would otherwise mix it in.
    pipeline.music_path = None
    pipeline.music_prompt = ""
    pipeline.project_config.audio.default_music_path = ""
    pipeline.project_config.paths.music = ""
    result = pipeline.run(tmp_media, transcript_path=transcript_with_filler)

    assert Path(result["locked"]).exists()
    assert Path(result["graphics"]).exists()
    assert Path(result["captions"]).exists()
    assert result["music"] is None
    assert Path(result["final"]).exists()
    assert result["final"].endswith("final.mp4")

    # Export artifacts were written.
    assert (ctx.output_dir / "report.md").exists()
    assert (ctx.output_dir / "decision_log.jsonl").exists()


def test_skills_pipeline_uses_custom_output_name(
    tmp_media: Path,
    ctx: JobContext,
    transcript_with_filler: Path,
) -> None:
    """The --output-name option is honored by the export stage."""
    pipeline = SkillsPipeline(ctx)
    result = pipeline.run(
        tmp_media,
        transcript_path=transcript_with_filler,
        output_name="deliverable.mp4",
    )

    assert result["final"].endswith("deliverable.mp4")
    assert Path(result["final"]).exists()


def test_skills_pipeline_resumes_completed_stages(
    tmp_media: Path,
    tmp_path: Path,
    transcript_with_filler: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #78 regression: completed stages are skipped on re-run so the
    pipeline resumes instead of starting from scratch.
    """
    ctx = JobContext(
        job_id="resume-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    first = SkillsPipeline(ctx).run(tmp_media, transcript_path=transcript_with_filler)
    assert Path(first["final"]).exists()

    class _RaisingRoughCutSkill(RoughCutSkill):
        def run(self, *args: object, **kwargs: object) -> Path:
            raise AssertionError("rough_cut stage should have been skipped on resume")

    monkeypatch.setattr(pipeline_module, "RoughCutSkill", _RaisingRoughCutSkill)

    second = SkillsPipeline(ctx).run(tmp_media, transcript_path=transcript_with_filler)
    assert Path(second["final"]).exists()
    assert second["locked"] == first["locked"]


def test_skills_pipeline_injects_the_configured_assets(
    ctx: JobContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for Issue B: ``ve run --config`` must reach asset providers.

    The pipeline now resolves one ``Settings`` object at construction and
    injects its ``assets`` section into every manager; it must not silently
    re-read the default config for a later music/SFX call.
    """
    custom_config = tmp_path / "custom.yaml"
    custom_config.write_text("assets:\n  strict: false\n", encoding="utf-8")

    pipeline = SkillsPipeline(
        ctx,
        music_prompt="upbeat intro",
        music_provider="elevenlabs_music",
        config_path=custom_config,
    )

    captured: dict[str, Any] = {}

    class _FakeManager(AssetIngestionManager):
        def __init__(
            self,
            ctx_arg: JobContext,
            config: Any = None,
            cache_dir: Path | str | None = None,
            budget: Any = None,
            final: bool = False,
        ) -> None:
            captured["config"] = config

        async def resolve(self, requests: list[Any]) -> list[Any]:
            class _R:
                def __init__(self, p: Path):
                    self.local_path = str(p)
                    self.duration_ms = 1
                    self.scene_id = "music"

            return [_R(tmp_path / "fake_music.mp3")]

    (tmp_path / "fake_music.mp3").write_bytes(b"fake")
    monkeypatch.setattr(pipeline_module, "AssetIngestionManager", _FakeManager)

    asyncio.run(pipeline._generate_music())

    config = captured.get("config")
    assert config is not None
    assert config is pipeline.settings.config.assets
    assert config.strict is False


def test_skills_pipeline_cache_dir_contained_in_work_dir(
    ctx: JobContext,
    tmp_path: Path,
) -> None:
    """A ``render.cache_dir`` that escapes the job work dir is contained."""
    import yaml

    config = tmp_path / "escape_cache.yaml"
    config.write_text(
        yaml.safe_dump({"render": {"cache_dir": str(tmp_path / "shared_cache")}}),
        encoding="utf-8",
    )
    pipeline = SkillsPipeline(ctx, config_path=config)
    assert pipeline.cache_dir == ctx.work_dir.resolve() / ".cache"


def test_skills_pipeline_cache_dir_relative_to_work_dir(
    ctx: JobContext,
    tmp_path: Path,
) -> None:
    """A repo-relative ``render.cache_dir`` is resolved inside the work dir."""
    import yaml

    config = tmp_path / "relative_cache.yaml"
    config.write_text(
        yaml.safe_dump({"render": {"cache_dir": "projects/cache"}}),
        encoding="utf-8",
    )
    pipeline = SkillsPipeline(ctx, config_path=config)
    assert pipeline.cache_dir == ctx.work_dir.resolve() / "projects" / "cache"


def test_skills_pipeline_output_name_template_rejects_traversal(
    ctx: JobContext,
    tmp_path: Path,
) -> None:
    """A malicious ``render.output_name_template`` cannot escape ``output_dir``."""
    import yaml

    config = tmp_path / "bad_template.yaml"
    config.write_text(
        yaml.safe_dump({"render": {"output_name_template": "../../{stage}.mp4"}}),
        encoding="utf-8",
    )
    pipeline = SkillsPipeline(ctx, config_path=config)
    with pytest.raises(ValueError, match="plain filename"):
        pipeline._safe_output_name(
            pipeline.project_config.render.output_name_template.replace("{stage}", "final")
        )


def test_skills_pipeline_applies_render_config_defaults(
    tmp_media: Path,
    ctx: JobContext,
    tmp_path: Path,
    transcript_with_filler: Path,
) -> None:
    """Bug #187/#188: config render.* settings are consumed end-to-end.

    A custom config changes the video codec, frame rate, and audio codec.
    The final exported video must reflect those choices, not the historical
    hardcoded defaults, proving that ``RenderConfig`` flows through the
    skills into the FFmpeg commands.
    """
    import yaml

    config = tmp_path / "render_config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "render": {
                    "video_codec": "libx265",
                    "audio_codec": "aac",
                    "fps": 24,
                    "crf": 28,
                }
            }
        ),
        encoding="utf-8",
    )

    # Keep the real presets directory so the graphics stage has a brand/preset
    # to load even though the config file lives in a temp repo root.
    repo_presets = Path(__file__).resolve().parents[1] / "presets"
    # Force the FFmpeg engine: this test asserts that RenderConfig reaches
    # FFmpeg, while the HyperFrames path has its own codec/container contract.
    pipeline = SkillsPipeline(
        ctx,
        config_path=config,
        presets_dir=repo_presets,
        render_engine="ffmpeg",
    )
    result = pipeline.run(tmp_media, transcript_path=transcript_with_filler)

    final = Path(result["final"])
    assert final.exists()
    info = probe_media(final)
    assert info.video_stream is not None
    assert info.audio_stream is not None
    assert info.video_stream.get("codec_name") == "hevc"
    assert round(info.fps) == 24
    assert info.audio_stream.get("codec_name") == "aac"


def test_skills_pipeline_render_options_without_config_use_defaults(
    tmp_media: Path,
    ctx: JobContext,
    transcript_with_filler: Path,
) -> None:
    """When no config is supplied, the pipeline keeps historical defaults."""
    pipeline = SkillsPipeline(ctx)
    result = pipeline.run(tmp_media, transcript_path=transcript_with_filler)

    final = Path(result["final"])
    info = probe_media(final)
    assert info.video_stream is not None
    assert info.audio_stream is not None
    assert info.video_stream.get("codec_name") == "h264"
    assert round(info.fps) == 30
    assert info.audio_stream.get("codec_name") == "aac"
