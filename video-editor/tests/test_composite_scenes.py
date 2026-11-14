"""Tests for the composite editorial scene renderer and its pipeline wiring.

A composite scene is the plate an essay explainer uses most: a still and the
type designed together (``split``, ``over_media``, ``compare``) or a typographic
plate with an editorial treatment (``quote``, ``section``).
"""

from __future__ import annotations

import re

import pytest
from video_editor_asset_ingestion.models import AssetType
from video_editor_core.composite_scenes import (
    CompositeMedia,
    CompositeSceneError,
    composite_scene_cues,
    render_composite,
    silence_unspoken_composite_cues,
)
from video_editor_core.designs import load_design
from video_editor_pipelines.explainer_plan import build_explainer_plan, explainer_requests
from video_editor_pipelines.explainer_script import ScriptScene, parse_explainer_script
from video_editor_pipelines.still_reuse import plan_still_reuse

DESIGN = load_design("paper_editorial")


def render(
    props: dict,
    *,
    duration: float = 6.0,
    words=None,
    index: int = 3,
    images: int = 1,
    width: int = 1920,
    height: int = 1080,
):
    return render_composite(
        {"id": "scene_3", "type": "composite", "props": props},
        DESIGN,
        index=index,
        duration=duration,
        width=width,
        height=height,
        image_count=images,
        words=words,
    )


# --- split ----------------------------------------------------------------


def test_split_mounts_the_still_beside_a_text_column():
    result = render(
        {
            "layout": "split",
            "eyebrow": "Chapter 01",
            "headline": "The Silk Road",
            "body": "A network of routes that carried more than goods.",
        }
    )
    assert result.media == (CompositeMedia(role="hero", asset_index=0, rect=(90, 110, 760, 860)),)
    assert 'class="comp-text is-left"' in result.markup
    assert 'class="comp-frame"' in result.markup
    assert "The Silk Road" in result.markup
    # The plate stays visible around the still, on a layer below the media.
    assert "comp-paper" in result.background
    assert "comp-paper" not in result.markup
    assert ".comp-3 .comp-text" in result.css


def test_split_side_right_mirrors_the_plate():
    result = render({"layout": "split", "side": "right", "headline": "Right"})
    assert result.media[0].rect == (1070, 110, 760, 860)
    assert ".comp-3 .comp-text { position:absolute; left:120.0px;" in result.css


def test_split_numbers_and_bullets_its_facts():
    result = render(
        {
            "layout": "split",
            "headline": "Three things",
            "facts": ["Trade routes", {"text": "Buddhism", "cue": "faith"}],
        }
    )
    assert ">01<" in result.markup and ">02<" in result.markup
    assert "Trade routes" in result.markup


def test_a_split_with_only_decoration_fails_closed():
    with pytest.raises(CompositeSceneError, match="headline, body, stat or facts"):
        render({"layout": "split", "eyebrow": "Chapter 01", "credit": "Archive image"})


# --- over_media -----------------------------------------------------------


def test_over_media_is_full_bleed_with_a_scrim_and_a_title_block():
    result = render(
        {
            "layout": "over_media",
            "headline": "Before the road",
            "body": "A valley, a river, a pass.",
            "badge": "Atlas",
            "badge_sub": "Field notes",
        }
    )
    assert result.media == (CompositeMedia(role="hero", asset_index=0, rect=(0, 0, 1920, 1080)),)
    assert 'class="comp-scrim"' in result.markup
    assert 'class="comp-title-block"' in result.markup
    assert "comp-badge-disc" in result.markup
    # A still covers the whole plate, so there is no inline paper to speak of;
    # the paper layer underneath only shows on a canvas the plate cannot fill.
    assert "comp-paper" not in result.markup


def test_over_media_fills_a_portrait_frame():
    result = render({"layout": "over_media", "headline": "Vertical"}, width=1080, height=1920)
    # A 9:16 canvas takes the 9:16 plate, so the still is the frame, not a
    # letterboxed band inside it.
    assert result.media[0].rect == (0, 0, 1080, 1920)
    # The paper still backs the plate: a media clip cannot paint the frame.
    assert "comp-paper" in result.background


