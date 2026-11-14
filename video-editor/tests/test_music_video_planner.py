"""Tests for the deterministic music-video planner."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from video_editor_core.models.scene_plan import ScenePlan
from video_editor_pipelines.music_video_planner import (
    DEFAULT_SEGMENT_SECONDS,
    MAX_SEGMENT_SECONDS,
    MIN_SEGMENT_SECONDS,
    _discover_media,
    _music_duration_seconds,
    build_music_video_plan,
    write_music_video_plan,
)


def _make_music(tmp_path: Path, duration: int = 10) -> Path:
    """Create a synthetic MP3 of the given duration using ffmpeg."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available on PATH")
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
    """Create a directory with one image and one short video."""
    media_dir = tmp_path / "visuals"
    media_dir.mkdir()
    (media_dir / "img1.png").write_bytes(b"fake png")
    (media_dir / "img2.jpg").write_bytes(b"fake jpg")
    (media_dir / "clip.mov").write_bytes(b"fake mov")
    return media_dir


def test_discover_media_sorts_and_filters(tmp_path: Path) -> None:
    """Supported files are returned in sorted order; unsupported files are ignored."""
    media_dir = _make_media_dir(tmp_path)
    (media_dir / "readme.txt").write_text("notes")
    (media_dir / "nested").mkdir()
    (media_dir / "nested" / "ignored.png").write_bytes(b"fake png")

    found = _discover_media(media_dir)
    assert [p.name for p in found] == ["clip.mov", "img1.png", "img2.jpg"]


def test_discover_media_empty_raises(tmp_path: Path) -> None:
    """A directory with no supported media raises a clear error."""
    media_dir = tmp_path / "empty"
    media_dir.mkdir()
    with pytest.raises(ValueError, match="No supported media files found"):
        _discover_media(media_dir)


def test_discover_media_skips_symlink_escape(tmp_path: Path) -> None:
    """Bug #198: a symlink in ``media_dir`` pointing outside the directory
    (e.g. another tenant's file or ``/etc/passwd``) must NOT be followed and
    copied into the build. It is skipped with a warning; in-tree files are
    still returned."""
    media_dir = _make_media_dir(tmp_path)
    # An outside file the symlink would otherwise reach.
    outside = tmp_path / "outside_secret.png"
    outside.write_bytes(b"another tenant's bytes")
    escape = media_dir / "escape.png"
    escape.symlink_to(outside)
    # A legitimate in-tree symlink is allowed.
    in_tree = media_dir / "alias.jpg"
    in_tree.symlink_to(media_dir / "img1.png")

    found = _discover_media(media_dir)
    names = [p.name for p in found]
    assert "escape.png" not in names  # symlink to outside is skipped
    assert "alias.jpg" in names  # in-tree symlink stays
    assert "img1.png" in names


def test_discover_media_skips_hardlink_escape(tmp_path: Path) -> None:
    """Bug #226: a hardlink in ``media_dir`` to an out-of-tree file is not a
    symlink, so the #198 resolve-then-contain check (which follows symlinks)
    does not catch it — the hardlink resolves to its in-tree path while
    sharing the original inode. Reject it via ``st_nlink > 1`` so
    ``shutil.copy2`` cannot pull the out-of-tree content into the build."""
    import os

    media_dir = tmp_path / "visuals"
    media_dir.mkdir()
    (media_dir / "real.png").write_bytes(b"legit in-tree png")
    # Out-of-tree file the hardlink would expose.
    secret = tmp_path / "secret_target.png"
    secret.write_bytes(b"OUT_OF_TREE_SECRET")
    hardlink = media_dir / "link.png"
    os.link(secret, hardlink)
    # Sanity: the hardlink is not a symlink and looks in-tree, which is why
    # the #198 guard alone cannot catch it.
    assert not hardlink.is_symlink()
    assert hardlink.resolve().relative_to(media_dir.resolve())
    assert hardlink.stat().st_nlink == 2

    found = _discover_media(media_dir)
    names = [p.name for p in found]
    assert "link.png" not in names  # hardlink to outside is skipped
    assert "real.png" in names  # legit single-link file stays


def test_discover_media_is_non_recursive(tmp_path: Path) -> None:
    """Bug #215: ``_discover_media`` lists only files directly in
    ``media_dir``; nested directories are silently skipped (not a recursive
    descent), matching the corrected docstring."""
    media_dir = tmp_path / "visuals"
    media_dir.mkdir()
    (media_dir / "top.png").write_bytes(b"fake png")
    nested = media_dir / "nested"
    nested.mkdir()
    (nested / "deep.png").write_bytes(b"fake png")

    found = _discover_media(media_dir)
    assert [p.name for p in found] == ["top.png"]


