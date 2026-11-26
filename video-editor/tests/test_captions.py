"""Tests for the captions skill."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from captions.captions import CaptionsSkill, caption_graph_layers
from captions.models import CaptionLine, CaptionsError, CaptionStyle, CaptionWord
from captions.timing import generate_caption_words, generate_segment_caption_lines
from graphics.graphics import GraphicsSkill
from graphics.models import Brand
from video_editor_core.render_graph import Rect as GraphRect
from video_editor_core.timeline import EDL, compute_timeline_segments
from video_editor_ffmpeg.probe import probe_media
from video_editor_pipelines.caption_qa import caption_tokens_check
from video_editor_state.job import JobContext


def _make_brand(
    *,
    font: str = "Coolvetica",
    color: str = "#FFFFFF",
    animation: str = "pop_in",
) -> Brand:
    return Brand(
        brand="test-brand",
        colors={"primary": "#000", "secondary": "#111", "background": "#222", "text": color},
        fonts={"headline": font, "body": "Inter", "captions": font},
        logo={"path": "x.png", "width": 1, "height": 1},
        defaults={"caption_animation": animation, "music_duck_db": -14, "final_loudness": -16},
    )


def _build_transcript(words: list[tuple[str, float, float]]) -> dict[str, object]:
    return {
        "source_audio": "",
        "language": "en",
        "language_probability": 1.0,
        "word_timestamps": True,
        "segments": [],
        "words": [{"word": w, "start": s, "end": e, "probability": 0.95} for w, s, e in words],
    }


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="captions-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


def test_generate_caption_words_remapped_to_cuts() -> None:
    transcript = _build_transcript(
        [
            ("Hello", 0.0, 0.4),
            ("um", 0.5, 0.7),
            ("world", 0.8, 1.2),
            ("again", 2.2, 2.6),
        ]
    )
    segments = compute_timeline_segments(
        [
            (0.0, 0.45, "intro"),
            (2.0, 3.0, "outro"),
        ]
    )
    edl = EDL(job_id="c", source_path="raw.mp4", segments=segments)
    words = generate_caption_words(transcript, edl)

    texts = [w.text for w in words]
    assert "um" not in texts
    assert "world" not in texts
    assert "Hello" in texts
    assert "again" in texts

    # Words after the first cut should be shifted back by the removed gap.
    # First segment duration is 0.45, so again (originally at 2.2) lands at 0.65.
    again = next(w for w in words if w.text == "again")
    assert again.start == pytest.approx(0.65, abs=1e-3)


def test_generate_segment_caption_lines() -> None:
    words = [
        CaptionWord(text=f"w{i}", start=i * 0.5, end=i * 0.5 + 0.4, confidence=0.9)
        for i in range(20)
    ]
    style = CaptionStyle(max_chars=20)
    lines = generate_segment_caption_lines(words, 0.0, 10.0, style)

    assert lines
    for line in lines:
        assert line.text
        assert line.start < line.end
        assert len(line.text) <= style.max_chars + 5  # allow small overflow from last word


def test_a_sentence_end_forces_a_new_caption_line() -> None:
    """A line must not carry the first words of the next sentence."""
    words = [
        CaptionWord(text="Korean", start=0.0, end=0.2),
        CaptionWord(text="Masters.", start=0.2, end=0.5),
        CaptionWord(text="Wait", start=0.6, end=0.8),
        CaptionWord(text="for", start=0.8, end=0.9),
        CaptionWord(text="yours.", start=0.9, end=1.2),
        CaptionWord(text="Yang", start=1.3, end=1.5),
    ]
    lines = generate_segment_caption_lines(words, 0.0, 2.0, CaptionStyle())

    assert [" ".join(word.text for word in line.words) for line in lines] == [
        "Korean Masters.",
        "Wait for yours.",
        "Yang",
    ]


def test_a_question_or_ellipsis_also_ends_the_line() -> None:
    words = [
        CaptionWord(text="A", start=0.0, end=0.1),
        CaptionWord(text="Rat?", start=0.1, end=0.4),
        CaptionWord(text="Not", start=0.5, end=0.7),
        CaptionWord(text="yet…", start=0.7, end=1.0),
        CaptionWord(text="Next", start=1.1, end=1.3),
    ]
    lines = generate_segment_caption_lines(words, 0.0, 2.0, CaptionStyle())

    assert [" ".join(word.text for word in line.words) for line in lines] == [
        "A Rat?",
        "Not yet…",
        "Next",
    ]


def _caption_words(*texts: str) -> list[CaptionWord]:
    """Build 0.3s words spaced 0.35s apart (0.05s gaps, well under max_gap)."""
    return [
        CaptionWord(text=text, start=i * 0.35, end=i * 0.35 + 0.3) for i, text in enumerate(texts)
    ]


def _line_texts(lines: list[CaptionLine]) -> list[str]:
    return [" ".join(word.text for word in line.words) for line in lines]


# With max_chars=10 greedy produces 8 frames whose last one is the orphan "us.":
# [The Thar][desert][lies to][the west][and the][Ganga][plain to][us.]
_THAR_WORDS = (
    "The",
    "Thar",
    "desert",
    "lies",
    "to",
    "the",
    "west",
    "and",
    "the",
    "Ganga",
    "plain",
    "to",
    "us.",
)


def test_caption_lines_balance_a_one_word_short_tail() -> None:
    """A greedy short tail ("us.") is redistributed instead of left orphaned."""
    words = _caption_words(*_THAR_WORDS)
    lines = generate_segment_caption_lines(words, 0.0, 10.0, CaptionStyle(max_chars=10))
    texts = _line_texts(lines)

    assert len(texts) == 8
    assert "us." not in texts
    for line in lines:
        single_short_word = len(line.words) == 1 and sum(c.isalpha() for c in line.text) < 4
        assert not single_short_word, texts


def test_caption_lines_balanced_partition_keeps_greedy_frame_count() -> None:
    """Balancing never adds frames: same count as the greedy grouping."""
    words = _caption_words(*_THAR_WORDS)
    lines = generate_segment_caption_lines(words, 0.0, 10.0, CaptionStyle(max_chars=10))

    # Greedy uses 8 frames for these words at max_chars=10 (see _THAR_WORDS).
    assert len(lines) == 8


def test_sentence_ends_and_large_gaps_still_break_lines() -> None:
    """Hard breaks are untouched by the balancing pass."""
    words = [
        CaptionWord(text="The", start=0.0, end=0.2),
        CaptionWord(text="Thar", start=0.2, end=0.4),
        CaptionWord(text="desert.", start=0.4, end=0.7),
        # Sentence end, and also a large gap before the next word.
        CaptionWord(text="Next", start=2.2, end=2.4),
        # Large gap inside a sentence (no sentence end).
        CaptionWord(text="words", start=4.0, end=4.2),
        CaptionWord(text="here", start=4.2, end=4.4),
    ]
    lines = generate_segment_caption_lines(words, 0.0, 6.0, CaptionStyle())

    assert _line_texts(lines) == ["The Thar desert.", "Next", "words here"]


def test_caption_lines_prefer_breaking_after_a_comma_when_costs_tie() -> None:
    """Equal-slack partitions prefer the frame ending on the comma."""
    words = _caption_words("The", "cats,", "is", "beautiful")
    lines = generate_segment_caption_lines(words, 0.0, 5.0, CaptionStyle(max_chars=12))

    # Both partitions use 2 frames with equal slack:
    #   ["The cats, is"][beautiful] vs ["The cats,"]["is beautiful"].
    assert _line_texts(lines) == ["The cats,", "is beautiful"]


def test_a_short_number_frame_is_merged_into_the_previous_line() -> None:
    """₹583. is long to say, so it fills a frame, but it is still an orphan."""
    words = [
        CaptionWord(text="రోజుకి", start=0.0, end=0.5),
        CaptionWord(text="₹583.", start=0.55, end=2.7),
        CaptionWord(text="ఇది", start=2.75, end=3.0),
        CaptionWord(text="మెయిన్", start=3.0, end=3.4),
        CaptionWord(text="రోడ్", start=3.4, end=3.7),
        CaptionWord(text="మీద", start=3.7, end=4.0),
    ]
    lines = generate_segment_caption_lines(words, 0.0, 5.0, CaptionStyle())
    texts = _line_texts(lines)
    script = "రోజుకి ₹583. ఇది మెయిన్ రోడ్ మీద"

    assert caption_tokens_check(texts, script).ok, texts
    assert any("రోజుకి" in line and "₹583." in line for line in texts), texts


def test_a_leading_short_frame_is_merged_into_the_next_line() -> None:
    """An orphan that opens the run has no previous line, so it joins the next."""
    words = [
        CaptionWord(text="₹583", start=0.0, end=2.7),
        CaptionWord(text="ఇది", start=2.75, end=3.0),
        CaptionWord(text="మెయిన్", start=3.0, end=3.4),
        CaptionWord(text="రోడ్", start=3.4, end=3.7),
        CaptionWord(text="మీద", start=3.7, end=4.0),
    ]
    lines = generate_segment_caption_lines(words, 0.0, 5.0, CaptionStyle())
    texts = _line_texts(lines)
    script = "₹583 ఇది మెయిన్ రోడ్ మీద"

    assert caption_tokens_check(texts, script).ok, texts
    assert texts[0].startswith("₹583 ఇది"), texts


def test_captions_skill_run(ctx: JobContext, tmp_media: Path) -> None:
    transcript_path = ctx.artifact("transcript", "transcript.json")
    transcript_path.write_text(
        json.dumps(
            _build_transcript(
                [
                    ("Hello", 0.1, 0.5),
                    ("world", 0.6, 1.0),
                    ("again", 2.1, 2.5),
                ]
            )
        ),
        encoding="utf-8",
    )

    segments = compute_timeline_segments(
        [
            (0.0, 1.5, "intro"),
            (2.0, 3.0, "outro"),
        ]
    )
    edl = EDL(job_id=ctx.job_id, source_path=str(tmp_media), segments=segments)
    ctx.artifact("plan", "edl.json").write_text(json.dumps(edl.model_dump()), encoding="utf-8")

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    graphics_skill.run(
        preset_name="short_explainer",
        edl=edl,
        source_video=tmp_media,
        transcript_path=transcript_path,
    )

    first_output = probe_media(ctx.work_dir / "graphics" / "with_graphics.mp4")
    first_duration = first_output.duration

    captions_skill = CaptionsSkill(ctx, graphics_skill=graphics_skill)
    output = captions_skill.run()

    assert output.exists()
    info = probe_media(output)
    assert info.duration == pytest.approx(first_duration, abs=0.5)

    # Plan should now contain timed caption layers.
    plan_path = ctx.artifact("graphics", "plan.json")
    with open(plan_path, encoding="utf-8") as f:
        plan_data = json.load(f)
    caption_layers = [
        layer
        for seg in plan_data["segments"]
        for layer in seg["layers"]
        if layer["name"].startswith("caption_line")
    ]
    assert caption_layers


def test_captions_skill_errors_on_empty_transcript(ctx: JobContext, tmp_media: Path) -> None:
    """Regression for Issue C: CaptionsSkill silently emitted the graphics-only
    output (byte-identical to with_graphics.mp4) when the transcript had 0
    words — the pipeline produced a caption-less video with no signal that
    anything was wrong. It must now raise a clear error so the failure surfaces
    at the captions stage instead of shipping a silently-broken deliverable.
    """
    # Empty transcript (0 words) — what WhisperX produces on silent source
    # audio when no alignment.json was promoted.
    transcript_path = ctx.artifact("transcript", "transcript.json")
    transcript_path.write_text(json.dumps(_build_transcript([])), encoding="utf-8")

    segments = compute_timeline_segments(
        [
            (0.0, 1.5, "intro"),
            (2.0, 3.0, "outro"),
        ]
    )
    edl = EDL(job_id=ctx.job_id, source_path=str(tmp_media), segments=segments)
    ctx.artifact("plan", "edl.json").write_text(json.dumps(edl.model_dump()), encoding="utf-8")

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    graphics_skill.run(
        preset_name="short_explainer",
        edl=edl,
        source_video=tmp_media,
        transcript_path=transcript_path,
    )

    captions_skill = CaptionsSkill(ctx, graphics_skill=graphics_skill)
    with pytest.raises(CaptionsError, match="transcript"):
        captions_skill.run()


def test_captions_skill_cache_reuse(
    ctx: JobContext, tmp_media: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.INFO)

    transcript_path = ctx.artifact("transcript", "transcript.json")
    transcript_path.write_text(
        json.dumps(
            _build_transcript(
                [
                    ("Hello", 0.1, 0.5),
                    ("world", 0.6, 1.0),
                ]
            )
        ),
        encoding="utf-8",
    )

    segments = compute_timeline_segments(
        [
            (0.0, 1.0, "intro"),
        ]
    )
    edl = EDL(job_id=ctx.job_id, source_path=str(tmp_media), segments=segments)
    ctx.artifact("plan", "edl.json").write_text(json.dumps(edl.model_dump()), encoding="utf-8")

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    graphics_skill.run(
        preset_name="short_explainer",
        edl=edl,
        source_video=tmp_media,
        transcript_path=transcript_path,
    )

    captions_skill = CaptionsSkill(ctx, graphics_skill=graphics_skill)
    captions_skill.run()

    # Re-running with identical inputs should hit the cache for the segment render.
    caplog.clear()
    captions_skill2 = CaptionsSkill(
        ctx, graphics_skill=GraphicsSkill(ctx, presets_dir=Path("presets"))
    )
    captions_skill2.run()

    assert "cache_hit" in caplog.text
    assert "ffmpeg_fallback_render" not in caplog.text


def _load_caption_rects(ctx: JobContext) -> list[dict[str, Any]]:
    """Return every caption_line rect from the saved graphics plan."""
    plan_path = ctx.artifact("graphics", "plan.json")
    with open(plan_path, encoding="utf-8") as f:
        plan_data = json.load(f)
    return [
        layer["rect"]
        for seg in plan_data["segments"]
        for layer in seg["layers"]
        if layer["name"].startswith("caption_line")
    ]


def test_captions_bottom_padding_default_preserves_preset_rect(
    ctx: JobContext, tmp_media: Path
) -> None:
    """bottom_padding=0 (the default) leaves the rect at the preset's
    y-coordinate so existing renders are byte-identical to pre-change."""
    transcript_path = ctx.artifact("transcript", "transcript.json")
    transcript_path.write_text(
        json.dumps(
            _build_transcript(
                [
                    ("Hello", 0.1, 0.5),
                    ("world", 0.6, 1.0),
                ]
            )
        ),
        encoding="utf-8",
    )
    segments = compute_timeline_segments([(0.0, 1.0, "intro")])
    edl = EDL(job_id=ctx.job_id, source_path=str(tmp_media), segments=segments)
    ctx.artifact("plan", "edl.json").write_text(json.dumps(edl.model_dump()), encoding="utf-8")

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    graphics_skill.run(
        preset_name="short_explainer",
        edl=edl,
        source_video=tmp_media,
        transcript_path=transcript_path,
    )

    # Default style: bottom_padding is 0 -> rect.y matches the preset.
    captions_skill = CaptionsSkill(ctx, graphics_skill=graphics_skill)
    captions_skill.run()

    rects = _load_caption_rects(ctx)
    assert rects, "expected at least one caption_line rect"
    # short_explainer preset pins captions at y=880, h=160 on a 1080x1920 canvas.
    assert all(r["y"] == 880 for r in rects)
    assert all(r["height"] == 160 for r in rects)


def test_captions_bottom_padding_pulls_band_to_bottom(ctx: JobContext, tmp_media: Path) -> None:
    """bottom_padding>0 pulls the caption band down so the bottom edge
    of the band sits `bottom_padding` pixels above the canvas bottom."""
    transcript_path = ctx.artifact("transcript", "transcript.json")
    transcript_path.write_text(
        json.dumps(
            _build_transcript(
                [
                    ("Hello", 0.1, 0.5),
                    ("world", 0.6, 1.0),
                ]
            )
        ),
        encoding="utf-8",
    )
    segments = compute_timeline_segments([(0.0, 1.0, "intro")])
    edl = EDL(job_id=ctx.job_id, source_path=str(tmp_media), segments=segments)
    ctx.artifact("plan", "edl.json").write_text(json.dumps(edl.model_dump()), encoding="utf-8")

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    graphics_skill.run(
        preset_name="short_explainer",
        edl=edl,
        source_video=tmp_media,
        transcript_path=transcript_path,
    )

    style = CaptionStyle(bottom_padding=200)
    captions_skill = CaptionsSkill(ctx, graphics_skill=graphics_skill, style=style)
    captions_skill.run()

    rects = _load_caption_rects(ctx)
    assert rects, "expected at least one caption_line rect"
    # 1080x1920 canvas, h=160, bottom_padding=200 -> y = 1920 - 200 - 160 = 1560.
    expected_y = 1920 - 200 - 160
    assert all(r["y"] == expected_y for r in rects), rects
    # band height is preserved
    assert all(r["height"] == 160 for r in rects)


def test_caption_style_from_brand_uses_brand_fields() -> None:
    """from_brand() reads fonts.captions, colors.text, defaults.caption_animation."""
    brand = _make_brand(font="HelveticaNeue", color="#FFAA00", animation="slide_in")
    style = CaptionStyle.from_brand(brand)
    assert style.font == "HelveticaNeue"
    assert style.font_color == "#FFAA00"
    assert style.animation == "slide_in"
    # Other fields keep their class defaults (font_size, border, etc).
    assert style.font_size == CaptionStyle().font_size
    assert style.border_width == 4


def test_caption_style_from_brand_falls_back_to_defaults() -> None:
    """A brand missing a key falls back to CaptionStyle's default."""
    sparse_brand = Brand(
        brand="sparse",
        colors={"text": "#ABCDEF"},
        fonts={"captions": "Inter"},
        logo={"path": "", "width": 0, "height": 0},
        defaults={},
    )
    style = CaptionStyle.from_brand(sparse_brand)
    assert style.font == "Inter"
    assert style.font_color == "#ABCDEF"
    # animation was not in the brand's defaults -> CaptionStyle default.
    assert style.animation == "karaoke"