def test_a_portrait_that_is_not_nine_by_sixteen_letterboxes_onto_paper():
    result = render({"layout": "over_media", "headline": "Square-ish"}, width=1080, height=1350)
    # 4:5 contains the 9:16 plate with one uniform scale (1350/1920), centred.
    assert result.media[0].rect == (160, 0, 759, 1350)
    assert "comp-paper" in result.background


def test_the_badge_reads_on_the_still_not_on_paper():
    result = render(
        {"layout": "over_media", "headline": "One", "badge": "Atlas", "badge_sub": "Notes"}
    )
    # The design's slate highlight is invisible on a dark photograph, so the
    # over_media badge takes the paper colour instead.
    name = re.search(r"\.comp-3 \.comp-badge-name \{[^}]*color:(#[0-9A-F]{6});", result.css)
    assert name is not None and name.group(1).upper() == "#FBF7EE"


def test_over_media_needs_a_still():
    with pytest.raises(CompositeSceneError, match="needs 1 image"):
        render({"layout": "over_media", "headline": "No image"}, images=0)


# --- compare --------------------------------------------------------------


def test_compare_places_two_stills_under_their_own_labels():
    result = render(
        {
            "layout": "compare",
            "headline": "Two roads",
            "labels": ["Before", "After"],
            "caption": "Same site, fifty years apart",
        },
        images=2,
    )
    assert [media.rect for media in result.media] == [
        (120, 270, 760, 520),
        (1040, 270, 760, 520),
    ]
    assert [media.asset_index for media in result.media] == [0, 1]
    assert 'class="comp-item comp-label is-first"' in result.markup
    assert 'class="comp-item comp-label is-second"' in result.markup
    assert "Before" in result.markup and "After" in result.markup
    assert "comp-top-headline" in result.markup
    assert "Same site, fifty years apart" in result.markup


def test_compare_needs_both_stills_and_both_labels():
    props = {"layout": "compare", "headline": "Two roads", "labels": ["Before", "After"]}
    with pytest.raises(CompositeSceneError, match="needs 2 image"):
        render(props, images=1)
    with pytest.raises(CompositeSceneError, match="exactly 2"):
        render({**props, "labels": ["Only one"]}, images=2)
    with pytest.raises(CompositeSceneError, match="non-empty"):
        render({**props, "labels": ["Before", ""]}, images=2)


# --- quote and section ----------------------------------------------------


def test_quote_draws_on_the_paper_plate():
    result = render(
        {
            "layout": "quote",
            "quote": "The past is never dead. It is not even past.",
            "attribution": "William Faulkner",
            "role": "Requiem for a Nun, 1951",
        },
        images=0,
    )
    assert result.media == ()
    assert result.background == ""
    assert 'class="comp-quote-mark"' in result.markup
    assert "It is not even past." in result.markup
    assert "William Faulkner" in result.markup


def test_a_quote_needs_a_quote():
    with pytest.raises(CompositeSceneError, match="`quote`"):
        render({"layout": "quote", "attribution": "Nobody"}, images=0)


def test_section_shows_its_number_and_falls_back_to_one():
    numbered = render(
        {"layout": "section", "number": "02", "eyebrow": "Part two", "headline": "After the Fall"},
        images=0,
    )
    assert 'class="comp-section-num">02<' in numbered.markup
    assert "After the Fall" in numbered.markup
    default = render({"layout": "section", "headline": "Start"}, images=0)
    assert 'class="comp-section-num">01<' in default.markup


def test_a_section_needs_a_headline():
    with pytest.raises(CompositeSceneError, match="headline"):
        render({"layout": "section", "subtitle": "1918-1939"}, images=0)


# --- validation -----------------------------------------------------------


def test_unknown_or_missing_layout_fails_closed():
    with pytest.raises(CompositeSceneError, match="unknown composite layout"):
        render({"layout": "carousel", "headline": "No"})
    with pytest.raises(CompositeSceneError, match="unknown composite layout"):
        render({"headline": "No"})


