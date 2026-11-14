"""Tests for the SkillsPipeline SFX auto-mix (Session U).

The SFX stage is the optional step between captions and music in the
v2 skills pipeline. When ``SkillsPipeline(sfx_provider=<name>)`` is
set, the pipeline:

  1. Reads the scene plan at ``<work>/plan/scene_plan.json``.
  2. Calls ``sfx_requests_for_scene`` once per scene to build the
     list of SFX asset requests.
  3. Resolves them through ``AssetIngestionManager`` (which honors
     the cache so re-runs are free).
  4. Copies the resolved clips to ``<work>/sfx/<scene_id>.mp3``.
  5. Calls ``mix_sfx_into_video`` with a timeline built from
     cumulative scene durations.
  6. Feeds the SFX-mixed video (``<work>/broll/with_broll_sfx.mp4``)
     into ``MusicSkill`` instead of the captions video, so the SFX
     survives the music mix.

When ``sfx_provider=""`` (the default), the SFX stage is a no-op
and the captions video flows straight into music.

These tests lock the orchestration contract end-to-end. The
``StubSFXProvider`` (offline FFmpeg ``lavfi`` noise burst) lets the
tests run without an ElevenLabs key.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_ffmpeg.broll import mix_sfx_into_video
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


def _make_silent_music(path: Path, duration: float = 1.0) -> Path:
    """Create a short silent music file so MusicSkill picks it up."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=44100:cl=mono:d={duration}",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _write_scene_plan(
    plan_path: Path,
    scenes: list[Scene],
    aspect_ratio: str = "16:9",
) -> Path:
    """Persist a ScenePlan with the given scenes to ``plan_path``."""
    plan = ScenePlan(
        job_id="pipeline-sfx-job",
        script="",
        scenes=scenes,
        aspect_ratio=aspect_ratio,
    )
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return plan_path


def _scenes_with_visual(*visual_descriptions: str) -> list[Scene]:
    """Build scenes with the given visual_description + a 3s duration each."""
    return [
        Scene(
            id=f"s{i + 1}",
            type="narrative",
            duration_seconds=3.0,
            visual_description=vd,
        )
        for i, vd in enumerate(visual_descriptions)
    ]


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="pipeline-sfx-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


@pytest.fixture
def transcript_with_filler(tmp_path: Path) -> Path:
    transcript = _build_transcript(
        [
            ("Hello", 0.0, 0.4),
            ("world", 0.5, 1.0),
        ]
    )
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(transcript), encoding="utf-8")
    return path


class TestSfxAutoMixDefault:
    """When ``sfx_provider`` is empty (the default), the SFX stage
    is a strict no-op — no SFX work runs, the captions video
    flows straight into MusicSkill."""

    def test_default_sfx_provider_is_no_op(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
    ) -> None:
        """``SkillsPipeline()`` with no sfx_provider leaves no SFX artifacts."""
        pipeline = SkillsPipeline(ctx)
        result = pipeline.run(
            tmp_media,
            transcript_path=transcript_with_filler,
        )

        # The sfx directory should not exist.
        assert not (ctx.work_dir / "sfx").exists()
        # The sfx-mixed video should not exist.
        assert not (ctx.work_dir / "broll" / "with_broll_sfx.mp4").exists()
        # The captions video is what flowed forward — it's the
        # only video the SFX stage could have fed to music, and
        # the music stage is the only consumer.
        assert Path(result["captions"]).exists()
        # ``sfx_input`` should equal ``captions`` when SFX is a no-op.
        assert result["sfx_input"] == result["captions"]


