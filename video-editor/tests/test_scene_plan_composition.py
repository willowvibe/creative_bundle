"""Tests for the ScenePlan -> HyperFrames HTML compiler."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from video_editor_core.scene_plan_composition import (
    canvas_for_aspect,
    compile_scene_plan_project,
)


def _plan(tmp_path: Path, aspect: str = "9:16") -> dict[str, Any]:
    img = tmp_path / "scene1.png"
    img.write_bytes(b"png")
    tts = tmp_path / "scene1.mp3"
    tts.write_bytes(b"mp3")
    music = tmp_path / "bg.mp3"
    music.write_bytes(b"mp3")
    return {
        "job_id": "job-1",
        "aspect_ratio": aspect,
        "music_path": str(music),
        "scenes": [
            {
                "id": "s1",
                "type": "narration",
                "duration_seconds": 2.0,
                "narration": "one",
                "assets": [
                    {"asset_type": "image", "local_path": str(img)},
                    {"asset_type": "tts", "local_path": str(tts)},
                ],
            },
            {
                "id": "s2",
                "type": "broll",
                "duration_seconds": 3.0,
                "visual_description": "two",
                "assets": [],
            },
        ],
    }


def test_canvas_for_aspect() -> None:
    assert canvas_for_aspect("16:9") == (1920, 1080)
    assert canvas_for_aspect("9:16") == (1080, 1920)
    assert canvas_for_aspect(None) == (1920, 1080)
    assert canvas_for_aspect("weird") == (1920, 1080)


def test_compile_writes_scene_timeline(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    index = compile_scene_plan_project(_plan(tmp_path), project)
    html = index.read_text(encoding="utf-8")

    assert 'data-composition-id="main"' in html
    assert 'data-width="1080"' in html and 'data-height="1920"' in html
    assert 'data-duration="5.000"' in html  # 2 + 3

    # scene 1: image with Ken Burns keyframes + narration audio
    assert 'id="scene_0_img"' in html
    assert "@keyframes ve_drift_" in html
    assert "animation:ve_drift_" in html and " 2.000s ease-in-out both" in html
    assert 'id="scene_0_tts"' in html
    assert 'data-start="0.000"' in html

    # scene 2: placeholder text, starts at 2s
    assert 'id="scene_1_placeholder"' in html
    assert "two" in html
    assert 'data-start="2.000"' in html

    # music bed
    assert 'id="music"' in html


def test_compile_copies_assets(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    compile_scene_plan_project(_plan(tmp_path), project)
    assets = sorted(p.name for p in (project / "assets").iterdir())
    assert sorted(assets) == ["bg.mp3", "scene1.mp3", "scene1.png"]


def test_video_scene_becomes_video_element(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"mp4")
    plan = {
        "job_id": "j",
        "aspect_ratio": "16:9",
        "scenes": [
            {
                "id": "s1",
                "type": "broll",
                "duration_seconds": 1.5,
                "assets": [{"asset_type": "video", "local_path": str(clip)}],
            }
        ],
    }
    html = compile_scene_plan_project(plan, tmp_path / "proj").read_text(encoding="utf-8")
    assert 'id="scene_0_video"' in html
    assert "<video" in html
    assert "ve_drift_" not in html  # video needs no Ken Burns


def test_video_scene_loops_so_a_short_clip_cannot_freeze(tmp_path: Path) -> None:
    """A clip shorter than its scene must repeat, not hold its last frame."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"mp4")
    plan = {
        "job_id": "j",
        "aspect_ratio": "16:9",
        "scenes": [
            {
                "id": "s1",
                "type": "broll",
                "duration_seconds": 9.0,
                "assets": [{"asset_type": "video", "local_path": str(clip)}],
            }
        ],
    }
    html = compile_scene_plan_project(plan, tmp_path / "proj").read_text(encoding="utf-8")
    video_tag = html[html.index("<video") : html.index("</video>")]
    assert "loop" in video_tag


def test_root_opts_out_of_timeline_poll(tmp_path: Path) -> None:
    html = compile_scene_plan_project(_plan(tmp_path), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    assert "data-no-timeline" in html