@pytest.mark.parametrize(
    ("props", "message"),
    [
        ({"layout": "split", "headline": "H", "facts": "one"}, "`facts` must be a list"),
        ({"layout": "split", "headline": "H", "stat": "42"}, "`stat` must be a map"),
        ({"layout": "split", "headline": "H", "stat": {"label": "km"}}, "needs a `value`"),
        ({"layout": "split", "headline": "H", "facts": ["  "]}, "cannot be empty"),
    ],
)
def test_malformed_props_fail_closed(props: dict, message: str):
    with pytest.raises(CompositeSceneError, match=message):
        render(props)


def test_every_piece_of_text_is_escaped():
    result = render({"layout": "split", "headline": "Tom & Jerry <b>", "body": "a > b"})
    assert "Tom &amp; Jerry &lt;b&gt;" in result.markup
    assert "a &gt; b" in result.markup
    assert "<b>" not in result.markup


def test_the_plate_keeps_its_aspect_the_same_way_on_any_canvas():
    # 9:16 takes the portrait plate at scale 1, so design units are the frame.
    result = render({"layout": "split", "headline": "Vertical"}, width=1080, height=1920)
    assert result.media[0].rect == (72, 200, 936, 760)
    # 4:5 does not have a plate of its own: the 9:16 plate is contained.
    square_ish = render({"layout": "split", "headline": "Vertical"}, width=1080, height=1350)
    assert square_ish.media[0].rect == (211, 141, 658, 534)


# --- portrait plate -------------------------------------------------------


def test_a_portrait_split_stacks_the_still_above_the_text():
    result = render({"layout": "split", "headline": "Vertical"}, width=1080, height=1920)
    assert result.media[0].rect == (72, 200, 936, 760)
    assert "left:72.0px; top:1040.0px; width:936.0px;" in result.css
    # The portrait plate carries its own type scale: the still is narrower, so
    # the headline steps down and the body stays at reading size.
    assert "font-size:72.0px" in result.css
    assert "font-size:30.0px" in result.css


def test_mounted_plates_hold_still_so_the_mat_and_type_stay_put():
    split = render({"layout": "split", "headline": "H"})
    assert 'class="comp-stage" style="animation:none"' in split.markup
    full = render({"layout": "over_media", "headline": "H"})
    assert 'class="comp-stage" style="animation:none"' not in full.markup


def test_a_portrait_split_with_side_right_puts_the_still_below():
    result = render(
        {"layout": "split", "headline": "Vertical", "side": "right"},
        width=1080,
        height=1920,
    )
    assert result.media[0].rect == (72, 1000, 936, 760)
    assert "left:72.0px; top:150.0px; width:936.0px;" in result.css


def test_a_portrait_compare_stacks_both_stills_and_their_labels():
    result = render(
        {"layout": "compare", "headline": "Two roads", "labels": ["Before", "After"]},
        images=2,
        width=1080,
        height=1920,
    )
    assert [media.rect for media in result.media] == [
        (72, 400, 936, 520),
        (72, 1060, 936, 520),
    ]
    assert "is-first" in result.css and "is-second" in result.css
    # Each label sits a fixed gap under its own still, not at one shared top.
    assert "top:956.0px" in result.css
    assert "top:1616.0px" in result.css


def test_a_portrait_section_scales_its_number_to_the_frame():
    result = render(
        {"layout": "section", "number": "02", "headline": "After the Fall"},
        images=0,
        width=1080,
        height=1920,
    )
    assert "font-size:420.0px" in result.css
    assert "left:56.0px" in result.css and "top:110.0px" in result.css


def test_a_square_frame_takes_the_portrait_plate():
    result = render(
        {"layout": "quote", "quote": "Still vertical"}, images=0, width=1080, height=1080
    )
    # h >= w is a phone-ish frame; the 9:16 plate is contained, not cropped.
    assert "width:607.5px;height:1080.0px" in result.markup


