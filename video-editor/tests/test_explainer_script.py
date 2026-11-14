from __future__ import annotations

import pytest
from video_editor_pipelines.explainer_script import (
    ExplainerScript,
    ScriptError,
    load_explainer_script,
    parse_explainer_script,
)

SCRIPT = """\
---
title: Why Google Paid $32 Billion For Wiz
aspect_ratio: 9:16
voice: sarang_en_narration
design: cream_editorial
reuse_threshold: 0.35
target_seconds: 50
---

## Scene 1 — The number
visual: typographic
layout: big_stat
headline: $32B
sub: Google's largest acquisition ever
narration: |
  Thirty-two billion dollars. That's what Google just paid.

## Scene 2 — The racks
visual: still
image: |
  two black server racks in a warm cream data centre
narration: |
  Wiz is five years old.
"""


def test_parses_front_matter_and_scenes():
    script = parse_explainer_script(SCRIPT)
    assert isinstance(script, ExplainerScript)
    assert script.title == "Why Google Paid $32 Billion For Wiz"
    assert script.aspect_ratio == "9:16"
    assert script.voice == "sarang_en_narration"
    assert script.reuse_threshold == 0.35
    assert script.target_seconds == 50.0
    assert [scene.id for scene in script.scenes] == ["scene_1", "scene_2"]
    first, second = script.scenes
    assert first.visual == "typographic"
    assert first.layout == "big_stat"
    assert first.props == {
        "headline": "$32B",
        "sub": "Google's largest acquisition ever",
    }
    assert second.visual == "still"
    assert second.image.startswith("two black server racks")
    assert second.subject == ""
    assert script.narration.startswith("Thirty-two billion dollars.")


def test_defaults_apply_when_front_matter_is_thin():
    script = parse_explainer_script(
        "---\ntitle: T\n---\n\n## One\nvisual: typographic\nlayout: definition\n"
        "term: Cloud\nnarration: A place for data.\n"
    )
    assert script.aspect_ratio == "9:16"
    assert script.caption_preset == "karaoke"
    assert script.design == "cream_editorial"
    assert script.reuse_threshold == 0.30
    assert script.target_seconds is None
    assert script.scenes[0].props == {"term": "Cloud"}


def test_subject_is_recorded_and_not_leaked_into_props():
    script = parse_explainer_script(
        SCRIPT.replace("visual: still", "visual: still\nsubject: racks")
    )
    assert script.scenes[1].subject == "racks"
    assert "subject" not in script.scenes[1].props


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ("visual: photo\nimage: x\nnarration: y\n", "visual"),
        ("visual: typographic\nheadline: x\nnarration: y\n", "layout"),
        ("visual: typographic\nlayout: pie_chart\nnarration: y\n", "pie_chart"),
        ("visual: still\nnarration: y\n", "image"),
        ("visual: still\nimage: x\nnarration: '   '\n", "narration"),
    ],
)
def test_invalid_scene_is_rejected_with_the_scene_named(block: str, message: str):
    text = f"---\ntitle: T\n---\n\n## Bad scene\n{block}"
    with pytest.raises(ScriptError) as excinfo:
        parse_explainer_script(text)
    assert "Bad scene" in str(excinfo.value)
    assert message in str(excinfo.value)


def test_missing_front_matter_is_rejected():
    with pytest.raises(ScriptError, match="front matter"):
        parse_explainer_script("## One\nvisual: still\nimage: x\nnarration: y\n")


def test_missing_title_is_rejected():
    with pytest.raises(ScriptError, match="title"):
        parse_explainer_script(
            "---\naspect_ratio: 9:16\n---\n\n## One\nvisual: still\nimage: x\nnarration: y\n"
        )


def test_no_scene_headings_is_rejected():
    with pytest.raises(ScriptError, match="scene heading"):
        parse_explainer_script("---\ntitle: T\n---\n\nnothing here\n")


def test_invalid_yaml_in_a_scene_names_the_scene():
    with pytest.raises(ScriptError) as excinfo:
        parse_explainer_script(
            "---\ntitle: T\n---\n\n## Broken\nvisual: still\nimage: [unclosed\nnarration: y\n"
        )
    assert "Broken" in str(excinfo.value)


def test_threshold_out_of_range_is_rejected():
    with pytest.raises(ScriptError, match="reuse_threshold"):
        parse_explainer_script(SCRIPT.replace("reuse_threshold: 0.35", "reuse_threshold: 1.5"))


def test_load_reads_the_file(tmp_path):
    path = tmp_path / "script.md"
    path.write_text(SCRIPT, encoding="utf-8")
    assert load_explainer_script(path).title.startswith("Why Google")


PRONOUNCED = """\
---
title: Coasts
pronunciations:
  Kutch: Kuchh
  Sundarbans: Shoon-dor-bon
---

## Scene 1 — West
visual: still
image: a salt flat
narration: |
  The Rann of Kutch floods. Kutchi salt.

## Scene 2 — East
visual: still
image: a mangrove delta
narration: |
  The Sundarbans, the Sundarbans' tigers.
"""


def test_pronunciations_change_only_the_spoken_narration():
    # #346: the voice reads the spoken form; captions keep the display text.
    from video_editor_pipelines.explainer_script import parse_explainer_script

    script = parse_explainer_script(PRONOUNCED)
    assert script.pronunciations == {"Kutch": "Kuchh", "Sundarbans": "Shoon-dor-bon"}
    assert "Kutch floods" in script.narration
    spoken = script.spoken_narration
    assert "Rann of Kuchh floods." in spoken
    assert "Kutchi salt" in spoken  # whole words only
    assert "The Shoon-dor-bon, the Shoon-dor-bon' tigers." in spoken


def test_a_script_without_pronunciations_speaks_its_narration():
    from video_editor_pipelines.explainer_script import parse_explainer_script

    script = parse_explainer_script(SCRIPT)
    assert script.spoken_narration == script.narration


def test_malformed_pronunciations_fail_at_parse_time():
    from video_editor_pipelines.explainer_script import ScriptError, parse_explainer_script

    with pytest.raises(ScriptError, match="pronunciations"):
        parse_explainer_script(
            PRONOUNCED.replace("  Kutch: Kuchh\n  Sundarbans: Shoon-dor-bon\n", "  - Kutch\n")
        )


def test_voice_pace_parses_as_a_number():
    script = parse_explainer_script(SCRIPT.replace("---\n", "---\nvoice_pace: 1.15\n", 1))
    assert script.voice_pace == 1.15


def test_voice_pace_defaults_to_none():
    assert parse_explainer_script(SCRIPT).voice_pace is None


@pytest.mark.parametrize("bad", ["fast", "0.2", "2.5"])
def test_a_voice_pace_outside_0_5_to_2_is_refused(bad):
    with pytest.raises(ScriptError, match="voice_pace"):
        parse_explainer_script(SCRIPT.replace("---\n", f"---\nvoice_pace: {bad}\n", 1))
