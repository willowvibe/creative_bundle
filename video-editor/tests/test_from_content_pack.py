from __future__ import annotations

import copy
from pathlib import Path

import pytest
from video_editor_core.adapters.from_content_pack import pack_to_composition
from video_editor_core.content_pack import Alignment, ContentPack, ContentPackError
from video_editor_core.designs import LightLeak, load_design
from video_editor_core.render_graph import Rect

PACK = {
    "job_id": "demo",
    "voice": {"voice_id": "sarvam:test"},
    "scenes": [
        {
            "id": "hook",
            "still": {"key": "rat", "prompt": "a rat on a branch"},
            "lines": [{"say": "You think you're a Rat?", "show": "Not just a Rat", "cue": "Rat"}],
        },
        {
            "id": "branch",
            "still": {"reuse": "rat"},
            "chip": "子",
            "lines": [{"say": "Your animal is the year branch.", "show": "Rat = 1 of 8"}],
        },
    ],
    "end_card": {
        "say": "Find yours free at cosmicsaju.com.",
        "text": "Free chart snapshot",
    },
}

ALIGNMENT = {
    "duration": 6.0,
    "scenes": [
        {
            "id": "hook",
            "start": 0.0,
            "end": 2.0,
            "words": [
                {"text": "You", "start": 0.1, "end": 0.3},
                {"text": "Rat", "start": 1.0, "end": 1.2},
            ],
        },
        {
            "id": "branch",
            "start": 2.0,
            "end": 4.0,
            "words": [{"text": "branch", "start": 2.4, "end": 2.8}],
        },
        {
            "id": "end_card",
            "start": 4.0,
            "end": 6.0,
            "words": [{"text": "cosmicsaju.com", "start": 4.2, "end": 5.4}],
        },
    ],
}


def build(stills: dict[str, Path] | None = None):
    pack = ContentPack.model_validate(PACK)
    tokens = load_design("saju_series")
    alignment = Alignment.model_validate(ALIGNMENT)
    # `is None`, not `or`: the missing-still test passes `{}`, which is falsy, so
    # `stills or default` would silently substitute the default and the test
    # would exercise the opposite of what it names.
    return pack_to_composition(
        pack,
        tokens,
        alignment,
        stills={"rat": Path("/tmp/rat.png")} if stills is None else stills,
    )


def test_scenes_are_the_pack_scenes_then_the_end_card() -> None:
    composition = build()
    assert [scene.id for scene in composition.scenes] == ["hook", "branch", "end_card"]
    assert composition.duration == pytest.approx(7.2)
    assert composition.scenes[-1].start == pytest.approx(4.0)
    assert composition.audio_mode == "external"


def test_each_scene_carries_a_full_canvas_still_with_drift() -> None:
    composition = build()
    still = composition.scenes[0].layers[0]
    assert still.id == "still_hook"
    assert still.kind == "media"
    assert still.rect.model_dump() == {"x": 0, "y": 0, "width": 1080, "height": 1920}
    assert still.style["object_fit"] == "cover"
    assert still.media_kind == "image"
    assert [motion.name for motion in still.motions] == ["drift"]
    assert still.motions[0].params["to"] == "scale(1.08)"


def test_scrims_sit_between_the_still_and_the_copy() -> None:
    composition = build()
    presets = [layer.preset for layer in composition.scenes[0].layers]
    assert presets[:3] == ["base", "scrim", "scrim"]
    scrims = [layer for layer in composition.scenes[0].layers if layer.preset == "scrim"]
    assert [scrim.z for scrim in scrims] == [10, 12]
    assert scrims[0].content.gradient.startswith("linear-gradient(")


def test_the_chip_appears_only_where_the_pack_asks_for_it() -> None:
    composition = build()
    chips = [
        layer for scene in composition.scenes for layer in scene.layers if layer.preset == "chip"
    ]
    assert len(chips) == 1
    assert chips[0].content.text == "子"
    assert chips[0].style["font"] == "Noto Serif KR"


