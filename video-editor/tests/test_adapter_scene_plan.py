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


def test_the_tally_counts_when_its_calculation_is_spoken():
    plan = {
        "job_id": "tally_job",
        "aspect_ratio": "9:16",
        "scenes": [
            {
                "id": "open",
                "type": "narration",
                "duration_seconds": 3.0,
                "props": {"tally": {"label": "cash", "value": -12500}},
            },
            {
                "id": "milk",
                "type": "narration",
                "duration_seconds": 6.0,
                "props": {
                    "tally": {"label": "cash", "value": -1488},
                    "calc": [{"label": "పాలు", "expr": "24 L × ₹62", "amount": -1488}],
                    "word_times": [
                        {"word": "రోజుకి", "start": 3.2, "end": 3.8},
                        {"word": "₹1,488", "start": 6.1, "end": 6.9},
                    ],
                },
            },
        ],
    }
    composition = from_scene_plan(plan)
    chip = next(
        layer for layer in composition.scenes[1].layers if layer.id.endswith("_tally")
    )

    # Scene 1 starts at 3.0 s, so the amount is spoken 3.10 s into the clip; the
    # tally waits TALLY_AFTER_RESULT_SECONDS (1.0 s) after the card shows it.
    assert "4.10s" in chip.content.text
    assert "3.10s" not in chip.content.text


def _sales_plan(word_times, duration=10.0):
    return {
        "job_id": "order_job",
        "aspect_ratio": "16:9",
        "scenes": [
            {
                "id": "open",
                "type": "narration",
                "duration_seconds": 2.0,
                "props": {"tally": {"label": "galla", "value": 0}},
            },
            {
                "id": "sales",
                "type": "narration",
                "duration_seconds": duration,
                "props": {
                    "tally": {"label": "galla", "value": 12000},
                    "calc": [{"label": "Sales", "expr": "300 × ₹40", "amount": 12000}],
                    "word_times": word_times,
                },
            }
        ],
    }


def _layer(composition, suffix):
    return next(layer for layer in composition.scenes[1].layers if layer.id.endswith(suffix))


def test_calculation_then_result_then_tally():
    # "₹40 ... 300 ప్లేట్లు ... ₹12,000." : the expression shows at ₹40, the result at
    # ₹12,000, and the tally counts only after the card has shown the result.
    words = [
        {"word": "₹40", "start": 3.0, "end": 1.4},
        {"word": "300", "start": 4.0, "end": 2.4},
        {"word": "ప్లేట్లు", "start": 4.5, "end": 3.0},
        {"word": "₹12,000.", "start": 5.5, "end": 4.2},
    ]
    composition = from_scene_plan(_sales_plan(words))
    card = _layer(composition, "_calc").content.text
    chip = _layer(composition, "_tally").content.text
    assert 'calc-row" style="animation-delay: 1.00s"' in card
    assert 'calc-result" style="animation-delay: 3.50s"' in card
    assert "4.50s both" in chip  # 3.50 + 1.0


def test_an_operand_outside_the_window_is_not_the_setup():
    # ₹40 is said 8 s before the result: too early to count as setting it up.
    words = [
        {"word": "₹40", "start": 2.5, "end": 0.9},
        {"word": "300", "start": 9.0, "end": 7.4},
        {"word": "₹12,000.", "start": 10.5, "end": 9.0},
    ]
    card = _layer(from_scene_plan(_sales_plan(words, duration=12.0)), "_calc").content.text
    assert 'calc-row" style="animation-delay: 7.00s"' in card


def test_unheard_rows_lead_their_result_by_a_beat():
    card = _layer(from_scene_plan(_sales_plan([])), "_calc").content.text
    result = float(card.split('calc-result" style="animation-delay: ')[1].split("s")[0])
    row = float(card.split('calc-row" style="animation-delay: ')[1].split("s")[0])
    assert row < result
    assert result - row <= 0.8 + 1e-6


def test_a_late_result_still_comes_before_the_tally():
    # The result is spoken 1 s before the scene ends; the end clamp must not pull the
    # count ahead of the result.
    words = [{"word": "₹12,000.", "start": 11.0, "end": 9.6}]
    chip = _layer(from_scene_plan(_sales_plan(words)), "_tally").content.text
    card = _layer(from_scene_plan(_sales_plan(words)), "_calc").content.text
    result = float(card.split('calc-result" style="animation-delay: ')[1].split("s")[0])
    count = float(chip.split("linear ")[1].split("s")[0])
    assert count > result


def test_two_rows_with_the_same_amount_land_where_each_is_said():
    plan = _sales_plan([])
    plan["scenes"][1]["props"]["calc"] = [
        {"label": "Parcel", "expr": "75 × ₹2", "amount": -150},
        {"label": "Power", "expr": "₹150", "amount": -150},
        {"label": "Misc", "expr": "₹3", "amount": -3},
    ]
    plan["scenes"][1]["props"]["word_times"] = [
        {"word": "₹150.", "start": 3.0, "end": 3.5},
        {"word": "₹3,764", "start": 4.0, "end": 4.5},
        {"word": "₹150.", "start": 7.0, "end": 7.5},
        {"word": "₹3", "start": 9.0, "end": 9.3},
    ]
    card = _layer(from_scene_plan(plan), "_calc").content.text
    results = [float(part.split("s")[0]) for part in card.split('calc-result" style="animation-delay: ')[1:]]
    # Clip-relative (the opener is 2 s); ₹3,764 is not "3". The ₹3 row has no
    # separate result span (its expression is its answer), so check its row delay.
    # Power's setup is its own "₹150" (the parcel ₹150 belongs to the row above), so
    # its expression and result land together, as one span, at 5.0.
    assert results == [1.0]
    assert 'calc-row" style="animation-delay: 5.00s"' in card
    assert 'calc-row" style="animation-delay: 7.00s"' in card