def test_colours_come_from_the_design_then_the_scene():
    assert "#59608A" in render({"layout": "split", "headline": "H"}).css
    overridden = render({"layout": "split", "headline": "H", "highlight": "#C8102E"})
    assert "#C8102E" in overridden.css


def test_the_eyebrow_text_is_never_read_as_a_colour():
    """``eyebrow`` is display copy; a quote in it must not reach the CSS.

    The renderer once treated the prop as a colour override, emitting
    ``color:THE WORLD'S...``. An apostrophe in that text opened an unterminated
    CSS string, so Chrome dropped the rest of the stylesheet and every later
    plate rendered unstyled.
    """
    result = render(
        {
            "layout": "over_media",
            "eyebrow": "THE WORLD'S BUSIEST WATERWAY",
            "headline": "H",
        }
    )
    assert "THE WORLD'S" not in result.css
    assert "#B8862F" in result.css


def test_eyebrow_color_is_the_separate_colour_override():
    result = render(
        {"layout": "split", "headline": "H", "eyebrow": "Chapter 01", "eyebrow_color": "#C8102E"}
    )
    assert "Chapter 01" in result.markup
    assert "color:#C8102E" in result.css


# --- cues and timing ------------------------------------------------------


def test_cues_come_from_the_layouts_own_text():
    cues = composite_scene_cues(
        {
            "props": {
                "layout": "split",
                "eyebrow": "Chapter 01",
                "headline": "The Silk Road",
                "body": "A network of routes.",
                "cue": "a network",
                "stat": {"value": "6,400", "label": "km"},
                "facts": [{"text": "Trade routes", "cue": "trade"}],
            }
        }
    )
    # The eyebrow and the credit are decoration and never carry a cue.
    assert cues == {
        "item_1": "The Silk Road",
        "item_2": "a network",
        "item_3": "6,400",
        "item_4": "trade",
    }


def test_display_text_can_opt_out_of_its_default_cue():
    """A title or chart label may be display copy the narration never says."""
    cues = composite_scene_cues(
        {
            "props": {
                "layout": "split",
                "headline": "Empires on the Water",
                "headline_cue": False,
                "stat": {"value": "~10,000", "label": "years", "cue": False},
                "facts": [{"text": "A land bridge", "cue": False}],
            }
        }
    )
    assert cues == {}


def test_a_compare_label_can_opt_out_of_its_default_cue():
    cues = composite_scene_cues(
        {
            "props": {
                "layout": "compare",
                "headline": "Two fortunes",
                "headline_cue": False,
                "labels": [{"text": "The Rack", "cue": False}, {"text": "The Stone", "cue": False}],
            }
        }
    )
    assert cues == {}


def test_silencing_turns_off_only_the_defaults_the_narration_never_says():
    narration = "A land bridge once linked the peninsula to Sumatra."
    words = [{"word": token, "start": 0.0, "end": 0.0} for token in narration.split()]
    out = silence_unspoken_composite_cues(
        {
            "layout": "split",
            "headline": "The sea drew a shortcut",
            "stat": {"value": "~10,000", "label": "years ago"},
            "facts": ["A land bridge", {"text": "Later trade", "cue": "trade"}],
        },
        words,
    )
    assert out["headline_cue"] is False
    assert out["stat"]["cue"] is False
    # The spoken fact keeps its default and the unspoken one is silenced.
    assert out["facts"] == ["A land bridge", {"text": "Later trade", "cue": False}]


def test_a_spoken_headline_keeps_its_default_cue():
    narration = "Empires on the water"
    words = [{"word": token, "start": 0.0, "end": 0.0} for token in narration.split()]
    out = silence_unspoken_composite_cues(
        {"layout": "section", "headline": "Empires on the Water"}, words
    )
    assert "headline_cue" not in out


