"""Tests for typographic scenes in the ScenePlan -> HyperFrames compiler."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_core.scene_plan_composition import compile_scene_plan_project


def typographic_plan(**extra) -> dict:
    plan = {
        "job_id": "typo_job",
        "aspect_ratio": "9:16",
        "design": "cream_editorial",
        "scenes": [
            {
                "id": "scene_1",
                "type": "typographic",
                "duration_seconds": 4.0,
                "narration": "Thirty-two billion dollars.",
                "props": {"layout": "big_stat", "headline": "$32B"},
            }
        ],
    }
    plan.update(extra)
    return plan


def test_typographic_scene_type_and_design_field_are_accepted():
    scene = Scene(id="s1", type="typographic", duration_seconds=3.0, narration="x")
    plan = ScenePlan(job_id="j", script="s", scenes=[scene], design="ink_noir")
    assert plan.design == "ink_noir"


def test_unknown_scene_type_is_still_rejected():
    with pytest.raises(ValidationError):
        Scene(id="s1", type="motion_graphics", duration_seconds=3.0)


def test_typographic_scene_compiles_without_an_image(tmp_path):
    index = compile_scene_plan_project(typographic_plan(), tmp_path)
    html = index.read_text(encoding="utf-8")
    assert "<img" not in html
    assert 'data-start="0.000"' in html
    assert 'data-duration="4.000"' in html
    assert "$32B" in html
    assert "@font-face" in html
    assert "typo-0" in html


def test_plan_narration_is_emitted_once_at_the_root(tmp_path):
    narration = tmp_path / "narration.mp3"
    narration.write_bytes(b"ID3fake")
    index = compile_scene_plan_project(
        typographic_plan(narration_path=str(narration)), tmp_path / "project"
    )
    html = index.read_text(encoding="utf-8")
    assert html.count('id="narration"') == 1
    assert "assets/narration.mp3" in html


def test_word_times_reach_the_layout(tmp_path):
    plan = typographic_plan()
    plan["scenes"][0]["props"] = {
        "layout": "bar_compare",
        "bars": [{"label": "Wiz", "value": 32}, {"label": "Nest", "value": 3}],
        "word_times": [{"word": "Nest", "start": 2.5, "end": 2.7}],
    }
    html = compile_scene_plan_project(plan, tmp_path).read_text(encoding="utf-8")
    assert "animation-delay:2.500s" in html


def test_a_plan_with_no_design_still_compiles(tmp_path):
    plan = typographic_plan()
    del plan["design"]
    assert "typo-0" in compile_scene_plan_project(plan, tmp_path).read_text(encoding="utf-8")


def test_word_times_are_rebased_to_the_clip_start(tmp_path):
    plan = typographic_plan()
    plan["scenes"][0]["duration_seconds"] = 5.0
    plan["scenes"].append(
        {
            "id": "scene_2",
            "type": "typographic",
            "duration_seconds": 2.0,
            "narration": "It is bigger than Nest.",
            "props": {
                "layout": "big_stat",
                "headline": "Bigger",
                "cue": "Bigger",
                # Scene two starts at 5.0 s; the cue is spoken 0.4 s into it.
                "word_times": [
                    {"word": "Bigger", "start": 5.4, "end": 5.7},
                    {"word": "Nest", "start": 5.2, "end": 5.5},
                ],
            },
        }
    )
    compile_scene_plan_project(plan, tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    # Element ids are index-scoped, so the second scene is ``scene_1_typo``.
    scene_two = html.split('id="scene_1_typo"', 1)[1]
    delays = [float(v) for v in re.findall(r"animation-delay:\s*([0-9.]+)s", scene_two)]
    assert delays, "no animation delays emitted"
    # 5.4 s absolute minus the 5.0 s clip start, not 5.4 s and not the scene end.
    assert any(abs(delay - 0.4) <= 0.02 for delay in delays), (
        f"word cue was not rebased by the clip start: {delays}"
    )
    assert max(delays) < 1.0, f"word cue was not rebased to the clip: {max(delays)}"
