"""CLI tests for the musical_video template."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _tmp_data_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make each test's ``tmp_path`` the configured data root (#278)."""
    monkeypatch.setenv("VE_WORK_ROOT", str(tmp_path))
    monkeypatch.setenv("VE_OUTPUT_ROOT", str(tmp_path))


def _make_music(tmp_path: Path, duration: int = 4) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available on PATH")
    music = tmp_path / "song.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-q:a",
            "0",
            str(music),
        ],
        check=True,
        capture_output=True,
    )
    return music


def _make_media_dir(tmp_path: Path) -> Path:
    media_dir = tmp_path / "visuals"
    media_dir.mkdir()
    (media_dir / "img1.png").write_bytes(
        bytes(
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
    )
    return media_dir


def _noop_render(ctx: Any, plan_path: Path, output_path: Path, **kwargs: Any) -> Path:
    """Mock render_scene_plan that writes a real short video to the output path.

    The rendered duration is derived from the scene plan so the default
    post-render review gate does not fail on a duration mismatch in tests.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    duration = (
        sum(float(scene.get("duration_seconds", 0) or 0) for scene in plan.get("scenes", [])) or 3.0
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size=640x360:rate=30",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )
    return output_path


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def test_run_musical_video_explicit_template(
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ve run song.mp3 --media-dir visuals --template musical_video` succeeds."""
    music = _make_music(tmp_path, duration=4)
    media_dir = _make_media_dir(tmp_path)
    monkeypatch.setattr(
        "video_editor_pipelines.pipeline.render_scene_plan",
        _noop_render,
    )
    from video_editor_cli.main import app

    result = cli_runner.invoke(
        app,
        [
            "run",
            str(music),
            "-j",
            "mv-cli-job",
            "--work-root",
            str(tmp_path / "work"),
            "--output-root",
            str(tmp_path / "output"),
            "--media-dir",
            str(media_dir),
            "--template",
            "musical_video",
            "--segment-seconds",
            "2.0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "musical_video" in result.output
    assert (tmp_path / "output" / "mv-cli-job" / "final.mp4").exists()


def test_run_musical_video_inferred_from_media_dir(
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An audio source with --media-dir is inferred as musical_video."""
    music = _make_music(tmp_path, duration=4)
    media_dir = _make_media_dir(tmp_path)
    monkeypatch.setattr(
        "video_editor_pipelines.pipeline.render_scene_plan",
        _noop_render,
    )
    from video_editor_cli.main import app

    result = cli_runner.invoke(
        app,
        [
            "run",
            str(music),
            "-j",
            "mv-inferred-job",
            "--work-root",
            str(tmp_path / "work"),
            "--output-root",
            str(tmp_path / "output"),
            "--media-dir",
            str(media_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "musical_video" in result.output
    assert (tmp_path / "output" / "mv-inferred-job" / "final.mp4").exists()


def test_run_musical_video_requires_media_dir(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """musical_video requires --media-dir to exist."""
    music = _make_music(tmp_path, duration=2)
    from video_editor_cli.main import app

    result = cli_runner.invoke(
        app,
        [
            "run",
            str(music),
            "-j",
            "mv-missing-dir",
            "--work-root",
            str(tmp_path / "work"),
            "--output-root",
            str(tmp_path / "output"),
            "--template",
            "musical_video",
        ],
    )
    assert result.exit_code != 0
    assert "requires an existing --media-dir" in result.output


def test_run_musical_video_rejects_directory_source(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """Bug #209: a directory passed as ``--source`` for musical_video is
    rejected at the CLI with a clear message instead of constructing
    ``MusicVideoPipeline(music_path=<dir>)`` and raising a confusing
    ``FileNotFoundError`` for a path that does exist."""
    media_dir = _make_media_dir(tmp_path)
    from video_editor_cli.main import app

    result = cli_runner.invoke(
        app,
        [
            "run",
            str(media_dir),  # a directory, not a music file
            "-j",
            "mv-dir-source",
            "--work-root",
            str(tmp_path / "work"),
            "--output-root",
            str(tmp_path / "output"),
            "--media-dir",
            str(media_dir),
            "--template",
            "musical_video",
        ],
    )
    assert result.exit_code != 0
    assert "music/audio file" in result.output


# ---------------------------------------------------------------------------
# Bug #199 — the worker ``_handle_run`` music_video branch had zero test
# coverage. This end-to-end test enqueues a real ``run`` payload through the
# typed schema and drains it via ``ve worker``, exercising the real
# ``_handle_run`` music_video dispatch (no fakes). It would have caught
# #191/#192 (traversal), #200 (missing media_dir at enqueue), #201 (NaN
# segment_seconds), and #209 (directory source).
# ---------------------------------------------------------------------------


def test_worker_handle_run_musical_video_end_to_end(
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued ``musical_video`` run dispatched through the real worker
    produces a final.mp4. The ``source`` (music) and ``media_dir`` are placed
    inside the job's work_dir so they pass the worker's path-containment
    guard (C4). ``render_scene_plan`` is stubbed so no HyperFrames/Node render
    is required; everything else (enqueue schema, _handle_run dispatch,
    MusicVideoPipeline.run_async, FFmpeg export) runs for real."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available on PATH")
    from video_editor_cli import main as cli_main
    from video_editor_cli.main import app
    from video_editor_state.queue import InMemoryJobQueue

    work_root = tmp_path / "work"
    output_root = tmp_path / "output"
    job_id = "mv-worker-job"
    work_dir = work_root / job_id
    work_dir.mkdir(parents=True)

    # C4: place inputs inside the job's work_dir so the worker's
    # _resolve_payload_path containment accepts them.
    music = work_dir / "song.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-q:a",
            "0",
            str(music),
        ],
        check=True,
        capture_output=True,
    )
    media_dir = work_dir / "visuals"
    media_dir.mkdir()
    (media_dir / "img1.png").write_bytes(_PNG_BYTES)

    monkeypatch.setattr(cli_main, "_queue_instance", InMemoryJobQueue(), raising=False)
    monkeypatch.setattr(
        "video_editor_pipelines.pipeline.render_scene_plan",
        _noop_render,
    )

    # 1. Enqueue via the real ``ve enqueue`` CLI (typed payload schema).
    enqueue_result = cli_runner.invoke(
        app,
        [
            "enqueue",
            "run",
            "-j",
            job_id,
            "--payload",
            json.dumps(
                {
                    "source": str(music),
                    "template": "musical_video",
                    "media_dir": str(media_dir),
                    "segment_seconds": 2.0,
                    "work_root": str(work_root),
                    "output_root": str(output_root),
                }
            ),
        ],
    )
    assert enqueue_result.exit_code == 0, enqueue_result.output

    # 2. Drain via the real ``ve worker`` CLI — exercises
    # queue.dequeue → _dispatch_message → _handle_run (music_video branch)
    # → MusicVideoPipeline.run_async → ExportSkill.
    worker_result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
    assert worker_result.exit_code == 0, worker_result.output

    # 3. The real pipeline wrote the final deliverable.
    final = output_root / job_id / "final.mp4"
    assert final.exists(), f"final.mp4 missing at {final}"
    assert final.stat().st_size > 0, "final.mp4 is empty"


# ---------------------------------------------------------------------------
# Bug #200 / #201 — enqueue-time payload validation for musical_video.
# ---------------------------------------------------------------------------


def test_enqueue_run_rejects_musical_video_without_media_dir(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """Bug #200: a ``musical_video`` enqueue without ``media_dir`` must fail
    at enqueue (typed schema) rather than dead-lettering after dequeue."""
    from video_editor_cli.main import app

    # A music file source inside work_dir so source containment would pass
    # — the failure must be the missing media_dir, not source containment.
    work_root = tmp_path / "work"
    job_id = "mv-no-mediadir"
    work_dir = work_root / job_id
    work_dir.mkdir(parents=True)
    source = work_dir / "song.mp3"
    source.write_bytes(b"fake")

    result = cli_runner.invoke(
        app,
        [
            "enqueue",
            "run",
            "-j",
            job_id,
            "--payload",
            json.dumps(
                {
                    "source": str(source),
                    "template": "musical_video",
                    "work_root": str(work_root),
                    "output_root": str(tmp_path / "output"),
                }
            ),
        ],
    )
    assert result.exit_code != 0
    assert "media_dir" in result.output


@pytest.mark.parametrize("bad_seconds", ["NaN", "Infinity", "-Infinity", "0", "-1"])
def test_enqueue_run_rejects_non_finite_or_non_positive_segment_seconds(
    cli_runner: CliRunner,
    tmp_path: Path,
    bad_seconds: str,
) -> None:
    """Bug #201: ``segment_seconds`` must be finite and positive at enqueue
    time. NaN/inf used to pass Pydantic ``float`` and only misbehave inside
    the planner's fragile NaN-clamp; zero/negative would enqueue and fail
    later. The payload schema now rejects them up front."""
    from video_editor_cli.main import app

    work_root = tmp_path / "work"
    job_id = "mv-bad-seg"
    work_dir = work_root / job_id
    work_dir.mkdir(parents=True)
    source = work_dir / "song.mp3"
    source.write_bytes(b"fake")
    media_dir = work_dir / "visuals"
    media_dir.mkdir()

    result = cli_runner.invoke(
        app,
        [
            "enqueue",
            "run",
            "-j",
            job_id,
            "--payload",
            json.dumps(
                {
                    "source": str(source),
                    "template": "musical_video",
                    "media_dir": str(media_dir),
                    "segment_seconds": float(bad_seconds),
                    "work_root": str(work_root),
                    "output_root": str(tmp_path / "output"),
                }
            ),
        ],
    )
    assert result.exit_code != 0


_PNG_BYTES = bytes(
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
