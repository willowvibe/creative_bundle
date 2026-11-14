"""Tests for the scene-plan -> Composition adapter."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_core.adapters.from_scene_plan import from_scene_plan
from video_editor_core.scene_plan_composition import compile_scene_plan_project


def _plan(**extra) -> dict:
    plan = {
        "job_id": "plan_job",
        "aspect_ratio": "9:16",
        "scenes": [
            {
                "id": "s1",
                "type": "image",
                "duration_seconds": 3.0,
                "narration": "one",
                "assets": [{"asset_type": "image", "local_path": "still.png"}],
            },
            {
                "id": "s2",
                "type": "video",
                "duration_seconds": 2.0,
                "narration": "two",
                "assets": [{"asset_type": "video", "local_path": "clip.mp4"}],
            },
        ],
    }
    plan.update(extra)
    return plan


def test_scene_windows_are_contiguous_and_ordered():
    comp = from_scene_plan(_plan())
    assert [(s.start, s.end) for s in comp.scenes] == [(0.0, 3.0), (3.0, 5.0)]
    assert comp.duration == 5.0
    assert (comp.canvas.width, comp.canvas.height) == (1080, 1920)


def test_a_still_gets_the_ken_burns_move_for_its_index():
    comp = from_scene_plan(_plan())
    still = comp.scenes[0].layers[0]
    assert still.kind == "media" and still.media_kind == "image"
    assert still.id == "scene_0_img"
    assert still.motions[0].name == "drift"
    assert still.motions[0].params["move"] == 0
    assert still.motions[0].ease == "ease-in-out"


def test_a_video_scene_loops_and_covers():
    comp = from_scene_plan(_plan())
    video = comp.scenes[1].layers[0]
    assert video.media_kind == "video"
    assert video.id == "scene_1_video"
    assert video.style["loop"] is True


def test_a_scene_id_outside_the_pattern_falls_back_to_its_index(tmp_path: Path):
    # ``ScenePlan`` leaves ``id`` unconstrained; the Composition model does
    # not, so an odd-but-legal plan id must not abort the compile.
    plan = _plan()
    plan["scenes"][0]["id"] = "scene 1"
    comp = from_scene_plan(plan)
    assert comp.scenes[0].id == "scene_0"
    compile_scene_plan_project(plan, tmp_path / "proj")


def test_a_scene_without_media_becomes_a_placeholder():
    plan = _plan()
    plan["scenes"] = [{"id": "s1", "type": "image", "duration_seconds": 2.0, "narration": "x"}]
    comp = from_scene_plan(plan)
    placeholder = comp.scenes[0].layers[0]
    assert placeholder.kind == "html" and placeholder.preset == "placeholder"
    assert placeholder.content.text == "x"


def test_typographic_scenes_carry_their_markup_and_css():
    plan = _plan()
    plan["scenes"] = [
        {
            "id": "s1",
            "type": "typographic",
            "duration_seconds": 3.0,
            "narration": "Thirty-two billion dollars.",
            "props": {"layout": "big_stat", "headline": "$32B"},
            "design": "cream_editorial",
        }
    ]
    plan["design"] = "cream_editorial"
    comp = from_scene_plan(plan)
    typo = comp.scenes[0].layers[0]
    assert typo.id == "scene_0_typo"
    assert typo.kind == "html"
    assert "typo-0" in typo.content.text
    assert typo.style["extra_css"]


def test_audio_assets_become_cues(tmp_path: Path):
    tts = tmp_path / "line.mp3"
    tts.write_bytes(b"mp3")
    narration = tmp_path / "narration.mp3"
    narration.write_bytes(b"mp3")
    music = tmp_path / "music.mp3"
    music.write_bytes(b"mp3")
    plan = _plan()
    plan["scenes"][0]["assets"].append({"asset_type": "tts", "local_path": str(tts)})
    plan["narration_path"] = str(narration)
    plan["music_path"] = str(music)
    comp = from_scene_plan(plan)
    by_id = {cue.id: cue for cue in comp.audio}
    assert by_id["scene_0_tts"].track == 100
    assert by_id["narration"].track == 150 and by_id["narration"].duration == 5.0
    assert by_id["music"].track == 200


def test_a_missing_audio_path_is_not_emitted():
    plan = _plan(narration_path="/nope/missing.mp3")
    assert from_scene_plan(plan).audio == []


def test_the_design_reaches_the_composition():
    plan = _plan(design="cream_editorial")
    plan["scenes"].append(
        {
            "id": "s3",
            "type": "typographic",
            "duration_seconds": 2.0,
            "narration": "x",
            "props": {"layout": "big_stat", "headline": "X"},
        }
    )
    comp = from_scene_plan(plan)
    assert comp.design is not None and comp.design.name == "cream_editorial"


def test_no_layer_outlives_its_scene_or_the_composition():
    # Durations taken from a real ten-scene draft. Rounding the scene end once
    # from the running cursor (as the composition is timed) differs from
    # rounding ``start + duration`` a second time inside the layer window, and
    # the second round can land a frame late -- on the last scene, outside the
    # composition, which the Composition validator rejects.
    durations = [4.68, 5.86, 6.14, 7.04, 6.74, 11.62, 6.62, 5.22, 5.1, 8.995]
    plan = {
        "job_id": "quantise_job",
        "aspect_ratio": "9:16",
        "scenes": [
            {
                "id": f"s{index}",
                "type": "image",
                "duration_seconds": duration,
                "narration": f"line {index}",
                "assets": [{"asset_type": "image", "local_path": "still.png"}],
            }
            for index, duration in enumerate(durations)
        ],
    }
    comp = from_scene_plan(plan, fps=30)
    for scene in comp.scenes:
        for layer in scene.layers:
            assert layer.time.end <= scene.end
            assert layer.time.end <= comp.duration
            assert layer.time.start == scene.start
            assert layer.time.end == scene.end
    assert comp.scenes[-1].end == comp.duration
    assert [s.start for s in comp.scenes] == [0.0, *[s.end for s in comp.scenes[:-1]]]


def test_the_narration_cue_takes_the_measured_audio_length(tmp_path: Path):
    """A tail after the last word must not leave the audio slot longer than the file."""
    narration = tmp_path / "narration.mp3"
    narration.write_bytes(b"mp3")
    music = tmp_path / "music.mp3"
    music.write_bytes(b"mp3")
    plan = _plan(narration_path=str(narration), music_path=str(music))
    plan["narration_duration"] = 4.2
    cues = {cue.id: cue for cue in from_scene_plan(plan).audio}
    assert cues["narration"].duration == pytest.approx(4.2)
    assert cues["music"].duration == pytest.approx(5.0)


def test_a_longer_measured_narration_never_stretches_the_composition(tmp_path: Path):
    narration = tmp_path / "narration.mp3"
    narration.write_bytes(b"mp3")
    plan = _plan(narration_path=str(narration))
    plan["narration_duration"] = 9.9
    cues = {cue.id: cue for cue in from_scene_plan(plan).audio}
    assert cues["narration"].duration == pytest.approx(5.0)
