"""Tests for the Composition -> HyperFrames project compiler."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import pytest
from video_editor_core.compile_composition import compile_composition
from video_editor_core.composition import (
    AudioCue,
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
    MotionSpec,
    WordSpan,
)
from video_editor_core.designs import DesignTokens, Grade, Palette
from video_editor_core.motion import MotionError
from video_editor_core.render_graph import Rect


def _composition(tmp_path: Path, layers: list[CompositionLayer], **overrides) -> Composition:
    still = tmp_path / "still.png"
    still.write_bytes(b"png")
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"mp3")
    base = CompositionLayer(
        id="base_video",
        kind="media",
        preset="base",
        media_kind="image",
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=0.0, end=4.0),
        content=LayerContent(source=str(still)),
        track=0,
        z=0,
    )
    data = {
        "job_id": "job-1",
        "canvas": Canvas(width=1080, height=1920, fps=30),
        "duration": 4.0,
        "scenes": [CompositionScene(id="s0", start=0.0, end=4.0, layers=[base, *layers])],
        "audio": [],
    }
    data.update(overrides)
    return Composition(**data)  # type: ignore[arg-type]


def _text(layer_id: str = "title", **overrides) -> CompositionLayer:
    data = {
        "id": layer_id,
        "kind": "text",
        "rect": Rect(x=80, y=300, width=920, height=300),
        "time": LayerTime(start=0.0, end=4.0),
        "content": LayerContent(text="四柱"),
        "style": {"font": "Montserrat", "font_size": 72, "font_color": "#FFFFFF"},
    }
    data.update(overrides)
    return CompositionLayer(**data)  # type: ignore[arg-type]


def test_every_composition_embeds_the_indic_faces(tmp_path):
    # Telugu/Devanagari text can come from any layer (captions, typographic,
    # map, composite scenes), so the faces are embedded unconditionally.
    index = compile_composition(_composition(tmp_path, [_text()]), tmp_path / "proj")
    html = index.read_text(encoding="utf-8")
    assert "font-family: 'Noto Sans Telugu'" in html
    assert "font-family: 'Noto Sans Devanagari'" in html
    fonts = tmp_path / "proj" / "assets" / "fonts"
    assert (fonts / "NotoSansTelugu-Regular.ttf").is_file()
    assert (fonts / "NotoSansTelugu-Bold.ttf").is_file()
    assert "'Noto Sans Telugu'" in html.split("font-family:'Montserrat',")[1].split(";")[0]


def test_root_carries_the_composition_contract(tmp_path):
    index = compile_composition(_composition(tmp_path, [_text()]), tmp_path / "proj")
    html = index.read_text(encoding="utf-8")
    assert 'data-composition-id="main"' in html
    assert "data-no-timeline" in html
    assert 'data-width="1080"' in html and 'data-height="1920"' in html
    assert 'data-duration="4.000"' in html
    assert 'id="base_video"' in html
    assert "assets/still.png" in html


def test_every_visual_layer_is_a_root_level_clip(tmp_path):
    # ``z`` defaults to 0 and the base layer is 0 too, so a compiler that
    # hard-coded the index would still satisfy a bare "z-index:1" scan. Give
    # this layer a distinct z so the assertion can only pass when the emitted
    # value comes from the model.
    index = compile_composition(_composition(tmp_path, [_text(z=1)]), tmp_path / "proj")
    html = index.read_text(encoding="utf-8")
    assert 'id="title" class="clip" data-start="0.000" data-duration="4.000"' in html
    assert "z-index:1;" in html
    # The base layer is z=0; without this a compiler emitting 1 everywhere
    # would still satisfy the assertion above.
    assert "z-index:0;" in html


def test_text_layers_get_their_bundled_font_embedded(tmp_path):
    index = compile_composition(_composition(tmp_path, [_text()]), tmp_path / "proj")
    html = index.read_text(encoding="utf-8")
    assert "@font-face" in html
    assert "font-family:'Montserrat'," in html
    assert (tmp_path / "proj" / "assets" / "fonts" / "Montserrat.ttf").is_file()


def test_identical_motions_share_one_keyframes_block(tmp_path):
    motion = {"name": "stamp", "params": {}}
    layers = [
        _text("first", motions=[MotionSpec(**motion)]),
        _text(
            "second", rect=Rect(x=80, y=700, width=920, height=300), motions=[MotionSpec(**motion)]
        ),
    ]
    html = compile_composition(_composition(tmp_path, layers), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    assert html.count("@keyframes ve_stamp_") == 1


def test_motion_delay_reaches_the_layer_animation_shorthand(tmp_path):
    layer = _text(motions=[MotionSpec(name="rise", delay=0.25)])
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    # Pin the layer's own ``animation`` value: a bare " 0.250s" anywhere in the
    # document could match an unrelated time.
    line = next(line for line in html.splitlines() if 'id="title"' in line)
    assert "animation:" in line
    assert line.split("animation:", 1)[1].split(";", 1)[0].endswith(" 0.250s")


def test_motion_is_clamped_to_the_layer_clip_not_the_composition(tmp_path):
    # The layer runs 1.0 s inside a 4.0 s composition. With a 0.9 s delay only
    # 0.1 s is left, so a clamp derived from the composition duration would
    # emit 3.000s and let the animation run past its own clip end.
    layer = _text(
        time=LayerTime(start=0.0, end=1.0),
        motions=[MotionSpec(name="rise", delay=0.9, duration=3.0)],
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    line = next(line for line in html.splitlines() if 'id="title"' in line)
    animation = line.split("animation:", 1)[1].split(";", 1)[0]
    assert "0.100s" in animation
    assert animation.endswith(" 0.900s")


def test_a_count_primitive_animates_its_own_strip(tmp_path):
    layer = _text(
        content=LayerContent(text=""), motions=[MotionSpec(name="count", params={"to": 2})]
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    assert "ve-count-strip" in html
    assert "animation:" in html.split("ve-count-strip", 1)[1][:200]


def test_a_count_primitive_on_a_media_layer_is_an_error(tmp_path):
    # ``count`` renders its own strip; a media layer returns before the
    # container that would hold it, so the motion would silently do nothing.
    layer = CompositionLayer(
        id="clip",
        kind="media",
        media_kind="image",
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=0.0, end=4.0),
        content=LayerContent(source=str(tmp_path / "still.png")),
        motions=[MotionSpec(name="count", params={"to": 2})],
    )
    with pytest.raises(MotionError, match=r"layer 'clip'.*motion 'count'"):
        compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj")


def test_rich_text_emits_karaoke_spans(tmp_path):
    spans = [WordSpan(text="hello", start=0.4, end=0.8)]
    layer = _text(
        kind="rich_text",
        content=LayerContent(spans=spans),
        style={
            "font": "Montserrat",
            "font_size": 48,
            "highlight_color": "#FDB813",
        },
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    assert "ve_word_hl 0.400s step-end 0.400s" in html
    assert "--ve-hl:#FDB813" in html
    assert html.count('class="ve-word"') == 1


def test_audio_cues_become_root_level_audio(tmp_path):
    cue = AudioCue(id="narration", source=str(tmp_path / "narration.mp3"), track=150)
    comp = _composition(tmp_path, [_text()])
    comp = comp.model_copy(update={"audio": [cue]})
    html = compile_composition(comp, tmp_path / "proj").read_text(encoding="utf-8")
    assert html.count('<audio id="narration"') == 1
    # Pin the element's own timing as one contiguous run: the base image also
    # emits ``data-duration="4.000"``, so a bare substring proves nothing.
    audio_line = next(line for line in html.splitlines() if "<audio" in line)
    assert 'data-start="0.000" data-duration="4.000" data-track-index="150"' in audio_line
    assert "assets/narration.mp3" in html


def test_external_audio_mode_never_embeds_cues(tmp_path):
    # ``external`` means the caller muxes; the cues travel for ducking and
    # loudnorm, and embedding them too would double up the mix.
    cue = AudioCue(id="narration", source=str(tmp_path / "narration.mp3"), track=150)
    comp = _composition(tmp_path, [_text()], audio=[cue], audio_mode="external")
    html = compile_composition(comp, tmp_path / "proj").read_text(encoding="utf-8")
    assert "<audio" not in html
    assert 'id="title"' in html
    assert 'id="base_video"' in html


def test_extra_css_from_an_html_layer_reaches_the_stylesheet(tmp_path):
    layer = CompositionLayer(
        id="scene_0_typo",
        kind="html",
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=0.0, end=4.0),
        content=LayerContent(text="<div class='typo-0'>$32B</div>"),
        style={"background": "#EFEAE3", "extra_css": ".typo-0 { font-size: 120px; }"},
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    assert ".typo-0 { font-size: 120px; }" in html
    # Nested plate markup is mounted from a template, not inlined in the row.
    assert (
        "<template id=\"scene_0_typo-template\"><div class='typo-0'>$32B</div></template>" in html
    )
    assert "background:#EFEAE3" in html  # backgrounds come from style["background"]


def test_html_layers_do_not_inherit_caption_css(tmp_path):
    # ``html`` layers are engine-generated markup with their own stylesheet.
    # Caption CSS on the wrapper would leak into every glyph they render.
    layer = CompositionLayer(
        id="scene_0_typo",
        kind="html",
        preset="typographic",
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=0.0, end=4.0),
        content=LayerContent(text="<div class='typo-0'>$32B</div>"),
        style={"background": "#EFEAE3", "extra_css": ".typo-0 { font-size: 120px; }"},
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    line = next(line for line in html.splitlines() if 'id="scene_0_typo"' in line)
    assert "background:#EFEAE3;" in line
    # The row is an empty sub-composition host; the plate waits in a template.
    assert 'data-composition-id="scene_0_typo"' in line
    assert line.rstrip().endswith("></div>")
    assert 'class="typo-0"' not in line
    template = next(line for line in html.splitlines() if 'id="scene_0_typo-template"' in line)
    assert "<div class='typo-0'>$32B</div>" in template
    assert ".typo-0 { font-size: 120px; }" in html
    # Scoped to the wrapper: the stylesheet legitimately carries font-size.
    assert "-webkit-text-stroke" not in line
    assert "text-shadow" not in line
    assert "font-size:" not in line


def test_a_nested_layer_is_mounted_from_a_template_outside_the_root(tmp_path):
    """HyperFrames' lint allows only inline tags in a timeline row.

    The sanctioned shape is a sub-composition: an empty host plus a template
    the runtime mounts. The template must sit outside ``#root`` or the root
    composition would hold a nested element again.
    """
    layer = CompositionLayer(
        id="scene_0_typo",
        kind="html",
        preset="typographic",
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=0.0, end=4.0),
        content=LayerContent(text="<div class='typo-0'>$32B</div>"),
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    line = next(line for line in html.splitlines() if 'id="scene_0_typo"' in line)
    assert "data-no-timeline" in line  # a CSS-only composition, like the root
    # Every template is emitted after the root composition closes.
    assert html.index('id="scene_0_typo-template"') > html.rindex("    </div>")


def test_a_plain_text_layer_stays_inline(tmp_path):
    layer = CompositionLayer(
        id="title",
        kind="text",
        rect=Rect(x=0, y=0, width=1080, height=200),
        time=LayerTime(start=0.0, end=4.0),
        content=LayerContent(text="Strait of Malacca"),
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    line = next(line for line in html.splitlines() if 'id="title"' in line)
    assert "Strait of Malacca" in line
    assert "data-composition-id" not in line
    assert "<template" not in html


def test_a_placeholder_gets_only_its_own_rule(tmp_path):
    layer = CompositionLayer(
        id="scene_0_placeholder",
        kind="html",
        preset="placeholder",
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=0.0, end=4.0),
        content=LayerContent(text="no media here"),
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    line = next(line for line in html.splitlines() if 'id="scene_0_placeholder"' in line)
    assert "display:flex" in line
    assert "no media here" in line
    assert "-webkit-text-stroke" not in line
    assert "text-shadow" not in line


def test_a_scene_transition_extends_the_outgoing_clip_and_fades_both_sides(tmp_path):
    composition = Composition(
        job_id="job-1",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=4.0,
        scenes=[
            CompositionScene(
                id="s0",
                start=0.0,
                end=2.0,
                layers=[_text("outgoing", time=LayerTime(start=0.0, end=2.0))],
            ),
            CompositionScene(
                id="s1",
                start=2.0,
                end=4.0,
                layers=[_text("incoming", time=LayerTime(start=2.0, end=4.0))],
                transition_in=MotionSpec(name="crossfade", duration=0.5),
            ),
        ],
    )
    html = compile_composition(composition, tmp_path / "proj").read_text(encoding="utf-8")
    outgoing = next(line for line in html.splitlines() if 'id="outgoing"' in line)
    incoming = next(line for line in html.splitlines() if 'id="incoming"' in line)

    # The outgoing clip keeps painting for the transition window...
    assert 'data-duration="2.500"' in outgoing
    # ...and fades out over it, while the incoming clip fades in.
    assert "ve_fade_out_" in outgoing.split("animation:", 1)[1].split(";", 1)[0]
    assert "ve_fade_" in incoming.split("animation:", 1)[1].split(";", 1)[0]
    assert "linear" in outgoing.split("animation:", 1)[1].split(";", 1)[0]
    assert "ve_fade_out_" not in incoming


def _video_layer(tmp_path: Path, **style: Any) -> CompositionLayer:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"mp4")
    return CompositionLayer(
        id="clip",
        kind="media",
        media_kind="video",
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=0.0, end=4.0),
        content=LayerContent(source=str(clip)),
        style=dict(style),
    )


def test_a_video_can_be_slowed_and_trimmed_from_style(tmp_path):
    layer = _video_layer(tmp_path, playback_rate=0.5, media_start=1.25)
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    line = next(line for line in html.splitlines() if 'id="clip"' in line)
    assert 'data-playback-rate="0.5"' in line
    assert 'data-media-start="1.25"' in line


def test_default_playback_values_are_not_emitted(tmp_path):
    layer = _video_layer(tmp_path, playback_rate=1.0, media_start=0.0)
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    line = next(line for line in html.splitlines() if 'id="clip"' in line)
    assert "data-playback-rate" not in line
    assert "data-media-start" not in line


def test_an_image_ignores_playback_attributes(tmp_path):
    still = tmp_path / "still.png"
    still.write_bytes(b"png")
    layer = CompositionLayer(
        id="still",
        kind="media",
        media_kind="image",
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=0.0, end=4.0),
        content=LayerContent(source=str(still)),
        style={"playback_rate": 0.5, "media_start": 1.0},
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    line = next(line for line in html.splitlines() if 'id="still"' in line)
    assert "data-playback-rate" not in line
    assert "data-media-start" not in line


@pytest.mark.parametrize(
    ("style", "message"),
    [
        ({"playback_rate": 0.0}, "playback_rate must be 0.1..10"),
        ({"playback_rate": 11.0}, "playback_rate must be 0.1..10"),
        ({"playback_rate": "fast"}, "playback_rate must be a number"),
        ({"media_start": -1.0}, "media_start must be >= 0"),
    ],
)
def test_invalid_playback_values_are_refused(tmp_path, style, message):
    layer = _video_layer(tmp_path, **style)
    with pytest.raises(ValueError, match=message):
        compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj")


def test_text_padding_and_top_alignment_are_emitted(tmp_path):
    layer = _text(style={"valign": "top", "padding": 20})
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    line = next(line for line in html.splitlines() if 'id="title"' in line)
    assert "align-items:flex-start;" in line
    assert "padding:20px;box-sizing:border-box;" in line


def test_text_alignment_defaults_to_centred(tmp_path):
    html = compile_composition(_composition(tmp_path, [_text()]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    line = next(line for line in html.splitlines() if 'id="title"' in line)
    assert "align-items:center;" in line
    assert "padding:" not in line


def test_a_hugged_text_background_moves_to_an_inner_span(tmp_path):
    layer = _text(
        style={
            "background": "rgba(0,0,0,0.5)",
            "background_hug": True,
            "padding": 20,
            "valign": "top",
        }
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    line = next(line for line in html.splitlines() if 'id="title"' in line)
    before, inner = line.split("<span", 1)
    # The layer stays transparent so the box can shrink to the text.
    assert "background:" not in before
    assert "padding:" not in before
    assert 'class="ve-box"' in inner
    assert "display:inline-block;background:rgba(0,0,0,0.5);padding:20px" in inner


def _design(grade: Grade) -> DesignTokens:
    return DesignTokens(
        name="graded",
        palette=Palette(bg="#000000", ink="#FFFFFF", accent="#FFAA00", muted="#888888"),
        grade=grade,
    )


def _grading_payload(html_text: str) -> dict:
    match = re.search(r'data-color-grading="([^"]*)"', html_text)
    assert match is not None, "no data-color-grading attribute"
    return json.loads(html.unescape(match.group(1)))


def test_a_design_grade_is_emitted_on_the_media_layer(tmp_path):
    comp = _composition(
        tmp_path,
        [_text()],
        design=_design(Grade(details={"vignette": 0.12, "grain": 0.1})),
    )
    html_text = compile_composition(comp, tmp_path / "proj").read_text(encoding="utf-8")
    assert _grading_payload(html_text) == {
        "intensity": 1.0,
        "details": {"vignette": 0.12, "grain": 0.1},
    }


def test_a_layer_grade_overrides_the_design(tmp_path):
    comp = _composition(
        tmp_path,
        [_text()],
        design=_design(Grade(details={"vignette": 0.12})),
    )
    scene = comp.scenes[0]
    base = scene.layers[0].model_copy(update={"style": {"grade": {"details": {"vignette": 0.3}}}})
    comp = comp.model_copy(
        update={"scenes": [scene.model_copy(update={"layers": [base, *scene.layers[1:]]})]}
    )
    html_text = compile_composition(comp, tmp_path / "proj").read_text(encoding="utf-8")
    assert _grading_payload(html_text)["details"] == {"vignette": 0.3}


def test_a_grade_lut_is_copied_and_referenced(tmp_path):
    lut = tmp_path / "look.cube"
    lut.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
    comp = _composition(
        tmp_path,
        [_text()],
        design=_design(Grade(lut=str(lut), lut_intensity=0.8)),
    )
    html_text = compile_composition(comp, tmp_path / "proj").read_text(encoding="utf-8")
    assert (tmp_path / "proj" / "assets" / "look.cube").is_file()
    assert _grading_payload(html_text)["lut"] == {"src": "assets/look.cube", "intensity": 0.8}


def test_a_missing_grade_lut_is_refused(tmp_path):
    comp = _composition(
        tmp_path,
        [_text()],
        design=_design(Grade(lut=str(tmp_path / "missing.cube"))),
    )
    with pytest.raises(ValueError, match="LUT not found"):
        compile_composition(comp, tmp_path / "proj")


def test_a_media_mask_is_copied_and_emitted(tmp_path: Path):
    mask = tmp_path / "near.png"
    mask.write_bytes(b"png")
    masked = CompositionLayer(
        id="masked",
        kind="media",
        preset="base",
        media_kind="image",
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=0.0, end=4.0),
        content=LayerContent(source=str(tmp_path / "still.png")),
        style={"mask": str(mask)},
        track=0,
        z=1,
    )
    index = compile_composition(_composition(tmp_path, [masked]), tmp_path / "proj")
    html = index.read_text(encoding="utf-8")
    assert "mask-image:url(assets/near.png)" in html
    assert "-webkit-mask-image:url" in html
    assert (tmp_path / "proj" / "assets" / "near.png").is_file()


def test_a_mask_on_a_non_media_layer_is_refused(tmp_path: Path):
    mask = tmp_path / "near.png"
    mask.write_bytes(b"png")
    with pytest.raises(ValueError, match="only supported on media"):
        compile_composition(
            _composition(tmp_path, [_text(style={"mask": str(mask)})]),
            tmp_path / "proj",
        )


def test_type_on_splits_text_into_staggered_spans(tmp_path: Path) -> None:
    layer = _text(
        "title",
        content=LayerContent(text="Hi"),
        motions=[MotionSpec(name="type_on", params={"step": 0.05})],
    )
    index = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj")
    page = index.read_text(encoding="utf-8")
    assert "@keyframes ve_type_char" in page
    assert page.count("ve_type_char .01s linear") == 2
    assert "0.050s" in page
    assert "0.000s both" in page
