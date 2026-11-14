"""Tests for the FableCut direct renderer.

These tests cover ``video_editor_fablecut_bridge.renderer.FableCutRenderer`` and
the ``render_fablecut_project`` wrapper that is now used by both the standalone
script and ``ve render-fablecut``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg


def _make_fablecut_project(
    project_dir: Path,
    media_src: Path,
    width: int = 320,
    height: int = 240,
    fps: int = 30,
    duration: float = 2.0,
    clip_type: str = "video",
    caption_text: str | None = None,
) -> Path:
    """Create a minimal exported FableCut project.json matching bridge models."""
    project_dir.mkdir(parents=True, exist_ok=True)
    media_dir = project_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    rel_media = media_src.relative_to(project_dir).as_posix()
    media_id = "media-1"
    media: dict[str, object] = {
        "id": media_id,
        "kind": clip_type,
        "src": rel_media,
    }
    if clip_type == "video":
        media["duration"] = duration
    clip: dict[str, object] = {
        "id": "clip-1",
        "mediaId": media_id,
        "kind": clip_type,
        "track": "V1",
        "start": 0.0,
        "duration": duration,
    }
    if caption_text:
        clip["kind"] = "text"
        clip["track"] = "T1"
        clip["props"] = {
            "text": caption_text,
            "fontSize": 24,
            "color": "#ffffff",
            "x": 0,
            "y": height - 30,
        }
    project = {
        "version": "1.0",
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        "background": "#000000",
        "media": [media],
        "clips": [clip],
    }
    project_path = project_dir / "project.json"
    project_path.write_text(json.dumps(project), encoding="utf-8")
    return project_path


@pytest.fixture
def project_with_video(tmp_path: Path, tmp_media: Path) -> Path:
    """A FableCut project directory containing a short video clip."""
    project_dir = tmp_path / "fc-project-video"
    media_dest = project_dir / "media" / "input.mp4"
    media_dest.parent.mkdir(parents=True, exist_ok=True)
    media_dest.write_bytes(tmp_media.read_bytes())
    _make_fablecut_project(
        project_dir,
        media_dest,
        width=320,
        height=240,
        fps=30,
        duration=2.0,
        clip_type="video",
    )
    return project_dir


@pytest.fixture
def project_with_image(tmp_path: Path) -> Path:
    """A FableCut project directory containing an image clip."""
    _require_ffmpeg()
    project_dir = tmp_path / "fc-project-image"
    media_dest = project_dir / "media" / "input.jpg"
    media_dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=1",
            "-frames:v",
            "1",
            str(media_dest),
        ],
        check=True,
        capture_output=True,
    )
    _make_fablecut_project(
        project_dir,
        media_dest,
        width=320,
        height=240,
        fps=30,
        duration=2.0,
        clip_type="image",
    )
    return project_dir


class TestFableCutRendererProject:
    """End-to-end rendering from a live FableCut project.json."""

    def test_render_video_project(self, project_with_video: Path, tmp_path: Path) -> None:
        """A project with a single video clip produces a valid MP4."""
        _require_ffmpeg()
        from video_editor_fablecut_bridge.renderer import render_fablecut_project

        output = tmp_path / "rendered.mp4"
        render_fablecut_project(
            project_path=project_with_video / "project.json",
            output_path=output,
            render_options=RenderOptions(),
        )
        assert output.exists()
        assert output.stat().st_size > 0
        info = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        rendered_duration = float(info.stdout.strip())
        assert rendered_duration >= 1.8

    def test_render_image_project(self, project_with_image: Path, tmp_path: Path) -> None:
        """A project with a single image clip produces a valid MP4 with Ken Burns."""
        _require_ffmpeg()
        from video_editor_fablecut_bridge.renderer import render_fablecut_project

        output = tmp_path / "rendered-image.mp4"
        render_fablecut_project(
            project_path=project_with_image / "project.json",
            output_path=output,
            render_options=RenderOptions(),
        )
        assert output.exists()
        assert output.stat().st_size > 0
        info = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert float(info.stdout.strip()) >= 1.8


class TestFableCutRendererSecurity:
    """Path containment and payload validation."""

    def test_rejects_media_src_outside_project_dir(self, tmp_path: Path, tmp_media: Path) -> None:
        """A media src that escapes the project directory raises ValueError."""
        _require_ffmpeg()
        from video_editor_fablecut_bridge.renderer import FableCutRenderer

        project_dir = tmp_path / "fc-safe"
        project_dir.mkdir()
        # Place a real media file outside the project directory.
        outside_file = tmp_path / "input.mp4"
        outside_file.write_bytes(tmp_media.read_bytes())
        project_data = {
            "version": "1.0",
            "width": 320,
            "height": 240,
            "fps": 30,
            "duration": 1.0,
            "background": "#000000",
            "media": [{"id": "m1", "kind": "video", "src": "../input.mp4", "duration": 1.0}],
            "clips": [],
        }
        project_path = project_dir / "project.json"
        project_path.write_text(json.dumps(project_data), encoding="utf-8")
        with pytest.raises(ValueError, match="outside"):
            FableCutRenderer(
                project_path=project_path,
                output_path=tmp_path / "out.mp4",
            )

    def test_safe_filename_rejects_path_separators_in_output_name(self) -> None:
        """The worker safety helper rejects output_name containing path separators."""
        from video_editor_cli.main import _safe_filename

        with pytest.raises(ValueError, match="output_name"):
            _safe_filename("../escape.mp4", "output_name")


class TestFableCutWorkerHandler:
    """Integration coverage for ``_handle_render_fablecut``."""

    def test_worker_handler_writes_final_video(
        self, project_with_video: Path, tmp_path: Path
    ) -> None:
        """The worker handler renders into the job output directory."""
        _require_ffmpeg()
        from video_editor_cli import main

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        ctx = JobContext(
            job_id="fc-render-job",
            work_root=work_root,
            output_root=output_root,
        )
        ctx.ensure_dirs()
        # Copy the prepared project into the job work dir.
        shutil.copytree(project_with_video, ctx.work_dir / "fablecut")
        payload = main._RenderFableCutPayload(
            project_dir="fablecut",
            output_name="final.mp4",
        )
        asyncio.run(main._handle_render_fablecut(ctx, payload.model_dump()))
        final = ctx.output_dir / "final.mp4"
        assert final.exists()
        assert final.stat().st_size > 0
        assert ctx.manifest().stages.get("export")