def test_pills_stack_per_scene_from_the_design_rows() -> None:
    composition = build()
    hook_pill = next(layer for layer in composition.scenes[0].layers if layer.preset == "pill")
    assert hook_pill.content.text == "Not just a Rat"
    assert hook_pill.rect.y == 520
    assert hook_pill.style["background"] == "rgba(15,11,30,0.72)"
    branch_pill = next(layer for layer in composition.scenes[1].layers if layer.preset == "pill")
    assert branch_pill.rect.y == 520


def test_pills_reveal_on_their_cue() -> None:
    composition = build()
    hook_pill = next(layer for layer in composition.scenes[0].layers if layer.preset == "pill")
    # "Rat" is spoken at 1.0s; resolve_cues leads by 0.08s so the reveal
    # lands with the word rather than a beat behind it. 0.92s then snaps to the
    # 30 fps frame grid: frame 28 is 0.933s, so the raw 0.92 would never appear.
    assert hook_pill.time.start == pytest.approx(28 / 30)
    assert hook_pill.time.end == pytest.approx(2.0)

    branch_pill = next(layer for layer in composition.scenes[1].layers if layer.preset == "pill")
    # No cue on that line: the pill is up for the whole scene.
    assert branch_pill.time.start == pytest.approx(2.0)


def test_a_cue_that_matches_no_spoken_word_fails_the_build() -> None:
    from video_editor_core.cues import CueMismatchError

    pack = ContentPack.model_validate(PACK)
    pack.scenes[0].lines[0].cue = "Year branch"
    with pytest.raises(CueMismatchError, match="hook:0"):
        pack_to_composition(
            pack,
            load_design("saju_series"),
            Alignment.model_validate(ALIGNMENT),
            stills={"rat": Path("/tmp/rat.png")},
        )


def test_the_end_card_carries_the_words_and_the_url() -> None:
    composition = build()
    card = composition.scenes[-1]
    assert card.id == "end_card"
    assert card.start == pytest.approx(4.0)
    assert card.end == pytest.approx(7.2)
    text, url = card.layers
    assert text.id == "end_card_text"
    assert text.content.text == "Free chart snapshot"
    assert text.z == 50
    assert url.id == "end_card_url"
    assert url.content.text == "cosmicsaju.com"
    assert url.style["font_color"] == "#F59E0B"
    assert url.z == 55
    assert url.rect.y == text.rect.y + text.rect.height


def test_a_cue_is_resolved_inside_its_own_scene() -> None:
    """A phrase spoken in another scene must not answer this scene's cue.

    "Rat" is spoken only in ``hook``. Cueing it on ``branch``'s line has to
    fail: a global search would silently time that pill from a word spoken
    two scenes earlier.
    """
    from video_editor_core.cues import CueMismatchError

    pack = ContentPack.model_validate(PACK)
    pack.scenes[1].lines[0].cue = "Rat"
    with pytest.raises(CueMismatchError, match="branch:0"):
        pack_to_composition(
            pack,
            load_design("saju_series"),
            Alignment.model_validate(ALIGNMENT),
            stills={"rat": Path("/tmp/rat.png")},
        )


def test_a_blank_end_card_is_refused() -> None:
    from video_editor_core.content_pack import ContentPackError

    pack = ContentPack.model_validate(PACK)
    pack.end_card.text = "  "
    with pytest.raises(ContentPackError, match="end_card.text"):
        pack_to_composition(
            pack,
            load_design("saju_series"),
            Alignment.model_validate(ALIGNMENT),
            stills={"rat": Path("/tmp/rat.png")},
        )


def test_every_layer_id_is_unique() -> None:
    composition = build()
    ids = [layer.id for scene in composition.scenes for layer in scene.layers]
    assert len(ids) == len(set(ids))


def test_a_missing_still_is_refused() -> None:
    with pytest.raises(ContentPackError, match="no still"):
        build(stills={})


def test_an_unknown_alignment_scene_is_refused() -> None:
    pack = ContentPack.model_validate(PACK)
    tokens = load_design("saju_series")
    alignment = Alignment.model_validate(
        {
            "duration": 2.0,
            "scenes": [{"id": "ghost", "start": 0.0, "end": 2.0, "words": []}],
        }
    )
    with pytest.raises(ContentPackError, match="ghost"):
        pack_to_composition(pack, tokens, alignment, stills={"rat": Path("/tmp/rat.png")})