def test_an_attribution_is_only_cued_when_a_cue_is_named():
    base = {"layout": "quote", "quote": "Memory is a country.", "cue": "memory"}
    assert composite_scene_cues({"props": {**base, "attribution": "A. Author"}}) == {
        "item_0": "memory"
    }
    assert composite_scene_cues(
        {"props": {**base, "attribution": "A. Author", "attribution_cue": "author"}}
    ) == {"item_0": "memory", "item_1": "author"}


def test_a_cued_item_reveals_on_the_word_that_earns_it():
    words = [
        {"word": "the", "start": 1.0, "end": 1.1},
        {"word": "silk", "start": 1.1, "end": 1.3},
        {"word": "road", "start": 1.3, "end": 1.5},
    ]
    result = render({"layout": "split", "headline": "The Silk Road"}, words=words)
    match = re.search(r'animation-delay:([\d.]+)s"><div class="comp-headline"', result.markup)
    assert match is not None
    # The phrase starts on "the" (1.0 s), not on the 0.21 s stagger fallback.
    assert float(match.group(1)) == pytest.approx(1.0, abs=0.05)


def test_without_cues_the_items_fall_in_on_a_bounded_stagger():
    result = render(
        {
            "layout": "split",
            "eyebrow": "Chapter",
            "headline": "Headline",
            "body": "Body",
            "facts": ["one", "two", "three"],
        }
    )
    delays = [float(value) for value in re.findall(r"animation-delay:([\d.]+)s", result.markup)]
    assert len(delays) == 6
    assert delays == sorted(delays)
    assert max(delays) < 6.0


# --- the adapter ----------------------------------------------------------


def _plan(props: dict, *, assets: list[dict] | None = None) -> dict:
    return {
        "job_id": "composite_job",
        "aspect_ratio": "16:9",
        "design": "paper_editorial",
        "scenes": [
            {
                "id": "plate",
                "type": "composite",
                "duration_seconds": 6.0,
                "narration": "The Silk Road.",
                "props": props,
                "assets": assets or [],
            }
        ],
    }


def _image(name: str) -> dict:
    return {"asset_type": "image", "local_path": f"/tmp/{name}.png"}


def test_the_adapter_stacks_paper_media_and_type():
    from video_editor_core.adapters.from_scene_plan import from_scene_plan

    composition = from_scene_plan(
        _plan({"layout": "split", "headline": "The Silk Road"}, assets=[_image("hero")])
    )
    assert composition.design is not None
    (scene,) = composition.scenes
    paper, media, overlay = scene.layers
    assert [layer.id for layer in scene.layers] == [
        "scene_0_paper",
        "scene_0_media0",
        "scene_0_composite",
    ]
    assert [layer.z for layer in scene.layers] == [0, 1, 2]
    assert len({layer.track for layer in scene.layers}) == 3
    assert paper.kind == "html" and paper.preset == "composite_background"
    assert media.kind == "media" and media.media_kind == "image"
    assert media.content.source == "/tmp/hero.png"
    assert (media.rect.x, media.rect.y, media.rect.width, media.rect.height) == (
        90,
        110,
        760,
        860,
    )
    assert overlay.preset == "composite"
    assert "comp-headline" in overlay.content.text
    assert "comp-0" in str(overlay.style.get("extra_css", ""))


def test_compare_compiles_two_media_clips():
    from video_editor_core.adapters.from_scene_plan import from_scene_plan

    composition = from_scene_plan(
        _plan(
            {"layout": "compare", "headline": "Two roads", "labels": ["Before", "After"]},
            assets=[_image("before"), _image("after")],
        )
    )
    (scene,) = composition.scenes
    media = [layer for layer in scene.layers if layer.kind == "media"]
    assert [layer.content.source for layer in media] == ["/tmp/before.png", "/tmp/after.png"]
    assert [layer.z for layer in media] == [1, 1]


