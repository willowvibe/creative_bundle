"""#278: every path a worker payload names stays inside the job's directories."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_pipelines.handlers import (
    _handle_graphics,
    _handle_music,
    _job_path,
    _require_job_path,
)
from video_editor_state.job import JobContext


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    job = JobContext(job_id="job-1", work_root=tmp_path / "work", output_root=tmp_path / "out")
    job.ensure_dirs()
    return job


def test_a_sibling_with_a_shared_prefix_is_not_inside(tmp_path: Path) -> None:
    from video_editor_cli.main import _validate_payload_root

    base = tmp_path / "abc"
    base.mkdir()

    with pytest.raises(ValueError, match="configured data root"):
        _validate_payload_root(str(tmp_path / "abc-evil"), str(base), "source")


@pytest.mark.parametrize("key", ["media_dir", "music_path", "presets_dir", "lyrics_path"])
@pytest.mark.parametrize("value", ["../../elsewhere", "/etc/passwd", "~/x"])
def test_paths_outside_the_job_are_refused(ctx: JobContext, key: str, value: str) -> None:
    with pytest.raises(ValueError, match=key):
        _job_path(ctx, {key: value}, key)


def test_relative_paths_are_taken_from_the_work_dir(ctx: JobContext) -> None:
    assert (
        _job_path(ctx, {"music_path": "assets/bed.mp3"}, "music_path")
        == (ctx.work_dir / "assets" / "bed.mp3").resolve()
    )


def test_paths_inside_either_job_dir_are_accepted(ctx: JobContext) -> None:
    inside_output = ctx.output_dir / "final.mp4"

    assert _job_path(ctx, {"music_path": str(inside_output)}, "music_path") == inside_output


def test_missing_optional_and_required_paths(ctx: JobContext) -> None:
    assert _job_path(ctx, {}, "music_path") is None
    with pytest.raises(ValueError, match="project_dir"):
        _require_job_path(ctx, {}, "project_dir")
    with pytest.raises(ValueError, match="project_dir"):
        _require_job_path(ctx, {"project_dir": "/tmp"}, "project_dir")


def test_music_handler_refuses_an_outside_music_path(ctx: JobContext) -> None:
    with pytest.raises(ValueError, match="music_path"):
        _handle_music(ctx, {"music_path": "/etc/hosts"})


def test_graphics_handler_refuses_an_outside_presets_dir(ctx: JobContext) -> None:
    with pytest.raises(ValueError, match="presets_dir"):
        _handle_graphics(ctx, {"presets_dir": "/etc"})