def test_caption_layers_land_in_the_scene_that_contains_them() -> None:
    from video_editor_core.composition import CompositionLayer, LayerContent, LayerTime, WordSpan

    pack = ContentPack.model_validate(PACK)
    tokens = load_design("saju_series")
    caption = CompositionLayer(
        id="caption_000",
        kind="rich_text",
        preset="caption_line",
        rect=Rect(x=90, y=1356, width=900, height=180),
        time=LayerTime(start=2.2, end=3.4),
        content=LayerContent(spans=[WordSpan(text="branch", start=0.0, end=0.6)]),
        z=40,
    )
    composition = pack_to_composition(
        pack,
        tokens,
        Alignment.model_validate(ALIGNMENT),
        stills={"rat": Path("/tmp/rat.png")},
        captions=[caption],
    )
    branch = next(scene for scene in composition.scenes if scene.id == "branch")
    assert "caption_000" in [layer.id for layer in branch.layers]


def test_a_caption_outside_every_scene_is_refused() -> None:
    from video_editor_core.composition import CompositionLayer, LayerTime

    pack = ContentPack.model_validate(PACK)
    tokens = load_design("saju_series")
    stray = CompositionLayer(
        id="caption_900",
        kind="text",
        preset="caption_line",
        rect=Rect(x=90, y=1356, width=900, height=180),
        time=LayerTime(start=8.0, end=8.5),
        z=40,
    )
    with pytest.raises(ContentPackError, match="caption_900"):
        pack_to_composition(
            pack,
            tokens,
            Alignment.model_validate(ALIGNMENT),
            stills={"rat": Path("/tmp/rat.png")},
            captions=[stray],
        )


def test_a_cue_word_at_a_quantised_scene_boundary_still_resolves() -> None:
    """The defect this guards: scene windows are snapped to the frame grid,
    word starts are not.

    Here ``branch`` opens at the raw 1.02 s but quantises to 1.0333 s, and the
    cue word begins at 1.02 s — before the quantised start. Searching the
    quantised window drops that word and the strict resolve aborts a valid
    build, after the paid stages have run.
    """
    pack = ContentPack.model_validate(PACK)
    pack.scenes[0].lines[0].cue = ""
    pack.scenes[1].lines[0].cue = "Rat"
    alignment = Alignment.model_validate(
        {
            "duration": 6.0,
            "scenes": [
                {"id": "hook", "start": 0.0, "end": 1.02, "words": []},
                {
                    "id": "branch",
                    "start": 1.02,
                    "end": 4.0,
                    "words": [{"text": "Rat", "start": 1.02, "end": 1.2}],
                },
                {"id": "end_card", "start": 4.0, "end": 6.0, "words": []},
            ],
        }
    )
    composition = pack_to_composition(
        pack,
        load_design("saju_series"),
        alignment,
        stills={"rat": Path("/tmp/rat.png")},
    )
    branch_pill = next(layer for layer in composition.scenes[1].layers if layer.preset == "pill")
    # The reveal (1.02 - 0.08 lead) lands before the window, so it snaps up to
    # the branch window's first frame: frame 31 = 1.0333 s.
    assert branch_pill.time.start == pytest.approx(31 / 30)
    assert branch_pill.time.end == pytest.approx(4.0)


def test_a_layerized_scene_emits_a_background_and_a_cut_out() -> None:
    raw = copy.deepcopy(PACK)
    raw["scenes"][0]["layers"] = "auto"
    raw["scenes"][1]["layers"] = "auto"
    pack = ContentPack.model_validate(raw)
    stills = {
        "rat": Path("/tmp/rat.png"),
        "rat__bg": Path("/tmp/bg.png"),
        "rat__fg": Path("/tmp/fg.png"),
        "rat__depth": Path("/tmp/depth.png"),
    }
    composition = pack_to_composition(
        pack, load_design("saju_series"), Alignment.model_validate(ALIGNMENT), stills=stills
    )
    media = [layer for layer in composition.scenes[0].layers if layer.kind == "media"]
    assert [layer.id for layer in media] == ["still_hook__bg", "still_hook__fg"]
    assert [layer.z for layer in media] == [0, 5]
    assert media[0].content.source == "/tmp/bg.png"
    assert media[1].content.source == "/tmp/fg.png"
    assert [layer.motions[0].name for layer in media] == ["parallax", "parallax"]
    assert [layer.motions[0].params["rate"] for layer in media] == [0.35, 1.0]
    # The reused still resolves to the same layerized key.
    reused = [layer for layer in composition.scenes[1].layers if layer.kind == "media"]
    assert [layer.id for layer in reused] == ["still_branch__bg", "still_branch__fg"]


