"""Tests for turning a parsed explainer script into a priced plan."""

from __future__ import annotations

from video_editor_asset_ingestion.models import AssetType
from video_editor_pipelines.explainer_plan import (
    build_explainer_plan,
    explainer_requests,
    plan_model,
    rewrite_timings,
)
from video_editor_pipelines.explainer_script import parse_explainer_script
from video_editor_pipelines.explainer_timing import SceneTiming

SCRIPT = """\
---
title: T
---
## One
visual: typographic
layout: big_stat
headline: $32B
narration: Thirty-two billion dollars.

## Two
visual: still
image: two black server racks in a cream data centre
narration: Wiz is five years old. It watches the whole cloud.

## Three
visual: still
image: two black server racks in a cream data centre
narration: The same racks, the same cloud, still watch.

## Four
visual: still
image: a gem on velvet
narration: A gem.
"""


DESIGNED_STILL = """\
---
title: T
---
## One
visual: still
image: a fishing boat at dawn in a narrow strait
eyebrow: OPENING
headline: A narrow sea lane
subtitle: Sixty miles of water
narration: The strait is a narrow sea lane.
"""


def test_a_still_with_display_copy_becomes_an_over_media_plate():
    """A titled still is a designed plate, not a bare full-bleed image."""
    plan, _ = build_explainer_plan(parse_explainer_script(DESIGNED_STILL), "job1")
    (scene,) = plan["scenes"]
    assert scene["type"] == "composite"
    assert scene["props"]["layout"] == "over_media"
    assert scene["props"]["headline"] == "A narrow sea lane"
    assert scene["props"]["subtitle"] == "Sixty miles of water"
    # The still prompt still reaches the IMAGE request.
    assert scene["visual_description"] == "a fishing boat at dawn in a narrow strait"


def test_a_designed_still_compiles_to_the_composite_plate():
    """Script → plan → composition keeps the still and adds the title plate."""
    from video_editor_core.adapters.from_scene_plan import from_scene_plan

    plan, _ = build_explainer_plan(parse_explainer_script(DESIGNED_STILL), "job1")
    plan["scenes"][0]["assets"] = [{"asset_type": "image", "local_path": "still.png"}]
    (scene,) = from_scene_plan(plan).scenes
    presets = {layer.preset for layer in scene.layers}
    assert "composite_background" in presets and "composite" in presets
    media = [layer for layer in scene.layers if layer.kind == "media"]
    assert [layer.content.source for layer in media] == ["still.png"]
    # The full-bleed still drifts; the title block stays composed against it.
    assert media[0].motions and media[0].motions[0].name == "drift"


def test_a_still_without_display_copy_stays_a_bare_narration_scene():
    plan, _ = build_explainer_plan(parse_explainer_script(SCRIPT), "job1")
    assert plan["scenes"][1]["type"] == "narration"
    assert "layout" not in plan["scenes"][1]["props"]


def test_plan_carries_types_design_and_reuse():
    script = parse_explainer_script(SCRIPT)
    plan, reuse = build_explainer_plan(script, "job1")
    assert [scene["type"] for scene in plan["scenes"]] == [
        "typographic",
        "narration",
        "narration",
        "narration",
    ]
    assert plan["design"] == "cream_editorial"
    assert plan["scenes"][0]["visual_description"] == ""
    assert plan["scenes"][1]["visual_description"].startswith("two black")
    assert plan["scenes"][2]["props"]["image_scene"] == "scene_2"
    assert reuse.saved == 1


def test_one_narration_request_for_the_whole_script():
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job1")
    requests = explainer_requests(plan, script, "job1")
    tts = [request for request in requests if request.asset_type == AssetType.TTS]
    assert [request.asset_id for request in tts] == ["job_narration"]
    assert "Thirty-two billion" in tts[0].prompt
    assert "A gem." in tts[0].prompt


def test_a_reused_still_emits_one_image_request_less():
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job1")
    images = [
        request
        for request in explainer_requests(plan, script, "job1")
        if request.asset_type == AssetType.IMAGE
    ]
    assert [request.asset_id for request in images] == ["scene_2_image", "scene_4_image"]


def test_no_reuse_keeps_every_image_request():
    script = parse_explainer_script(SCRIPT)
    plan, reuse = build_explainer_plan(script, "job1", reuse=False)
    assert reuse.clusters == []
    images = [
        request
        for request in explainer_requests(plan, script, "job1")
        if request.asset_type == AssetType.IMAGE
    ]
    assert len(images) == 3


def test_voice_front_matter_reaches_the_narration_request():
    script = parse_explainer_script(
        SCRIPT.replace("title: T", "title: T\nvoice: sarang_en_narration\nvoice_model: bulbul:v3")
    )
    plan, _ = build_explainer_plan(script, "job1")
    tts = explainer_requests(plan, script, "job1")[0]
    assert tts.params["voice_id"] == "sarang_en_narration"
    assert tts.params["model"] == "bulbul:v3"


def test_rewrite_timings_sets_durations_and_word_times():
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job1")
    timings = [
        SceneTiming("scene_1", 0.0, 3.0, [{"word": "Thirty-two", "start": 0.1, "end": 0.5}]),
        SceneTiming("scene_2", 3.0, 6.0, [{"word": "Wiz", "start": 3.2, "end": 3.4}]),
        SceneTiming("scene_3", 6.0, 8.0, []),
        SceneTiming("scene_4", 8.0, 10.0, []),
    ]
    rewrite_timings(plan, timings)
    assert [scene["duration_seconds"] for scene in plan["scenes"]] == [3.0, 3.0, 2.0, 2.0]
    assert plan["scenes"][0]["props"]["word_times"] == [
        {"word": "Thirty-two", "start": 0.1, "end": 0.5}
    ]
    model = plan_model(plan)
    assert model.scenes[0].props["word_times"][0]["word"] == "Thirty-two"


def test_rewrite_timings_writes_nothing_for_an_unknown_scene_id():
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job1")
    rewrite_timings(
        plan,
        [
            SceneTiming("scene_1", 0.0, 3.0, [{"word": "Thirty-two", "start": 0.1, "end": 0.5}]),
            SceneTiming("scene_missing", 3.0, 6.0, [{"word": "Wiz", "start": 3.2, "end": 3.4}]),
        ],
    )
    assert [scene["duration_seconds"] for scene in plan["scenes"]] == [3.0, 1.0, 1.0, 1.0]
    assert all("word_times" not in scene["props"] for scene in plan["scenes"][1:])


def test_the_narration_request_reads_the_spoken_form():
    # #346: the voice reads the pronunciation; the display narration is unchanged.
    script = parse_explainer_script(
        SCRIPT.replace("title: T", "title: T\npronunciations: {Wiz: Whiz}")
    )
    plan, _ = build_explainer_plan(script, "job1")
    tts = explainer_requests(plan, script, "job1")[0]
    assert "Whiz is five years old" in tts.prompt
    assert "Wiz is five years old" in script.narration


def test_voice_pace_reaches_the_narration_request():
    script = parse_explainer_script(SCRIPT.replace("title: T", "title: T\nvoice_pace: 1.15"))
    plan, _ = build_explainer_plan(script, "job1")
    tts = explainer_requests(plan, script, "job1")[0]
    # No voice prefix routes to ElevenLabs, which takes the pace as ``speed``.
    assert tts.params["speed"] == 1.15
    assert "pace" not in tts.params


def test_no_voice_pace_sends_no_pace():
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job1")
    assert "pace" not in explainer_requests(plan, script, "job1")[0].params