class TestSfxAutoMixOptIn:
    """When ``sfx_provider`` is set, the SFX stage generates per-scene
    clips and mixes them into the captions video."""

    def test_sfx_provider_generates_and_mixes(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
    ) -> None:
        """SFX clips are written to <work>/sfx/<scene_id>.mp3 and
        the mixed video is written to <work>/broll/with_broll_sfx.mp4."""
        # Pre-write a 2-scene plan with visual_descriptions.
        plan_path = ctx.work_dir / "plan" / "scene_plan.json"
        _write_scene_plan(
            plan_path,
            _scenes_with_visual(
                "A rolling green hill at sunrise",
                "A city skyline at night with twinkling lights",
            ),
        )

        pipeline = SkillsPipeline(ctx, sfx_provider="stub_sfx")
        result = pipeline.run(
            tmp_media,
            transcript_path=transcript_with_filler,
        )

        # Per-scene SFX clips were written.
        sfx_dir = ctx.work_dir / "sfx"
        assert sfx_dir.is_dir()
        assert (sfx_dir / "s1.mp3").exists()
        assert (sfx_dir / "s2.mp3").exists()
        assert (sfx_dir / "s1.mp3").stat().st_size > 0
        assert (sfx_dir / "s2.mp3").stat().st_size > 0

        # The sfx-mixed video was written.
        sfx_mixed = ctx.work_dir / "broll" / "with_broll_sfx.mp4"
        assert sfx_mixed.exists()
        assert sfx_mixed.stat().st_size > 0

        # The pipeline should report sfx_input == the mixed video,
        # not the captions video.
        assert result["sfx_input"] == str(sfx_mixed)
        # The captions video still exists (it's the input to the
        # SFX stage, not the output).
        assert Path(result["captions"]).exists()

    def test_sfx_provider_without_visual_descriptions_is_no_op(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
    ) -> None:
        """Scenes with empty visual_description produce no SFX requests
        and the captions video flows through unchanged."""
        # Pre-write a 2-scene plan with NO visual_descriptions.
        plan_path = ctx.work_dir / "plan" / "scene_plan.json"
        _write_scene_plan(
            plan_path,
            [
                Scene(
                    id=f"s{i + 1}",
                    type="narrative",
                    duration_seconds=3.0,
                    visual_description="",
                )
                for i in range(2)
            ],
        )

        pipeline = SkillsPipeline(ctx, sfx_provider="stub_sfx")
        result = pipeline.run(
            tmp_media,
            transcript_path=transcript_with_filler,
        )

        # No SFX artifacts.
        assert not (ctx.work_dir / "sfx").exists()
        assert not (ctx.work_dir / "broll" / "with_broll_sfx.mp4").exists()
        # Captions video was the SFX input (no SFX work happened).
        assert result["sfx_input"] == result["captions"]

    def test_sfx_provider_missing_scene_plan_warns_and_skips(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
    ) -> None:
        """When no scene plan exists, the SFX stage warns and passes
        the captions video through (does not raise)."""
        # Do NOT write a scene plan.
        pipeline = SkillsPipeline(ctx, sfx_provider="stub_sfx")
        result = pipeline.run(
            tmp_media,
            transcript_path=transcript_with_filler,
        )

        # Captions video was the SFX input.
        assert result["sfx_input"] == result["captions"]
        # No SFX artifacts.
        assert not (ctx.work_dir / "sfx").exists()
        assert not (ctx.work_dir / "broll" / "with_broll_sfx.mp4").exists()