def test_a_layerized_scene_names_the_missing_asset() -> None:
    raw = copy.deepcopy(PACK)
    raw["scenes"][0]["layers"] = "auto"
    pack = ContentPack.model_validate(raw)
    with pytest.raises(ContentPackError, match="rat__depth"):
        pack_to_composition(
            pack,
            load_design("saju_series"),
            Alignment.model_validate(ALIGNMENT),
            stills={
                "rat": Path("/tmp/rat.png"),
                "rat__bg": Path("/tmp/bg.png"),
                "rat__fg": Path("/tmp/fg.png"),
            },
        )


def test_a_light_leak_lands_on_the_first_cut() -> None:
    tokens = load_design("saju_series").model_copy(
        update={"light_leak": LightLeak(placement="first_cut", duration=0.6, intensity=0.4)}
    )
    composition = pack_to_composition(
        ContentPack.model_validate(PACK),
        tokens,
        Alignment.model_validate(ALIGNMENT),
        stills={"rat": Path("/tmp/rat.png")},
    )
    leaks = [
        layer
        for scene in composition.scenes
        for layer in scene.layers
        if layer.preset == "light_leak"
    ]
    assert [layer.id for layer in leaks] == ["light_leak_branch"]
    assert leaks[0].kind == "html"
    assert (leaks[0].time.start, leaks[0].time.end) == (2.0, 2.6)
    assert leaks[0].z == 35
    css = leaks[0].style["extra_css"]
    assert "@keyframes ve_leak_light_leak_branch" in css
    assert "animation:ve_leak_light_leak_branch 0.600s" in css
    assert "mix-blend-mode:screen" in css
    assert leaks[0].content.text == ""


def test_no_light_leak_when_the_design_says_off() -> None:
    composition = build()
    assert not [
        layer
        for scene in composition.scenes
        for layer in scene.layers
        if layer.preset == "light_leak"
    ]


def test_a_light_leak_colour_cannot_inject_css() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LightLeak(placement="first_cut", color="red; } body { display:none")


def test_a_depth_mask_splits_the_background_in_two() -> None:
    raw = copy.deepcopy(PACK)
    raw["scenes"][0]["layers"] = "auto"
    pack = ContentPack.model_validate(raw)
    stills = {
        "rat": Path("/tmp/rat.png"),
        "rat__bg": Path("/tmp/bg.png"),
        "rat__fg": Path("/tmp/fg.png"),
        "rat__depth": Path("/tmp/depth.png"),
        "rat__near": Path("/tmp/near.png"),
    }
    composition = pack_to_composition(
        pack, load_design("saju_series"), Alignment.model_validate(ALIGNMENT), stills=stills
    )
    media = [layer for layer in composition.scenes[0].layers if layer.kind == "media"]
    assert [layer.id for layer in media] == [
        "still_hook__bg_far",
        "still_hook__bg_near",
        "still_hook__fg",
    ]
    assert [layer.z for layer in media] == [0, 1, 5]
    assert [layer.motions[0].params["rate"] for layer in media] == [0.2, 0.6, 1.0]
    assert "mask" not in media[0].style
    assert media[1].style["mask"] == "/tmp/near.png"
    assert media[2].content.source == "/tmp/fg.png"


def test_a_scene_motion_replaces_the_ambient_drift() -> None:
    raw = copy.deepcopy(PACK)
    raw["scenes"][0]["motion"] = "stamp"
    composition = pack_to_composition(
        ContentPack.model_validate(raw),
        load_design("saju_series"),
        Alignment.model_validate(ALIGNMENT),
        stills={"rat": Path("/tmp/rat.png")},
    )
    still = next(layer for layer in composition.scenes[0].layers if layer.id == "still_hook")
    assert [motion.name for motion in still.motions] == ["stamp"]


