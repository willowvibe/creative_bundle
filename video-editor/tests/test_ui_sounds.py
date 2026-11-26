"""Chart swooshes and big-number hits are audio cues in the composition."""

from __future__ import annotations

from video_editor_core.adapters.from_scene_plan import from_scene_plan


def _plan(tally_sound: bool = True) -> dict:
    return {
        "job_id": "ui_sounds",
        "aspect_ratio": "16:9",
        "tally_sound": tally_sound,
        "scenes": [
            {"id": "a", "type": "narration", "duration_seconds": 4.0, "props": {}},
            {
                "id": "b",
                "type": "typographic",
                "duration_seconds": 4.0,
                "props": {"layout": "big_stat", "headline": "₹3,764", "sub": "a day"},
            },
            {
                "id": "c",
                "type": "typographic",
                "duration_seconds": 4.0,
                "props": {"layout": "big_stat", "headline": "152", "sub": "plates"},
            },
        ],
    }


def test_a_swoosh_marks_the_cut_from_footage_to_a_chart_only() -> None:
    cues = {cue.id: cue for cue in from_scene_plan(_plan()).audio}
    assert "scene_1_swoosh" in cues and abs(cues["scene_1_swoosh"].start - 4.05) < 1e-6
    assert "scene_2_swoosh" not in cues  # chart to chart: no swoosh


def test_each_big_stat_gets_a_hit_inside_its_scene() -> None:
    cues = {cue.id: cue for cue in from_scene_plan(_plan()).audio}
    for index, (start, end) in ((1, (4.0, 8.0)), (2, (8.0, 12.0))):
        hit = cues[f"scene_{index}_hit"]
        assert start <= hit.start < end


def test_tally_sound_false_silences_ui_sounds() -> None:
    ids = {cue.id for cue in from_scene_plan(_plan(tally_sound=False)).audio}
    assert not any(cue_id.endswith(("_swoosh", "_hit")) for cue_id in ids)
