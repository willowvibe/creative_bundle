"""The cost checklist strip: markup, geometry and script validation."""

from __future__ import annotations

import pytest
from video_editor_core.adapters.from_scene_plan import from_scene_plan
from video_editor_core.progress_strip import parse_progress, parse_progress_labels, render_progress
from video_editor_pipelines.explainer_script import ScriptError, parse_explainer_script

STEPS = ["Rent", "Staff", "Milk"]


def test_labels_must_be_unique_non_empty():
    assert parse_progress_labels(STEPS) == tuple(STEPS)
    for bad in ([], "Rent", ["Rent", ""], ["Rent", "Rent"]):
        with pytest.raises(ValueError):
            parse_progress_labels(bad)


def test_strip_marks_done_current_and_pending():
    progress = parse_progress({"items": STEPS, "current": "Staff"})
    markup, css = render_progress(progress, index=1, width=1080)
    assert (
        markup.index("progress-done") < markup.index("progress-now") < markup.index("progress-todo")
    )
    assert markup.count("progress-done") == 1 and markup.count("progress-now") == 1
    assert markup.count("progress-todo") == 1
    assert "✓" in markup and "▸" in markup
    assert "#FDB813" in css and "font-weight: 800" in css
    # no opacity animation on a child of the strip
    assert "opacity" not in css.split("@keyframes progress_mark_1", 1)[1]
    assert "0.30s ease-out 0.30s" in css  # marker animates in at 0.3 s


def test_unknown_current_is_rejected():
    with pytest.raises(ValueError):
        parse_progress({"items": STEPS, "current": "Tea"})


def _plan(progress=None):
    props = {"tally": {"label": "cash", "value": -100}}
    if progress is not None:
        props["progress"] = progress
    return {
        "job_id": "p_job",
        "aspect_ratio": "9:16",
        "scenes": [{"id": "s1", "type": "narration", "duration_seconds": 5.0, "props": props}],
    }


def test_strip_sits_below_the_tally_without_overlap():
    composition = from_scene_plan(_plan({"items": STEPS, "current": "Rent"}))
    layers = composition.scenes[0].layers
    strips = [layer for layer in layers if layer.preset == "progress"]
    assert len(strips) == 1
    strip = strips[0]
    tally = next(layer for layer in layers if layer.preset == "tally")
    assert strip.rect.x == tally.rect.x
    assert strip.rect.y >= tally.rect.y + tally.rect.height
    assert strip.rect.width == pytest.approx(1080 * 0.24, abs=1)
    assert "Rent" in strip.content.text


def test_a_scene_without_progress_gets_no_strip():
    layers = from_scene_plan(_plan()).scenes[0].layers
    assert not [layer for layer in layers if layer.preset == "progress"]


def _script(front: str, scene_progress: str) -> str:
    return (
        f"---\ntitle: T\n{front}---\n\n## Scene 1\nvisual: still\nimage: a still\n"
        f"narration: hello\n{scene_progress}\n"
    )


def test_script_resolves_the_scene_label_into_props():
    script = parse_explainer_script(_script("progress: [Rent, Staff]\n", "progress: Staff"))
    assert script.progress == ("Rent", "Staff")
    assert script.scenes[0].props["progress"] == {"items": ["Rent", "Staff"], "current": "Staff"}


def test_script_rejects_an_unknown_scene_label():
    with pytest.raises(ScriptError, match="Tea"):
        parse_explainer_script(_script("progress: [Rent, Staff]\n", "progress: Tea"))


def test_script_rejects_a_label_without_a_front_matter_list():
    with pytest.raises(ScriptError, match="front matter"):
        parse_explainer_script(_script("", "progress: Rent"))


def test_script_tally_sound_must_be_boolean():
    assert parse_explainer_script(_script("tally_sound: false\n", "")).tally_sound is False
    assert parse_explainer_script(_script("", "")).tally_sound is True
    with pytest.raises(ScriptError, match="tally_sound"):
        parse_explainer_script(_script("tally_sound: maybe\n", ""))