def test_a_mounted_still_does_not_drift_out_of_its_mat():
    from video_editor_core.adapters.from_scene_plan import from_scene_plan

    split = from_scene_plan(_plan({"layout": "split", "headline": "One"}, assets=[_image("hero")]))
    (scene,) = split.scenes
    mounted = next(layer for layer in scene.layers if layer.kind == "media")
    # Drifting a mounted still scales it past the paper mat, which eats the
    # mount edge; the still holds still instead.
    assert mounted.motions == []

    full = from_scene_plan(
        _plan({"layout": "over_media", "headline": "One"}, assets=[_image("hero")])
    )
    (scene,) = full.scenes
    bleed = next(layer for layer in scene.layers if layer.kind == "media")
    assert [motion.name for motion in bleed.motions] == ["drift"]


def test_a_quote_scene_needs_no_media_layer():
    from video_editor_core.adapters.from_scene_plan import from_scene_plan

    composition = from_scene_plan(_plan({"layout": "quote", "quote": "A line."}))
    (scene,) = composition.scenes
    (layer,) = scene.layers
    assert layer.kind == "html"
    assert layer.preset == "composite"
    assert "comp-quote" in layer.content.text


def test_a_composite_with_too_few_images_fails_closed():
    from video_editor_core.adapters.from_scene_plan import from_scene_plan

    with pytest.raises(CompositeSceneError, match="needs 2 image"):
        from_scene_plan(_plan({"layout": "compare", "headline": "Two", "labels": ["A", "B"]}))


# --- the cue gate ---------------------------------------------------------


def test_cue_qa_fails_a_headline_the_narration_never_says():
    from video_editor_pipelines.composition_qa import cue_problems

    plan = _plan(
        {
            "layout": "split",
            "headline": "The Silk Road",
            "facts": [{"text": "Buddhism", "cue": "faith"}],
            "word_times": [
                {"word": "the", "start": 1.0, "end": 1.1},
                {"word": "silk", "start": 1.1, "end": 1.2},
                {"word": "road", "start": 1.2, "end": 1.4},
            ],
        }
    )
    assert cue_problems(plan) == ["plate: 'faith'"]


def test_cue_qa_passes_when_every_cue_is_spoken():
    from video_editor_pipelines.composition_qa import cue_problems

    plan = _plan(
        {
            "layout": "split",
            "headline": "The Silk Road",
            "facts": ["The Silk Road"],
            "word_times": [
                {"word": "the", "start": 1.0, "end": 1.1},
                {"word": "silk", "start": 1.1, "end": 1.2},
                {"word": "road", "start": 1.2, "end": 1.4},
            ],
        }
    )
    assert cue_problems(plan) == []


def test_cue_qa_reports_an_unreadable_composite_layout():
    from video_editor_pipelines.composition_qa import cue_problems

    problems = cue_problems(_plan({"layout": "carousel", "headline": "No"}))
    assert len(problems) == 1
    assert "composite cues unreadable" in problems[0]


# --- the script layer -----------------------------------------------------


SCRIPT = """\
---
title: T
design: paper_editorial
---
## One
visual: composite
layout: split
image: a watercolour of a mountain pass
eyebrow: Chapter 01
headline: The Silk Road
narration: The road begins in the mountains.

## Two
visual: composite
layout: compare
image: the pass in 1918
image_b: the pass in 1968
headline: Two roads
labels: [Before, After]
narration: Fifty years later, the same pass.

## Three
visual: composite
layout: quote
quote: The past is never dead.
attribution: William Faulkner
narration: Memory works like that.
"""


def test_a_composite_script_scene_parses_its_layout_and_prompts():
    script = parse_explainer_script(SCRIPT)
    split, compare, quote = script.scenes
    assert (split.layout, split.image, split.image_b) == ("split", split.image, "")
    assert split.props["headline"] == "The Silk Road"
    assert compare.image_b == "the pass in 1968"
    assert compare.props["labels"] == ["Before", "After"]
    assert quote.layout == "quote"


def test_a_composite_scene_needs_a_layout_and_the_right_number_of_prompts():
    with pytest.raises(Exception, match="needs `layout`"):
        parse_explainer_script("---\ntitle: T\n---\n## One\nvisual: composite\nnarration: Hi.\n")
    with pytest.raises(Exception, match="second `image_b`"):
        parse_explainer_script(
            "---\ntitle: T\n---\n## One\nvisual: composite\nlayout: compare\n"
            "image: one\nnarration: Hi.\n"
        )
    with pytest.raises(Exception, match="needs an `image`"):
        parse_explainer_script(
            "---\ntitle: T\n---\n## One\nvisual: composite\nlayout: split\nnarration: Hi.\n"
        )


