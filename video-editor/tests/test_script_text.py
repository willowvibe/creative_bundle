"""Non-Latin labels drop letter-spacing and uppercase (Telugu conjuncts split)."""

from __future__ import annotations

import re

import pytest
from video_editor_core.composite_scenes import render_composite
from video_editor_core.designs import load_design
from video_editor_core.script_text import (
    NON_LATIN_CSS,
    has_non_latin_script,
    tag_non_latin_text,
)
from video_editor_core.typographic_scenes import render_typographic
from video_editor_maps.scenes import render_map

DESIGN = load_design("paper_editorial")
TELUGU = "ఉదయం 8:00"


@pytest.mark.parametrize(
    "text", ["ఉదయం", "ఉదయం 8:00", "东京", "東京タワー", "서울", "مرحبا", "हिन्दी", "Chennai ఉదయం"]
)
def test_caseless_scripts_are_detected(text):
    assert has_non_latin_script(text)


@pytest.mark.parametrize(
    "text", ["Chapter 01", "BEIJING", "Åre café", "₹12,000", "8:00", "Москва", "Αθήνα", ""]
)
def test_cased_scripts_and_symbols_keep_their_tracking(text):
    assert not has_non_latin_script(text)


def test_only_leaf_elements_with_caseless_text_are_tagged():
    markup = (
        '<div class="a">ఉదయం</div><div class="b">Chapter</div>'
        '<div class="c"><span class="d">東京</span></div><div>ఉదయం</div>'
    )
    out = tag_non_latin_text(markup)
    assert '<div class="a ve-nl">ఉదయం</div>' in out
    assert '<div class="b">Chapter</div>' in out
    assert '<span class="d ve-nl">東京</span>' in out
    assert '<div class="c">' in out
    assert "<div>ఉదయం</div>" in out  # no class attr to extend
    assert tag_non_latin_text(out) == out


def test_the_override_resets_tracking_and_case_with_priority():
    assert "letter-spacing:0 !important" in NON_LATIN_CSS
    assert "text-transform:none !important" in NON_LATIN_CSS


def _classes(markup: str, name: str) -> list[str]:
    found = re.findall(r'class="([^"]*)"', markup)
    return [c for c in found if name in c.split()]


def _map(props):
    return render_map(
        {"id": "s", "type": "map", "props": props},
        DESIGN,
        index=1,
        duration=6.0,
        width=1920,
        height=1080,
        words=None,
    )


def test_map_labels_in_telugu_get_the_override_and_latin_ones_do_not():
    result = _map(
        {
            "country": "India",
            "focus": "80.2,13.0",
            "label": TELUGU,
            "eyebrow": TELUGU,
            "headline": "Tiffin",
            "subtitle": TELUGU,
            "badge": TELUGU,
            "badge_sub": TELUGU,
            "watermark": TELUGU,
            "callouts": [TELUGU, "BUILT IN 495 CE"],
        }
    )
    for name in (
        "map-place",
        "map-eyebrow",
        "map-subtitle",
        "map-badge-name",
        "map-badge-sub",
        "map-mark",
        "map-card-text",
    ):
        classes = _classes(result.markup, name)
        assert classes, name
        assert any("ve-nl" in c for c in classes), name
    latin_card = re.search(r'<span class="(map-card-text[^"]*)">BUILT IN', result.markup)
    assert latin_card and "ve-nl" not in latin_card.group(1)
    assert "letter-spacing:0 !important" in result.css


def test_a_latin_map_keeps_its_tracking_class_free():
    result = _map({"country": "India", "eyebrow": "Chapter 01", "label": "CHENNAI"})
    assert "ve-nl" not in result.markup


def test_composite_labels_in_telugu_get_the_override():
    result = render_composite(
        {
            "id": "s",
            "type": "composite",
            "props": {
                "layout": "over_media",
                "eyebrow": TELUGU,
                "headline": "H",
                "subtitle": TELUGU,
                "badge": TELUGU,
                "badge_sub": TELUGU,
                "watermark": TELUGU,
            },
        },
        DESIGN,
        index=3,
        duration=6.0,
        width=1920,
        height=1080,
        image_count=1,
    )
    for name in (
        "comp-eyebrow-text",
        "comp-subtitle",
        "comp-badge-name",
        "comp-badge-sub",
        "comp-mark",
    ):
        classes = _classes(result.markup, name)
        assert classes and all("ve-nl" in c for c in classes), name
    assert "letter-spacing:0 !important" in result.css


def test_composite_split_credit_and_stat_label_get_the_override():
    result = render_composite(
        {
            "id": "s",
            "type": "composite",
            "props": {
                "layout": "split",
                "eyebrow": "Chapter 01",
                "headline": "H",
                "credit": TELUGU,
                "stat": {"value": "42", "label": TELUGU},
            },
        },
        DESIGN,
        index=3,
        duration=6.0,
        width=1920,
        height=1080,
        image_count=1,
    )
    for name in ("comp-credit", "comp-stat-label"):
        classes = _classes(result.markup, name)
        assert classes and all("ve-nl" in c for c in classes), name
    assert all("ve-nl" not in c for c in _classes(result.markup, "comp-eyebrow-text"))


def test_typographic_mark_in_telugu_gets_the_override():
    result = render_typographic(
        {
            "id": "s",
            "type": "typographic",
            "props": {"layout": "definition", "term": "ఇడ్లీ", "gloss": "ఉదయం", "mark": TELUGU},
        },
        load_design("cream_editorial"),
        index=1,
        duration=4.0,
        width=1080,
        words=None,
    )
    assert 'class="typo-mark ve-nl"' in result.markup
