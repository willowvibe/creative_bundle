"""Tests for the export skill."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from export.export import DEFAULT_INPUT_SEARCH, ExportSkill
from export.models import ExportStyle
from video_editor_ffmpeg.probe import probe_media
from video_editor_state.job import JobContext
from video_editor_state.project_brief import ProjectBrief, write_project_brief

from tests.conftest import _require_ffmpeg


def _make_video(path: Path, duration: float = 1.0) -> Path:
    """Create a minimal video with audio for export tests."""
    _require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=320x240:d={duration}",
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
        job_id="export-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


def test_export_copies_music_video(ctx: JobContext, tmp_path: Path) -> None:
    video = _make_video(tmp_path / "with_music.mp4")
    ctx.artifact("music", "with_music.mp4").write_bytes(video.read_bytes())

    skill = ExportSkill(ctx)
    final = skill.run()

    assert final.exists()
    assert final == ctx.output_dir / "final.mp4"
    assert probe_media(final).duration == pytest.approx(probe_media(video).duration, abs=0.1)


def test_export_falls_back_to_captions_video(ctx: JobContext, tmp_path: Path) -> None:
    video = _make_video(tmp_path / "with_captions.mp4")
    ctx.artifact("captions", "with_captions.mp4").write_bytes(video.read_bytes())

    skill = ExportSkill(ctx)
    final = skill.run()

    assert final.exists()


def test_export_generates_report_and_decision_log(ctx: JobContext, tmp_path: Path) -> None:
    video = _make_video(tmp_path / "locked.mp4")
    ctx.artifact("rough_cut", "locked.mp4").write_bytes(video.read_bytes())

    ctx.record("rough_cut", {"source": "raw.mp4"}, {"locked": str(video)})
    ctx.record("graphics", {"preset": "short_explainer"}, {"video": "with_graphics.mp4"})

    skill = ExportSkill(ctx)
    skill.run()

    report_path = ctx.output_dir / "report.md"
    decision_log_path = ctx.output_dir / "decision_log.jsonl"
    assert report_path.exists()
    assert decision_log_path.exists()

    report = report_path.read_text(encoding="utf-8")
    assert f"# Export Report — {ctx.job_id}" in report
    assert "rough_cut" in report
    assert "graphics" in report

    lines = decision_log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    decisions = [json.loads(line) for line in lines]
    assert {d["operation"] for d in decisions} == {"rough_cut", "graphics"}


def test_export_manifest_saved(ctx: JobContext, tmp_path: Path) -> None:
    video = _make_video(tmp_path / "locked.mp4")
    ctx.artifact("rough_cut", "locked.mp4").write_bytes(video.read_bytes())

    skill = ExportSkill(ctx, style=ExportStyle(output_name="deliverable.mp4"))
    skill.run()

    manifest_path = ctx.work_dir / "export" / "export.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["job_id"] == ctx.job_id
    assert data["final_video"].endswith("deliverable.mp4")


def test_export_raises_when_no_input(ctx: JobContext) -> None:
    skill = ExportSkill(ctx)
    with pytest.raises(FileNotFoundError):
        skill.run()


def test_default_input_search_prefers_render_graph_output() -> None:
    assert DEFAULT_INPUT_SEARCH[0] == "graph/composed.mp4"
    assert DEFAULT_INPUT_SEARCH[-1] == "rough_cut/locked.mp4"
    # Later stages beat earlier ones: music > captions > graphics > b-roll.
    assert DEFAULT_INPUT_SEARCH.index("captions/with_captions.mp4") < DEFAULT_INPUT_SEARCH.index(
        "broll/with_broll.mp4"
    )
    assert DEFAULT_INPUT_SEARCH.index("graphics/with_graphics.mp4") < DEFAULT_INPUT_SEARCH.index(
        "broll/with_broll.mp4"
    )


def test_export_uses_explicit_input_video(ctx: JobContext, tmp_path: Path) -> None:
    """An explicit input wins over every search path.

    The skills pipeline renders its deliverable to ``graph/composed.mp4``
    and must hand that file to export; otherwise export falls through to
    the untouched ``rough_cut/locked.mp4``.
    """
    locked = _make_video(tmp_path / "locked.mp4", duration=1.0)
    music = _make_video(tmp_path / "with_music.mp4", duration=1.5)
    composed = _make_video(tmp_path / "composed.mp4", duration=2.0)
    ctx.artifact("rough_cut", "locked.mp4").write_bytes(locked.read_bytes())
    ctx.artifact("music", "with_music.mp4").write_bytes(music.read_bytes())
    explicit = tmp_path / "explicit" / "deliverable.mp4"
    explicit.parent.mkdir(parents=True, exist_ok=True)
    explicit.write_bytes(composed.read_bytes())

    final = ExportSkill(ctx).run(input_video=explicit)

    assert final.read_bytes() == composed.read_bytes()


def test_export_report_includes_project_brief(ctx: JobContext, tmp_path: Path) -> None:
    """When the job carries a ProjectBrief, the report.md includes a
    "Project brief" section with the brief's title, audience, tone,
    target duration, and key segments. When the brief is absent the
    section is omitted — the report must not gain a "no brief" stub."""
    video = _make_video(tmp_path / "locked.mp4")
    ctx.artifact("rough_cut", "locked.mp4").write_bytes(video.read_bytes())

    # Provide a brief via ctx. ctx.brief now exposes it.
    brief = ProjectBrief(
        job_id=ctx.job_id,
        title="Saju Reel",
        audience="Curious newcomers",
        tone="Calm, mystical",
        target_duration_s=60.0,
        key_segments=["hook", "four pillars", "closing"],
        notes="Keep captions legible.",
        brand="saju",
    )
    ctx.project_brief = brief

    skill = ExportSkill(ctx)
    skill.run()

    report = (ctx.output_dir / "report.md").read_text(encoding="utf-8")
    assert "## Project Brief" in report
    assert "Saju Reel" in report
    assert "Curious newcomers" in report
    assert "Calm, mystical" in report
    assert "60.0s" in report
    assert "hook" in report
    assert "four pillars" in report
    assert "closing" in report
    assert "saju" in report


def test_export_report_includes_project_brief_loaded_from_workdir(
    ctx: JobContext, tmp_path: Path
) -> None:
    """A project.md dropped into <work_dir>/project.md is auto-loaded
    by ctx.load_brief_from_workdir() and shows up in the report."""
    video = _make_video(tmp_path / "locked.mp4")
    ctx.artifact("rough_cut", "locked.mp4").write_bytes(video.read_bytes())
    ctx.ensure_dirs()
    write_project_brief(
        ctx.work_dir,
        ProjectBrief(
            job_id=ctx.job_id,
            title="Loaded From Disk",
            audience="Disk reader",
            notes="Body note",
        ),
    )
    ctx.load_brief_from_workdir()

    skill = ExportSkill(ctx)
    skill.run()

    report = (ctx.output_dir / "report.md").read_text(encoding="utf-8")
    assert "Loaded From Disk" in report
    assert "Disk reader" in report


def test_export_report_omits_brief_section_when_absent(ctx: JobContext, tmp_path: Path) -> None:
    """When no brief is present (ctx.brief is None AND no project.md
    exists), the report does not include a "Project brief" section.
    We do not emit an empty stub."""
    video = _make_video(tmp_path / "locked.mp4")
    ctx.artifact("rough_cut", "locked.mp4").write_bytes(video.read_bytes())

    skill = ExportSkill(ctx)
    skill.run()

    report = (ctx.output_dir / "report.md").read_text(encoding="utf-8")
    assert "## Project Brief" not in report


def test_export_decision_log_falls_back_to_stage_summaries(ctx: JobContext, tmp_path: Path) -> None:
    """When no explicit operations are recorded, the decision log and
    report still reflect completed pipeline stages (C23)."""
    video = _make_video(tmp_path / "locked.mp4")
    ctx.artifact("rough_cut", "locked.mp4").write_bytes(video.read_bytes())

    ctx.manifest().stage_done(
        "rough_cut", {"locked": str(ctx.work_dir / "rough_cut" / "locked.mp4")}
    )
    ctx.manifest().stage_done("graphics", {"video": "with_graphics.mp4"})

    skill = ExportSkill(ctx)
    skill.run()

    decision_log_path = ctx.output_dir / "decision_log.jsonl"
    assert decision_log_path.exists()
    lines = decision_log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    operations = [json.loads(line)["operation"] for line in lines]
    assert set(operations) == {"rough_cut", "graphics"}

    report = (ctx.output_dir / "report.md").read_text(encoding="utf-8")
    assert "_No decisions recorded._" not in report
    assert "rough_cut" in report
    assert "graphics" in report