def test_the_plan_carries_the_composite_layout_and_the_second_prompt():
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job1")
    split, compare, quote = plan["scenes"]
    assert [scene["type"] for scene in plan["scenes"]] == ["composite"] * 3
    assert split["props"]["layout"] == "split"
    assert split["visual_description"] == "a watercolour of a mountain pass"
    assert compare["props"]["image_b_prompt"] == "the pass in 1968"
    assert "image_b_prompt" not in quote["props"]
    assert quote["visual_description"] == ""


def test_a_compare_scene_emits_two_image_requests():
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job1")
    images = [
        request
        for request in explainer_requests(plan, script, "job1")
        if request.asset_type is AssetType.IMAGE
    ]
    assert [(request.asset_id, request.prompt) for request in images] == [
        ("scene_1_image", "a watercolour of a mountain pass"),
        ("scene_2_image", "the pass in 1918"),
        ("scene_2_image_b", "the pass in 1968"),
    ]


# --- the reuse accounting -------------------------------------------------


def composite(scene_id: str, image: str, image_b: str = "", narration: str = "") -> ScriptScene:
    return ScriptScene(
        id=scene_id,
        title=scene_id,
        visual="composite",
        narration=narration or f"Narration for {scene_id}.",
        image=image,
        image_b=image_b,
    )


def test_compare_counts_its_second_still_as_a_render():
    reuse = plan_still_reuse([composite("a", "a pass", image_b="the same pass")])
    assert reuse.still_scenes == 1
    assert reuse.base_renders == 1
    assert reuse.extra_renders == 1
    assert reuse.renders == 2


def test_a_text_only_composite_is_not_a_still_scene():
    reuse = plan_still_reuse([composite("a", "")])
    assert reuse.still_scenes == 0
    assert reuse.renders == 0


def test_composite_scenes_join_the_still_reuse_clusters():
    scenes = [
        composite("a", "a pass under snow", narration="The pass is under snow."),
        composite("b", "a pass under snow", narration="The pass, still under snow."),
    ]
    reuse = plan_still_reuse(scenes, threshold=0.20)
    assert [cluster.scenes for cluster in reuse.clusters] == [["a", "b"]]
    assert reuse.saved == 1


def test_every_composite_type_size_clears_the_craft_floor():
    result = render(
        {
            "layout": "compare",
            "headline": "Two ways through",
            "labels": ["Strait", "Cape"],
            "caption": "Illustrative reconstruction",
            "eyebrow": "CHAPTER 01",
            "watermark": "EXPLAINER",
        },
        images=2,
    )
    sizes = [float(value) for value in re.findall(r"font-size:([\d.]+)px", result.css)]
    assert sizes and min(sizes) >= 28.0


def test_the_watermark_takes_the_overlay_colour_on_media():
    """On the scrim, muted ink reads 3.5:1; the overlay colour clears AA."""
    result = render({"layout": "over_media", "headline": "H", "watermark": "EXPLAINER"})
    rule = re.search(r"\.comp-3 \.comp-mark \{[^}]*color:(#[0-9A-Fa-f]{6})", result.css)
    assert rule is not None and rule.group(1).upper() == "#FBF7EE"


def test_the_fact_number_uses_the_darker_gold_on_paper():
    """The brand gold is 2.8:1 on paper; the darker ink is 3.4:1."""
    result = render(
        {"layout": "split", "headline": "H", "facts": ["Spice", "Tin", "Camphour"]},
        images=1,
    )
    rule = re.search(r"\.comp-3 \.comp-fact-num \{[^}]*color:(#[0-9A-Fa-f]{6})", result.css)
    assert rule is not None and rule.group(1).upper() == "#A9782A"