def test_caption_style_default_is_karaoke() -> None:
    """Brandless captions default to the karaoke look (user preference)."""
    assert CaptionStyle() == CaptionStyle.preset("karaoke")


def test_caption_style_pop_in_preset_restores_legacy_look() -> None:
    """A brand asking for ``pop_in`` keeps the original Coolvetica style."""
    style = CaptionStyle.from_brand(
        _make_brand(font="Coolvetica", color="#FFFFFF", animation="pop_in")
    )
    assert style.animation == "pop_in"
    assert style.font_size == 48
    assert style.text_effect == "stroke"
    assert style.upcoming_opacity == 1.0


def test_captions_skill_brand_kwarg_derives_style(
    tmp_path: Path,
) -> None:
    """C28 regression: CaptionsSkill(brand=...) must build its style from
    the brand via the real constructor, not a test-side reconstruction."""
    brand = _make_brand(font="HelveticaNeue", color="#FFAA00")
    ctx = JobContext(
        job_id="brand-captions",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    graphics = GraphicsSkill(ctx)
    skill = CaptionsSkill(ctx, graphics_skill=graphics, brand=brand)
    assert skill.style.font == "HelveticaNeue"
    assert skill.style.font_color == "#FFAA00"


# ---------------------------------------------------------------------------
# Karaoke captions
# ---------------------------------------------------------------------------


def _line(*words: tuple[str, float, float]) -> CaptionLine:
    return CaptionLine(words=[CaptionWord(text=t, start=s, end=e) for t, s, e in words])


def test_caption_style_karaoke_preset() -> None:
    style = CaptionStyle.preset("karaoke")
    assert style.animation == "karaoke"
    assert style.font == "Montserrat"
    assert style.text_effect == "shadow"
    assert style.highlight_color == "#FDB813"
    assert CaptionStyle.preset("karaoke", font_size=40).font_size == 40
    with pytest.raises(ValueError):
        CaptionStyle.preset("nope")


def test_caption_style_from_brand_karaoke_starts_from_preset() -> None:
    brand = _make_brand(font="Montserrat", color="#FFFFFF", animation="karaoke")
    style = CaptionStyle.from_brand(brand)
    assert style.animation == "karaoke"
    assert style.text_effect == "shadow"
    assert style.font_weight == 800


def test_caption_graph_layers_karaoke_words_and_linger() -> None:
    lines = [
        _line(("Four", 1.0, 1.3), ("giants", 1.35, 1.8)),
        _line(("of", 1.9, 2.0), ("gas", 2.05, 2.4)),
    ]
    rect = GraphRect(x=0, y=900, width=1920, height=120)
    layers = caption_graph_layers(lines, rect, CaptionStyle.preset("karaoke"), offset=10.0)

    assert [layer.text for layer in layers] == ["Four giants", "of gas"]
    first = layers[0]
    assert first.timing.start == pytest.approx(11.0)
    # linger (0.15s) is clamped so a line never overlaps the next one.
    assert first.timing.end == pytest.approx(11.9)
    assert layers[1].timing.end == pytest.approx(12.55)
    words = first.style["words"]
    assert words == [
        {"text": "Four", "start": 0.0, "end": pytest.approx(0.3)},
        {"text": "giants", "start": pytest.approx(0.35), "end": pytest.approx(0.8)},
    ]
    assert first.style["highlight_color"] == "#FDB813"
    assert first.style["text_effect"] == "shadow"
    assert first.style["requires_motion"] is True  # routes the graph to HyperFrames


def test_caption_graph_layers_plain_style_has_no_word_timing() -> None:
    lines = [_line(("Hello", 0.0, 0.5))]
    rect = GraphRect(x=0, y=900, width=1920, height=120)
    layer = caption_graph_layers(lines, rect, CaptionStyle.preset("pop_in"))[0]
    assert "words" not in layer.style
    assert "requires_motion" not in layer.style
    assert layer.timing.end == pytest.approx(0.5)
