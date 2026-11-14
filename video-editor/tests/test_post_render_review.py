"""Tests for the post-render self-review / quality gate."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from export.export import ExportSkill
from review.review import PostRenderReviewError, PostRenderReviewSkill
from typer.testing import CliRunner
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg


def _make_video(
    path: Path, *, duration: float = 3.0, silent: bool = False, black: bool = False
) -> Path:
    """Render a synthetic video for review tests."""
    _require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    size = "640x360"
    if black:
        video_src = f"color=c=black:s={size}:d={duration}"
    else:
        video_src = f"testsrc=duration={duration}:size={size}:rate=30"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        video_src,
    ]
    if not silent:
        cmd.extend(["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"])
    cmd.extend(
        [
            "-pix_fmt",
            "yuv420p",
            "-t",
            str(duration),
            str(path),
        ]
    )
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    """A job context with prepared work/output directories."""
    return JobContext(
        job_id="review-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


@pytest.fixture
def final_video(ctx: JobContext) -> Path:
    """A valid final video with audio and picture."""
    final = ctx.output_dir / "final.mp4"
    ctx.ensure_dirs()
    return _make_video(final)


def test_review_passes_on_valid_video(ctx: JobContext, final_video: Path) -> None:
    """A normal exported video should pass all critical checks."""
    ctx.manifest().stage_done("export", {"final_video": str(final_video)})
    skill = PostRenderReviewSkill(ctx, loudness_target_lufs=-22.0)
    report = skill.run(final_video)

    assert report.overall == "PASS"
    assert report.passed is True
    critical = [c for c in report.checks if c.category == "critical"]
    assert all(c.result == "PASS" for c in critical)

    # The report is persisted to the work directory.
    report_path = ctx.work_dir / "review" / "review_report.json"
    assert report_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["overall"] == "PASS"
    assert data["job_id"] == ctx.job_id


def test_review_records_stage_in_manifest(ctx: JobContext, final_video: Path) -> None:
    """The review skill marks the manifest stage as complete."""
    ctx.manifest().stage_done("export", {"final_video": str(final_video)})
    skill = PostRenderReviewSkill(ctx, loudness_target_lufs=-22.0)
    skill.run(final_video)

    assert ctx.manifest().is_stage_complete("review")
    outputs = ctx.manifest().stage_outputs("review")
    assert outputs["overall"] == "PASS"


def test_review_fails_when_video_has_no_audio(ctx: JobContext) -> None:
    """A video with no audio stream fails the critical has_audio_stream check."""
    silent = ctx.output_dir / "silent.mp4"
    _make_video(silent, silent=True)

    skill = PostRenderReviewSkill(ctx)
    with pytest.raises(PostRenderReviewError) as exc_info:
        skill.run(silent)
    assert "has_audio_stream" in str(exc_info.value)


def test_review_fails_on_black_frames(ctx: JobContext) -> None:
    """A black video fails the critical no_black_frames check."""
    black = ctx.output_dir / "black.mp4"
    _make_video(black, black=True, silent=True)

    skill = PostRenderReviewSkill(ctx)
    with pytest.raises(PostRenderReviewError) as exc_info:
        skill.run(black)
    assert "no_black_frames" in str(exc_info.value)


def test_review_uses_exported_final_by_default(ctx: JobContext) -> None:
    """When no path is supplied, the skill discovers the exported final video."""
    final = ctx.output_dir / "final.mp4"
    ctx.ensure_dirs()
    _make_video(final)
    ctx.manifest().stage_done("export", {"final_video": str(final)})

    skill = PostRenderReviewSkill(ctx, loudness_target_lufs=-22.0)
    report = skill.run()
    assert Path(report.video_path) == final
    assert report.overall == "PASS"


@pytest.fixture
def cli_runner() -> CliRunner:
    """Standard typer CLI runner."""
    return CliRunner()


def test_export_report_appends_review_section(ctx: JobContext, final_video: Path) -> None:
    """ExportSkill.append_post_render_review updates the Markdown report."""
    # Build a minimal report by running the export skill on the final video.
    ctx.ensure_dirs()
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    music_input = ctx.work_dir / "music" / "with_music.mp4"
    music_input.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(final_video, music_input)
    export_skill = ExportSkill(ctx)
    export_skill.run()

    report_path = ctx.output_dir / "report.md"
    report_before = report_path.read_text(encoding="utf-8")
    assert "Post-Render Review" not in report_before

    review_skill = PostRenderReviewSkill(ctx, loudness_target_lufs=-22.0)
    review_skill.run(final_video)
    export_skill.append_post_render_review()

    report_after = report_path.read_text(encoding="utf-8")
    assert "## Post-Render Review" in report_after
    assert "PASS" in report_after

    decision_log = ctx.output_dir / "decision_log.jsonl"
    assert decision_log.exists()
    decisions = [json.loads(line) for line in decision_log.read_text(encoding="utf-8").splitlines()]
    review_decisions = [d for d in decisions if d.get("operation") == "post_render_review"]
    assert len(review_decisions) == 1
    assert review_decisions[0]["outputs"]["overall"] == "PASS"


def test_cli_review_command_reports_result(cli_runner: CliRunner, tmp_path: Path) -> None:
    """``ve review <job-id>`` runs the gate on an existing final.mp4."""
    from video_editor_cli.main import app

    work_root = tmp_path / "work"
    output_root = tmp_path / "output"
    final = output_root / "review-cli" / "final.mp4"
    _make_video(final)

    result = cli_runner.invoke(
        app,
        [
            "review",
            "review-cli",
            "--work-root",
            str(work_root),
            "--output-root",
            str(output_root),
            "--loudness-target-lufs",
            "-22.0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Post-render review: PASS" in result.output


def test_cli_export_skips_review_when_requested(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ve export --skip-review`` does not invoke PostRenderReviewSkill."""
    from video_editor_cli import main
    from video_editor_cli.main import app

    calls: list[Any] = []

    class _NoopReview:
        def __init__(self, ctx: JobContext, **kwargs: Any) -> None:
            calls.append(kwargs)

        def run(self, video_path: Path | str | None = None) -> Any:
            raise AssertionError("review should not run with --skip-review")

    monkeypatch.setattr(main, "PostRenderReviewSkill", _NoopReview)

    work_root = tmp_path / "work"
    output_root = tmp_path / "output"
    job_id = "skip-review"
    (work_root / job_id / "music").mkdir(parents=True)
    final_input = work_root / job_id / "music" / "with_music.mp4"
    _make_video(final_input)

    result = cli_runner.invoke(
        app,
        [
            "export",
            job_id,
            "--skip-review",
            "--work-root",
            str(work_root),
            "--output-root",
            str(output_root),
        ],
    )
    assert result.exit_code == 0, result.output
    assert not calls
    assert "Final export:" in result.output


def test_cli_run_help_lists_review_flags(cli_runner: CliRunner) -> None:
    """The ``ve run`` command exposes --review / --skip-review."""
    from video_editor_cli.main import app

    result = cli_runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0, result.output
    assert "--review" in result.output
    assert "--skip-review" in result.output