def test_a_graphic_becomes_an_html_layer_over_the_still() -> None:
    pack = copy.deepcopy(PACK)
    pack["scenes"][0]["graphic"] = {
        "component": "four_pillars",
        "data": {
            "pillars": [
                {"label": "Year", "glyph": "甲"},
                {"label": "Month", "glyph": "乙"},
                {"label": "Day", "glyph": "丙"},
                {"label": "Hour", "glyph": "丁"},
            ]
        },
        "over": "rat",
    }
    composition = pack_to_composition(
        ContentPack.model_validate(pack),
        load_design("saju_series"),
        Alignment.model_validate(ALIGNMENT),
        stills={"rat": "rat.png"},
    )
    scene = composition.scenes[0]
    graphic = next(layer for layer in scene.layers if layer.id == "graphic_hook")
    assert graphic.kind == "html"
    assert "甲" in graphic.content.text
    assert any(
        "blur" in str(layer.style.get("filter", ""))
        for layer in scene.layers
        if layer.kind == "media"
    )


def test_a_pack_transition_out_builds_the_next_scene_transition() -> None:
    pack = copy.deepcopy(PACK)
    pack["scenes"][0]["transition_out"] = "seal_stamp"
    pack["scenes"][0]["transition_seconds"] = 0.4
    composition = pack_to_composition(
        ContentPack.model_validate(pack),
        load_design("saju_series"),
        Alignment.model_validate(ALIGNMENT),
        stills={"rat": "rat.png"},
    )
    transition = composition.scenes[1].transition_in
    assert transition is not None
    assert transition.name == "seal_stamp"
    assert transition.duration == 0.4


def test_a_video_scene_becomes_a_video_media_layer() -> None:
    pack = {
        "job_id": "video",
        "voice": {"voice_id": "sarvam:test"},
        "scenes": [
            {"id": "hook", "video": "clip.mp4", "lines": [{"say": "one"}]},
        ],
        "end_card": {"say": "bye", "text": "bye"},
    }
    alignment = {
        "duration": 4.0,
        "scenes": [
            {
                "id": "hook",
                "start": 0.0,
                "end": 2.0,
                "words": [{"text": "one", "start": 0.1, "end": 0.5}],
            },
            {
                "id": "end_card",
                "start": 2.0,
                "end": 4.0,
                "words": [{"text": "bye", "start": 2.2, "end": 2.6}],
            },
        ],
    }
    composition = pack_to_composition(
        ContentPack.model_validate(pack),
        load_design("saju_series"),
        Alignment.model_validate(alignment),
        stills={},
    )
    video = next(layer for layer in composition.scenes[0].layers if layer.kind == "media")
    assert video.media_kind == "video"
    assert video.content.source == "clip.mp4"


def test_an_html_scene_becomes_an_html_layer() -> None:
    pack = {
        "job_id": "html",
        "voice": {"voice_id": "sarvam:test"},
        "scenes": [
            {"id": "hook", "html": "<div>shot</div>", "lines": [{"say": "one"}]},
        ],
        "end_card": {"say": "bye", "text": "bye"},
    }
    alignment = {
        "duration": 4.0,
        "scenes": [
            {
                "id": "hook",
                "start": 0.0,
                "end": 2.0,
                "words": [{"text": "one", "start": 0.1, "end": 0.5}],
            },
            {
                "id": "end_card",
                "start": 2.0,
                "end": 4.0,
                "words": [{"text": "bye", "start": 2.2, "end": 2.6}],
            },
        ],
    }
    composition = pack_to_composition(
        ContentPack.model_validate(pack),
        load_design("saju_series"),
        Alignment.model_validate(alignment),
        stills={},
    )
    layer = next(layer for layer in composition.scenes[0].layers if layer.kind == "html")
    assert layer.content.text == "<div>shot</div>"