class TestSfxTimelineOrdering:
    """The SFX timeline passed to ``mix_sfx_into_video`` is built
    from cumulative scene durations."""

    def test_sfx_timeline_starts_at_cumulative_scene_durations(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Spy on ``mix_sfx_into_video`` to capture the timeline.
        3 scenes with 2s, 3s, 4s durations -> timeline start times
        should be 0.0, 2.0, 5.0.
        """
        # Build a 3-scene plan with known durations.
        plan_path = ctx.work_dir / "plan" / "scene_plan.json"
        plan = ScenePlan(
            job_id="pipeline-sfx-job",
            script="",
            scenes=[
                Scene(
                    id="s1",
                    type="narrative",
                    duration_seconds=2.0,
                    visual_description="A forest at dawn",
                ),
                Scene(
                    id="s2",
                    type="narrative",
                    duration_seconds=3.0,
                    visual_description="A river flowing through mountains",
                ),
                Scene(
                    id="s3",
                    type="narrative",
                    duration_seconds=4.0,
                    visual_description="A bustling city square",
                ),
            ],
        )
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

        # Spy on mix_sfx_into_video to capture the timeline.
        captured: dict[str, Any] = {}

        def _spy_mix_sfx_into_video(**kwargs: Any) -> Path:
            captured["timeline"] = kwargs.get("sfx_timeline", [])
            captured["gain_db"] = kwargs.get("sfx_gain_db")
            # Still call the real implementation so downstream
            # stages (music, export) have a real file to consume.
            return mix_sfx_into_video(**kwargs)

        # Patch the symbol where the pipeline imported it from.
        monkeypatch.setattr(
            "video_editor_pipelines.pipeline.mix_sfx_into_video",
            _spy_mix_sfx_into_video,
        )

        pipeline = SkillsPipeline(
            ctx,
            sfx_provider="stub_sfx",
            sfx_gain_db=-12.0,
        )
        pipeline.run(
            tmp_media,
            transcript_path=transcript_with_filler,
        )

        timeline = captured.get("timeline")
        assert timeline is not None, "mix_sfx_into_video was not called"
        assert len(timeline) == 3, f"expected 3 entries; got {timeline!r}"

        # First entry starts at t=0, second at t=2.0 (s1.duration),
        # third at t=5.0 (s1.duration + s2.duration).
        paths, start_times = zip(*timeline, strict=True)
        assert start_times == (0.0, 2.0, 5.0), (
            f"timeline start_times should be cumulative; got {start_times!r}"
        )
        # The sfx_gain_db is threaded through.
        assert captured.get("gain_db") == -12.0

    def test_sfx_timeline_skips_scenes_without_visual_description(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scenes with no visual_description are excluded from the
        SFX timeline but their duration still advances the cursor.
        """
        plan_path = ctx.work_dir / "plan" / "scene_plan.json"
        plan = ScenePlan(
            job_id="pipeline-sfx-job",
            script="",
            scenes=[
                Scene(
                    id="s1",
                    type="narrative",
                    duration_seconds=2.0,
                    visual_description="A forest at dawn",
                ),
                # s2 has no visual — no SFX request, but the
                # timeline cursor still advances by 3.0s.
                Scene(
                    id="s2",
                    type="narrative",
                    duration_seconds=3.0,
                    visual_description="",
                ),
                Scene(
                    id="s3",
                    type="narrative",
                    duration_seconds=4.0,
                    visual_description="A bustling city square",
                ),
            ],
        )
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

        captured: dict[str, Any] = {}

        def _spy_mix_sfx_into_video(**kwargs: Any) -> Path:
            captured["timeline"] = kwargs.get("sfx_timeline", [])
            return mix_sfx_into_video(**kwargs)

        monkeypatch.setattr(
            "video_editor_pipelines.pipeline.mix_sfx_into_video",
            _spy_mix_sfx_into_video,
        )

        pipeline = SkillsPipeline(ctx, sfx_provider="stub_sfx")
        pipeline.run(
            tmp_media,
            transcript_path=transcript_with_filler,
        )

        timeline = captured.get("timeline")
        assert timeline is not None
        # Only s1 and s3 have SFX entries.
        assert len(timeline) == 2
        paths, start_times = zip(*timeline, strict=True)
        # s1 starts at 0; s3 starts at s1.duration + s2.duration = 5.0
        # (s2's 3.0s still advances the cursor even though no SFX).
        assert start_times == (0.0, 5.0), (
            f"timeline should advance through s2's duration; got {start_times!r}"
        )


class TestSfxHandsOffToGraph:
    """The graph render now folds music before SFX placement."""

    def test_sfx_mix_consumes_the_music_folded_graph(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        music_file = _make_silent_music(tmp_path / "music.wav", duration=1.0)
        captured: dict[str, Path] = {}

        async def fake_generate_and_mix(self: SkillsPipeline, input_video: Path) -> Path:
            captured["input"] = input_video
            return input_video

        monkeypatch.setattr(
            "video_editor_pipelines.pipeline.SkillsPipeline._generate_and_mix_sfx",
            fake_generate_and_mix,
        )

        pipeline = SkillsPipeline(
            ctx,
            music_path=str(music_file),
            sfx_provider="stub_sfx",
        )
        pipeline.run(
            tmp_media,
            transcript_path=transcript_with_filler,
        )

        assert captured["input"] == ctx.work_dir / "graph" / "composed.mp4"


class TestRunAsync:
    """Bug #52 regression: SkillsPipeline must be awaitable from an async
    caller without calling ``asyncio.run`` internally."""

    @pytest.mark.asyncio
    async def test_run_async_does_not_use_asyncio_run(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When awaited, ``run_async`` should not spawn a nested event loop."""
        import asyncio

        plan_path = ctx.work_dir / "plan" / "scene_plan.json"
        _write_scene_plan(
            plan_path,
            _scenes_with_visual(
                "A rolling green hill at sunrise",
                "A city skyline at night with twinkling lights",
            ),
        )

        pipeline = SkillsPipeline(ctx, sfx_provider="stub_sfx")

        def _forbidden_run(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("asyncio.run called inside an async context")

        monkeypatch.setattr(asyncio, "run", _forbidden_run)

        result = await pipeline.run_async(
            tmp_media,
            transcript_path=transcript_with_filler,
        )

        sfx_mixed = ctx.work_dir / "broll" / "with_broll_sfx.mp4"
        assert Path(result["sfx_input"]) == sfx_mixed
        assert sfx_mixed.exists()
        assert sfx_mixed.stat().st_size > 0