def test_build_plan_last_segment_not_dropped_for_subms_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #213: when the music duration sits a sub-millisecond above an
    integer number of segments, the final segment's ``round(end - cursor, 3)``
    used to collapse to ``0.0`` and get dropped via ``break`` — leaving the
    plan total shorter than the music (clipping the audio tail) and
    ``num_segments`` overestimating by one. The final segment is now rounded
    UP so the plan total is never shorter than the music.

    Minimal reproducer: ``music = 2.0004 s``, ``segment = 2.0 s`` → one
    segment with ``raw_duration = 2.0004``. Pre-fix ``round(2.0004, 3) = 2.0``
    (plan total 2.0, short by 0.4 ms); post-fix ``ceil = 2.001 >= 2.0004``.
    """
    media_dir = _make_media_dir(tmp_path)
    monkeypatch.setattr(
        "video_editor_pipelines.music_video_planner._music_duration_seconds",
        lambda path: 2.0004,
    )

    plan = build_music_video_plan(
        job_id="j",
        music_path=tmp_path / "song.mp3",
        media_dir=media_dir,
        segment_seconds=2.0,
    )
    total = sum(s.duration_seconds for s in plan.scenes)
    # The plan total must not be shorter than the music duration.
    assert total >= 2.0004
    # No segment was dropped to zero; the last segment carries the tail.
    assert all(s.duration_seconds > 0 for s in plan.scenes)
    assert len(plan.scenes) >= 1


def test_music_duration(tmp_path: Path) -> None:
    """_music_duration_seconds reads the audio length via ffprobe."""
    music = _make_music(tmp_path, duration=7)
    duration = _music_duration_seconds(music)
    assert 6.5 <= duration <= 7.5


def test_build_music_video_plan_cycles_media(tmp_path: Path) -> None:
    """The plan covers the music duration and cycles through media."""
    music = _make_music(tmp_path, duration=12)
    media_dir = _make_media_dir(tmp_path)

    plan = build_music_video_plan(
        job_id="test_job",
        music_path=music,
        media_dir=media_dir,
        segment_seconds=5.0,
        aspect_ratio="16:9",
    )

    assert isinstance(plan, ScenePlan)
    assert plan.job_id == "test_job"
    assert plan.aspect_ratio == "16:9"
    assert plan.music_path == str(music.resolve())
    assert len(plan.scenes) == 3
    assert sum(s.duration_seconds for s in plan.scenes) == pytest.approx(12.0, abs=0.2)

    # Directory order: clip.mov, img1.png, img2.jpg
    assert plan.scenes[0].asset_query.endswith("clip.mov")
    assert plan.scenes[1].asset_query.endswith("img1.png")
    assert plan.scenes[2].asset_query.endswith("img2.jpg")

    for scene in plan.scenes:
        assert scene.type == "music_visual"
        assert scene.narration == ""
        assert scene.visual_description == ""


def test_build_plan_clamps_segment_seconds(tmp_path: Path) -> None:
    """segment_seconds is clamped to the allowed range."""
    music = _make_music(tmp_path, duration=3)
    media_dir = _make_media_dir(tmp_path)

    small = build_music_video_plan(
        job_id="j", music_path=music, media_dir=media_dir, segment_seconds=0.1
    )
    assert small.scenes[0].duration_seconds >= MIN_SEGMENT_SECONDS

    large = build_music_video_plan(
        job_id="j", music_path=music, media_dir=media_dir, segment_seconds=100.0
    )
    assert large.scenes[0].duration_seconds <= MAX_SEGMENT_SECONDS


def test_build_plan_respects_default_segment(tmp_path: Path) -> None:
    """Default segment length is used when not overridden."""
    music = _make_music(tmp_path, duration=11)
    media_dir = _make_media_dir(tmp_path)
    plan = build_music_video_plan(job_id="j", music_path=music, media_dir=media_dir)
    assert plan.scenes[0].duration_seconds == pytest.approx(DEFAULT_SEGMENT_SECONDS, abs=0.01)


def test_build_plan_rejects_zero_duration_music(tmp_path: Path) -> None:
    """A music file with zero duration is rejected."""
    from video_editor_ffmpeg.probe import MediaInfoError
    from video_editor_ffmpeg.run import FFmpegError

    music = tmp_path / "empty.mp3"
    music.write_bytes(b"")
    media_dir = _make_media_dir(tmp_path)
    with pytest.raises((MediaInfoError, FFmpegError)):
        build_music_video_plan(job_id="j", music_path=music, media_dir=media_dir)


def test_write_music_video_plan(tmp_path: Path) -> None:
    """write_music_video_plan persists the plan as JSON."""
    music = _make_music(tmp_path, duration=3)
    media_dir = _make_media_dir(tmp_path)
    plan = build_music_video_plan(job_id="j", music_path=music, media_dir=media_dir)
    output = tmp_path / "plan.json"
    write_music_video_plan(plan, output)
    assert output.exists()
    reloaded = ScenePlan.model_validate_json(output.read_text(encoding="utf-8"))
    assert len(reloaded.scenes) == len(plan.scenes)
