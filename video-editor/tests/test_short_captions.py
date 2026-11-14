from __future__ import annotations

import pytest
from video_editor_core.composition import Canvas
from video_editor_core.content_pack import Alignment
from video_editor_core.designs import load_design
from video_editor_core.layer_presets import caption as caption_placement
from video_editor_pipelines.short_captions import caption_layers, caption_style

CANVAS = Canvas(width=1080, height=1920, fps=30)

# Four words with a long tail on the last one: without a clamp the linger of
# line one would run past the start of line two.
ALIGNMENT = {
    "duration": 4.0,
    "scenes": [
        {
            "id": "hook",
            "start": 0.0,
            "end": 4.0,
            "words": [
                {"text": "You", "start": 0.1, "end": 0.5},
                {"text": "think", "start": 0.5, "end": 1.0},
                {"text": "you're", "start": 1.0, "end": 1.6},
                {"text": "a", "start": 3.6, "end": 3.9},
            ],
        }
    ],
}


@pytest.fixture
def tokens():
    return load_design("saju_series")


def build(tokens):
    return caption_layers(
        tokens,
        Alignment.model_validate(ALIGNMENT),
        canvas=CANVAS,
    )


def test_a_sentence_end_starts_a_new_caption_layer(tokens) -> None:
    """The short path must not show "Masters. Wait for" in one frame."""
    alignment = Alignment.model_validate(
        {
            "duration": 2.0,
            "scenes": [
                {
                    "id": "hook",
                    "start": 0.0,
                    "end": 2.0,
                    "words": [
                        {"text": "Masters.", "start": 0.1, "end": 0.5},
                        {"text": "Wait", "start": 0.6, "end": 0.8},
                        {"text": "for", "start": 0.8, "end": 0.9},
                    ],
                }
            ],
        }
    )

    layers = caption_layers(tokens, alignment, canvas=CANVAS)

    texts = [" ".join(span.text for span in layer.content.spans) for layer in layers]
    assert texts == ["Masters.", "Wait for"]


def test_a_display_spelling_wins_over_the_transcript(tokens) -> None:
    """A ``speak_as`` word is captioned with the pack's spelling."""
    alignment = Alignment.model_validate(
        {
            "duration": 2.0,
            "scenes": [
                {
                    "id": "hook",
                    "start": 0.0,
                    "end": 2.0,
                    "words": [
                        {
                            "text": "Sah-joo",
                            "display": "Saju",
                            "start": 0.1,
                            "end": 0.6,
                        }
                    ],
                }
            ],
        }
    )

    layers = caption_layers(tokens, alignment, canvas=CANVAS)

    texts = [span.text for layer in layers for span in layer.content.spans]
    assert "Saju" in texts
    assert "Sah-joo" not in texts


def test_the_style_reads_the_design(tokens) -> None:
    style = caption_style(tokens)
    assert style.font == "Inter"
    assert style.font_size == 64
    assert style.font_weight == 700
    assert style.highlight_color == "#F59E0B"
    assert style.animation == "karaoke"
    assert style.max_chars == 34


def test_caption_layers_use_the_designed_box(tokens) -> None:
    layers = build(tokens)
    assert layers, "the alignment produced no caption lines"
    placed = caption_placement(tokens, CANVAS)
    assert all(layer.rect == placed.rect for layer in layers)
    assert all(layer.kind == "rich_text" for layer in layers)
    assert all(layer.preset == "caption_line" for layer in layers)
    assert all(layer.z == 40 for layer in layers)
    assert layers[0].style["highlight_color"] == "#F59E0B"


def test_word_spans_are_relative_to_their_line(tokens) -> None:
    layers = build(tokens)
    first = layers[0]
    assert first.content.spans[0].start == pytest.approx(0.0, abs=1e-6)
    assert all(span.start < span.end for span in first.content.spans)
    duration = first.time.end - first.time.start
    assert all(span.end <= duration + 1e-6 for span in first.content.spans)


def test_a_linger_never_runs_past_the_next_line(tokens) -> None:
    layers = build(tokens)
    for current, following in zip(layers, layers[1:], strict=False):
        assert current.time.end <= following.time.start


def test_a_linger_is_clamped_when_the_next_line_starts_within_it(tokens) -> None:
    # The fixture above splits on a 2 s gap, so its linger could never reach
    # line two. Six contiguous words instead split on max_duration (2.6 s),
    # leaving line two starting 0.15 s after line one's last word ends — inside
    # the linger — and the last line's linger would run past the duration.
    alignment = Alignment.model_validate(
        {
            "duration": 3.0,
            "scenes": [
                {
                    "id": "rapid",
                    "start": 0.0,
                    "end": 3.0,
                    "words": [
                        {"text": "one", "start": 0.0, "end": 0.6},
                        {"text": "two", "start": 0.6, "end": 1.2},
                        {"text": "three", "start": 1.2, "end": 1.8},
                        {"text": "four", "start": 1.8, "end": 2.4},
                        {"text": "five", "start": 2.4, "end": 3.0},
                    ],
                }
            ],
        }
    )
    layers = caption_layers(tokens, alignment, canvas=CANVAS)
    assert len(layers) == 2
    assert layers[0].time.end == pytest.approx(layers[1].time.start)
    assert layers[-1].time.end == pytest.approx(3.0)


def test_an_alignment_with_no_words_emits_no_captions(tokens) -> None:
    alignment = Alignment.model_validate(
        {"duration": 1.0, "scenes": [{"id": "silent", "start": 0.0, "end": 1.0}]}
    )
    assert caption_layers(tokens, alignment, canvas=CANVAS) == []