def test_shots_split_the_scene_on_the_line_starts() -> None:
    pack = {
        "job_id": "shots",
        "voice": {"voice_id": "sarvam:test"},
        "scenes": [
            {
                "id": "hook",
                "lines": [{"say": "first"}, {"say": "second"}],
                "shots": [
                    {"still": {"key": "a", "prompt": "a"}, "at_line": 0},
                    {"still": {"key": "b", "prompt": "b"}, "at_line": 1},
                ],
            }
        ],
        "end_card": {"say": "bye", "text": "bye"},
    }
    alignment = {
        "duration": 6.0,
        "scenes": [
            {
                "id": "hook",
                "start": 0.0,
                "end": 4.0,
                "words": [
                    {"text": "first", "start": 0.2, "end": 0.6},
                    {"text": "second", "start": 2.0, "end": 2.4},
                ],
            },
            {
                "id": "end_card",
                "start": 4.0,
                "end": 6.0,
                "words": [{"text": "bye", "start": 4.2, "end": 4.6}],
            },
        ],
    }
    composition = pack_to_composition(
        ContentPack.model_validate(pack),
        load_design("saju_series"),
        Alignment.model_validate(alignment),
        stills={"a": "a.png", "b": "b.png"},
    )
    shots = [layer for layer in composition.scenes[0].layers if layer.id.startswith("shot")]
    assert [layer.id for layer in shots] == ["shot0_hook", "shot1_hook"]
    assert shots[0].time.start == 0.0
    assert shots[0].time.end == pytest.approx(2.0)
    assert shots[1].time.start == pytest.approx(2.0)
    assert shots[1].time.end == 4.0


def test_generated_layers_stack_bg_mg_fg() -> None:
    pack = {
        "job_id": "generated",
        "voice": {"voice_id": "sarvam:test"},
        "scenes": [
            {
                "id": "hook",
                "layers": "generated",
                "generated": {"bg": "background", "mg": "mid", "fg": "foreground"},
                "lines": [{"say": "one"}],
            }
        ],
        "end_card": {"say": "bye", "text": "bye"},
    }
    alignment = {
        "duration": 4.0,
        "scenes": [
            {
                "id": "hook",
                "start": 0.0,
                "end": 2.0,
                "words": [{"text": "one", "start": 0.1, "end": 0.5}],
            },
            {
                "id": "end_card",
                "start": 2.0,
                "end": 4.0,
                "words": [{"text": "bye", "start": 2.2, "end": 2.6}],
            },
        ],
    }
    composition = pack_to_composition(
        ContentPack.model_validate(pack),
        load_design("saju_series"),
        Alignment.model_validate(alignment),
        stills={
            "hook__bg": "bg.png",
            "hook__mg": "mg.png",
            "hook__fg": "fg.png",
        },
    )
    layers = {layer.id: layer for layer in composition.scenes[0].layers}
    assert {"bg_hook", "mg_hook", "fg_hook"} <= set(layers)
    assert layers["bg_hook"].z < layers["mg_hook"].z < layers["fg_hook"].z
    rates = {
        layer_id: layers[layer_id].motions[0].params["rate"]
        for layer_id in ("bg_hook", "mg_hook", "fg_hook")
    }
    assert rates == {"bg_hook": 0.15, "mg_hook": 0.45, "fg_hook": 0.9}


def test_a_chapter_pack_omits_the_end_card_scene() -> None:
    pack = {
        "job_id": "chapter",
        "voice": {"voice_id": "sarvam:test"},
        "end_card": {"enabled": False},
        "scenes": [{"id": "hook", "still": {"key": "a", "prompt": "a"}, "lines": [{"say": "one"}]}],
    }
    alignment = {
        "duration": 2.0,
        "scenes": [
            {
                "id": "hook",
                "start": 0.0,
                "end": 2.0,
                "words": [{"text": "one", "start": 0.1, "end": 0.5}],
            }
        ],
    }
    composition = pack_to_composition(
        ContentPack.model_validate(pack),
        load_design("saju_series"),
        Alignment.model_validate(alignment),
        stills={"a": "a.png"},
    )
    assert [scene.id for scene in composition.scenes] == ["hook"]
    assert composition.duration == 2.0
