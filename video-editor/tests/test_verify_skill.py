"""Tests for the requirements verification gate skill."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from verify.deterministic import (
    check_aspect_ratio,
    check_duration,
    check_filler_words,
    check_forbidden_element,
    check_must_keep_segments,
    check_must_mention,
    check_required_element,
)
from verify.models import ContentConstraints, DurationRange, RequirementsSpec
from verify.verify import VerificationError, VerifySkill
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg


def _make_video(path: Path, duration: float = 1.0, size: str = "320x240") -> Path:
    """Create a minimal video with audio for verification tests."""
    _require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={size}:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=1000:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="verify-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


class TestDeterministicChecks:
    """Unit tests for deterministic requirement checks."""

    def test_duration_check_pass(self, tmp_path: Path) -> None:
        video = _make_video(tmp_path / "video.mp4", duration=2.0)
        from video_editor_ffmpeg.probe import probe_media

        result, reason, actual = check_duration(probe_media(video), 1.0, 3.0)
        assert result == "PASS"
        assert actual == pytest.approx(2.0, abs=0.1)
        assert "within range" in reason

    def test_duration_check_fail_too_long(self, tmp_path: Path) -> None:
        video = _make_video(tmp_path / "video.mp4", duration=2.0)
        from video_editor_ffmpeg.probe import probe_media

        result, reason, actual = check_duration(probe_media(video), 0.0, 1.0)
        assert result == "FAIL"
        assert actual == pytest.approx(2.0, abs=0.1)
        assert "max" in reason

    def test_aspect_ratio_check_pass(self, tmp_path: Path) -> None:
        video = _make_video(tmp_path / "video.mp4", size="320x240")
        from video_editor_ffmpeg.probe import probe_media

        result, reason, actual = check_aspect_ratio(probe_media(video), "4:3")
        assert result == "PASS"
        assert actual == pytest.approx(4 / 3, abs=0.01)

    def test_aspect_ratio_check_fail(self, tmp_path: Path) -> None:
        video = _make_video(tmp_path / "video.mp4", size="320x240")
        from video_editor_ffmpeg.probe import probe_media

        result, reason, actual = check_aspect_ratio(probe_media(video), "16:9")
        assert result == "FAIL"
        assert actual == pytest.approx(4 / 3, abs=0.01)

    def test_required_element_captions_found(self, ctx: JobContext, tmp_path: Path) -> None:
        _make_video(tmp_path / "with_captions.mp4")
        ctx.artifact("captions", "with_captions.mp4").write_bytes(
            (tmp_path / "with_captions.mp4").read_bytes()
        )
        from video_editor_ffmpeg.probe import probe_media

        result, reason, actual = check_required_element(
            "captions", ctx.work_dir, probe_media(tmp_path / "with_captions.mp4")
        )
        assert result == "PASS"
        assert actual is True

    def test_required_element_music_missing(self, ctx: JobContext, tmp_path: Path) -> None:
        video = _make_video(tmp_path / "video.mp4")
        from video_editor_ffmpeg.probe import probe_media

        result, reason, actual = check_required_element("music", ctx.work_dir, probe_media(video))
        assert result == "FAIL"
        assert actual is False

    def test_forbidden_element_background_music_missing(
        self, ctx: JobContext, tmp_path: Path
    ) -> None:
        video = _make_video(tmp_path / "video.mp4")
        from video_editor_ffmpeg.probe import probe_media

        result, reason, actual = check_forbidden_element(
            "background_music", ctx.work_dir, probe_media(video)
        )
        assert result == "PASS"
        assert actual is False

    def test_must_mention_pass(self) -> None:
        transcript = [{"word": "hello"}, {"word": "world"}]
        result, reason, actual = check_must_mention(["hello"], transcript)
        assert result == "PASS"
        assert actual == ["hello"]

    def test_must_mention_fail(self) -> None:
        transcript = [{"word": "hello"}]
        result, reason, actual = check_must_mention(["world"], transcript)
        assert result == "FAIL"
        assert "world" in actual

    def test_filler_words_detected(self) -> None:
        transcript = [{"word": "um"}, {"word": "hello"}, {"word": "um"}]
        result, reason, actual = check_filler_words(transcript)
        assert result == "FAIL"
        assert actual == ["um"]

    def test_filler_words_none(self) -> None:
        transcript = [{"word": "hello"}, {"word": "world"}]
        result, reason, actual = check_filler_words(transcript)
        assert result == "PASS"
        assert actual == []

    def test_must_keep_segments_pass(self) -> None:
        plan = {"scenes": [{"id": "a"}, {"id": "b"}]}
        result, reason, actual = check_must_keep_segments(["a"], plan)
        assert result == "PASS"

    def test_must_keep_segments_fail(self) -> None:
        plan = {"scenes": [{"id": "a"}]}
        result, reason, actual = check_must_keep_segments(["b"], plan)
        assert result == "FAIL"


class TestVerifySkill:
    """Integration tests for the VerifySkill orchestrator."""

    @pytest.mark.asyncio
    async def test_run_passes_when_requirements_met(self, ctx: JobContext, tmp_path: Path) -> None:
        video = _make_video(tmp_path / "final.mp4", duration=2.0, size="320x240")
        ctx.output_dir.mkdir(parents=True, exist_ok=True)
        (ctx.output_dir / "final.mp4").write_bytes(video.read_bytes())
        ctx.manifest().stage_done("export", {"final_video": str(ctx.output_dir / "final.mp4")})

        spec = RequirementsSpec(
            job_id=ctx.job_id,
            target_duration_range=DurationRange(min=1.0, max=3.0),
            aspect_ratio="4:3",
        )
        skill = VerifySkill(ctx, requirements=spec)
        report = await skill.run()
        assert report.overall == "PASS"
        assert (ctx.work_dir / "verify" / "verification_report.json").exists()

    @pytest.mark.asyncio
    async def test_run_raises_when_duration_requirement_fails(
        self, ctx: JobContext, tmp_path: Path
    ) -> None:
        video = _make_video(tmp_path / "final.mp4", duration=2.0, size="320x240")
        ctx.output_dir.mkdir(parents=True, exist_ok=True)
        (ctx.output_dir / "final.mp4").write_bytes(video.read_bytes())
        ctx.manifest().stage_done("export", {"final_video": str(ctx.output_dir / "final.mp4")})

        spec = RequirementsSpec(
            job_id=ctx.job_id,
            target_duration_range=DurationRange(min=0.0, max=1.0),
        )
        skill = VerifySkill(ctx, requirements=spec)
        with pytest.raises(VerificationError):
            await skill.run()

    @pytest.mark.asyncio
    async def test_run_uses_persisted_requirements(self, ctx: JobContext, tmp_path: Path) -> None:
        video = _make_video(tmp_path / "final.mp4", duration=2.0)
        ctx.output_dir.mkdir(parents=True, exist_ok=True)
        (ctx.output_dir / "final.mp4").write_bytes(video.read_bytes())
        ctx.manifest().stage_done("export", {"final_video": str(ctx.output_dir / "final.mp4")})

        spec = RequirementsSpec(
            job_id=ctx.job_id,
            target_duration_range=DurationRange(min=1.0, max=3.0),
        )
        skill = VerifySkill(ctx, requirements=spec)
        skill.save_requirements(spec)

        fresh = VerifySkill(ctx)
        report = await fresh.run()
        assert report.overall == "PASS"

    def test_a_corrupt_requirements_file_raises(self, ctx: JobContext) -> None:
        """#309: do not report a corrupt spec as 'none found'."""
        path = ctx.work_dir / "verify" / "requirements.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(VerificationError, match="requirements.json is invalid"):
            VerifySkill(ctx).load_requirements()

    @pytest.mark.asyncio
    async def test_run_with_must_mention(self, ctx: JobContext, tmp_path: Path) -> None:
        video = _make_video(tmp_path / "final.mp4", duration=1.0)
        ctx.output_dir.mkdir(parents=True, exist_ok=True)
        (ctx.output_dir / "final.mp4").write_bytes(video.read_bytes())
        ctx.artifact("transcript", "transcript.json").write_text(
            json.dumps([{"word": "subscribe"}, {"word": "now"}]), encoding="utf-8"
        )
        ctx.manifest().stage_done("export", {"final_video": str(ctx.output_dir / "final.mp4")})

        spec = RequirementsSpec(
            job_id=ctx.job_id,
            content_constraints=ContentConstraints(must_mention=["subscribe"]),
        )
        skill = VerifySkill(ctx, requirements=spec)
        report = await skill.run()
        assert report.overall == "PASS"
        assert any(c.requirement == "must_mention" and c.result == "PASS" for c in report.checks)

    @pytest.mark.asyncio
    async def test_run_with_judge_checklist_mock_router(
        self, ctx: JobContext, tmp_path: Path
    ) -> None:
        video = _make_video(tmp_path / "final.mp4", duration=1.0)
        ctx.output_dir.mkdir(parents=True, exist_ok=True)
        (ctx.output_dir / "final.mp4").write_bytes(video.read_bytes())
        ctx.manifest().stage_done("export", {"final_video": str(ctx.output_dir / "final.mp4")})

        class _FakeRouter:
            async def generate(self, **kwargs: Any) -> str:
                return json.dumps(
                    {
                        "item_checks": [
                            {"item": "quality", "result": "PASS", "reason": "looks fine"}
                        ],
                        "overall": "PASS",
                        "summary": "all good",
                    }
                )

        spec = RequirementsSpec(
            job_id=ctx.job_id,
            judge_checklist=["quality"],
        )
        skill = VerifySkill(ctx, requirements=spec, router=_FakeRouter())  # type: ignore[arg-type]
        report = await skill.run()
        assert report.overall == "PASS"
        judge = next(c for c in report.checks if c.requirement == "llm_judge")
        assert judge.result == "PASS"

    @pytest.mark.asyncio
    async def test_run_with_judge_checklist_unsure_on_bad_json(
        self, ctx: JobContext, tmp_path: Path
    ) -> None:
        video = _make_video(tmp_path / "final.mp4", duration=1.0)
        ctx.output_dir.mkdir(parents=True, exist_ok=True)
        (ctx.output_dir / "final.mp4").write_bytes(video.read_bytes())
        ctx.manifest().stage_done("export", {"final_video": str(ctx.output_dir / "final.mp4")})

        class _FakeRouter:
            async def generate(self, **kwargs: Any) -> str:
                return "not json"

        spec = RequirementsSpec(
            job_id=ctx.job_id,
            judge_checklist=["quality"],
        )
        skill = VerifySkill(ctx, requirements=spec, router=_FakeRouter())  # type: ignore[arg-type]
        report = await skill.run()
        assert report.overall == "NEEDS_REVIEW"
        judge = next(c for c in report.checks if c.requirement == "llm_judge")
        assert judge.result == "UNSURE"
