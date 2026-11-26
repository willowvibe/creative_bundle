"""Tests for the content-addressed chunk render cache."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from video_editor_core import chunk_cache
from video_editor_core.chunk_cache import (
    RenderCache,
    count_video_frames,
    project_hash,
    render_settings_key,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _clip(path: Path, frames: int) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30",
         "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def _project(root: Path, html: str = "<html>a</html>") -> Path:
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(html, encoding="utf-8")
    (root / "assets" / "still.png").write_bytes(b"png-bytes")
    return root


def test_hash_is_stable_and_ignores_location(tmp_path: Path) -> None:
    a = project_hash(_project(tmp_path / "a"), "s")
    b = project_hash(_project(tmp_path / "b"), "s")
    assert a == b and len(a) == 20


def test_hash_changes_with_content_asset_or_settings(tmp_path: Path) -> None:
    base = project_hash(_project(tmp_path / "a"), "s")
    assert project_hash(_project(tmp_path / "b", "<html>b</html>"), "s") != base
    c = _project(tmp_path / "c")
    (c / "assets" / "still.png").write_bytes(b"other")
    assert project_hash(c, "s") != base
    assert project_hash(_project(tmp_path / "d"), "t") != base


def test_hash_skips_render_outputs(tmp_path: Path) -> None:
    project = _project(tmp_path / "a")
    before = project_hash(project, "s")
    (project / "rendered.mp4").write_bytes(b"x")
    (project / "renders").mkdir()
    (project / "renders" / "seg.mp4").write_bytes(b"y")
    assert project_hash(project, "s") == before


def test_settings_key_names_version_fps_and_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VE_RENDER_GPU", "0")
    monkeypatch.setattr(chunk_cache, "_hyperframes_version", lambda cli: "0.8.216")
    key = render_settings_key(30)
    assert "hyperframes=0.8.216" in key and "fps=30" in key and "schema=1" in key


@needs_ffmpeg
def test_store_then_lookup_validates_frames(tmp_path: Path) -> None:
    cache = RenderCache(tmp_path / "cache")
    stored = cache.store("k1", _clip(tmp_path / "r.mp4", 15), frames=15, scene_ids=["s0"])
    assert stored == tmp_path / "cache" / "k1.mp4"
    assert count_video_frames(stored) == 15
    assert cache.lookup("k1", 15) == stored
    assert cache.lookup("k1", 16) is None
    assert cache.lookup("missing", 15) is None


@needs_ffmpeg
def test_lookup_rejects_a_changed_file(tmp_path: Path) -> None:
    cache = RenderCache(tmp_path / "cache")
    stored = cache.store("k1", _clip(tmp_path / "r.mp4", 15), frames=15, scene_ids=["s0"])
    stored.write_bytes(b"truncated")
    assert cache.lookup("k1", 15) is None


@needs_ffmpeg
def test_prune_keeps_only_named_keys(tmp_path: Path) -> None:
    cache = RenderCache(tmp_path / "cache")
    cache.store("keep", _clip(tmp_path / "a.mp4", 5), frames=5, scene_ids=["s0"])
    cache.store("drop", _clip(tmp_path / "b.mp4", 5), frames=5, scene_ids=["s1"])
    (tmp_path / "cache" / "stray.mp4").write_bytes(b"z")
    removed = cache.prune({"keep"})
    assert sorted(removed) == ["drop", "stray"]
    assert sorted(p.name for p in (tmp_path / "cache").glob("*.mp4")) == ["keep.mp4"]
