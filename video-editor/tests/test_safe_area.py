"""Platform safe areas: geometry, caption/preset placement and the QA check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_core.render_graph import Cut, Layer, Rect, RenderGraph
from video_editor_core.safe_area import (
    PROFILES,
    TITLE_SAFE_INSETS,
    VERTICAL_INSETS,
    fit_box,
    is_inside,
    safe_area_violations,
    safe_box,
    safe_insets,
)

PRESETS = Path(__file__).resolve().parents[1] / "presets"
_TEXT_BANDS = {"captions", "hook", "lower_thirds", "chapter_title", "top_graphic", "callout"}


def test_insets_by_orientation() -> None:
    assert safe_insets(1080, 1920) == VERTICAL_INSETS
    assert safe_insets(1080, 1350) == VERTICAL_INSETS  # 4:5 feed
    assert safe_insets(1920, 1080) == TITLE_SAFE_INSETS
    assert safe_insets(1080, 1080) == TITLE_SAFE_INSETS


def test_vertical_safe_box_keeps_bottom_fifth_clear() -> None:
    x, y, w, h = safe_box(1080, 1920)
    assert (x, y) == (86, 154)
    assert x + w == 994
    assert y + h == 1536  # 1920 * 0.8


def test_fit_box_moves_bottom_caption_band_up_and_in() -> None:
    assert fit_box((0, 1720, 1080, 160), 1080, 1920) == (86, 1376, 908, 160)


def test_fit_box_leaves_safe_box_untouched() -> None:
    box = (200, 400, 600, 120)
    assert fit_box(box, 1080, 1920) == box
    assert is_inside(box, 1080, 1920)


def test_fit_box_shrinks_oversized_box() -> None:
    x, y, w, h = fit_box((0, 0, 1920, 1080), 1920, 1080)
    assert (x, y, w, h) == safe_box(1920, 1080)


def test_violations_only_report_text_layers() -> None:
    layers = [
        Layer(
            kind="text",
            name="caption_line",
            rect=Rect(x=0, y=1720, width=1080, height=160),
            text="hi",
        ),
        Layer(kind="text", name="title", rect=Rect(x=200, y=400, width=600, height=120), text="ok"),
        Layer(kind="shape", name="spot", rect=Rect(x=0, y=1800, width=100, height=100)),
    ]
    found = safe_area_violations(layers, 1080, 1920)
    assert [v["name"] for v in found] == ["caption_line"]


@pytest.mark.parametrize("path", sorted(PRESETS.glob("*.json")), ids=lambda p: p.stem)
def test_shipped_presets_keep_text_bands_safe_and_karaoke(path: Path) -> None:
    preset = json.loads(path.read_text())
    if "layout" not in preset:
        pytest.skip("not a layout preset")
    for key, r in preset["layout"].items():
        if key in _TEXT_BANDS:
            box = (r["x"], r["y"], r["width"], r["height"])
            assert is_inside(box, preset["width"], preset["height"]), (key, box)
    assert preset["captions"]["animation"] == "karaoke"


def test_planner_fits_text_layers_into_safe_area() -> None:
    from graphics.models import Layer as PlanLayer
    from graphics.models import Rect as PlanRect
    from graphics.planner import _fit_text_to_safe_area

    hook = PlanLayer(
        type="text", name="hook", rect=PlanRect(x=0, y=0, width=1080, height=200), text="Hi"
    )
    fitted = _fit_text_to_safe_area(hook, 1080, 1920)
    assert (fitted.rect.x, fitted.rect.y, fitted.rect.width) == (86, 154, 908)
    video = PlanLayer(type="video", name="v", rect=PlanRect(x=0, y=0, width=1080, height=1920))
    assert _fit_text_to_safe_area(video, 1080, 1920) is video


def _qa_skill(tmp_path: Path, **kwargs):  # type: ignore[no-untyped-def]
    from qa.qa import QASkill
    from video_editor_state.job import JobContext

    ctx = JobContext(job_id="safe", work_root=tmp_path / "w", output_root=tmp_path / "o")
    return ctx, QASkill(ctx, **kwargs)


def _write_graph(ctx, y: int) -> None:  # type: ignore[no-untyped-def]
    graph = RenderGraph(
        job_id="safe",
        width=1080,
        height=1920,
        source="in.mp4",
        cuts=[Cut(source_start=0, source_end=2, timeline_start=0, timeline_end=2)],
        overlays=[
            Layer(
                kind="text",
                name="caption_line",
                rect=Rect(x=86, y=y, width=908, height=160),
                text="x",
            )
        ],
    )
    path = ctx.artifact("plan", "render_graph.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(graph.model_dump_json())


def test_qa_safe_area_check_absent_without_graph(tmp_path: Path) -> None:
    _, qa = _qa_skill(tmp_path)
    assert qa._safe_area_check() is None


def test_qa_safe_area_check_is_advisory_by_default(tmp_path: Path) -> None:
    ctx, qa = _qa_skill(tmp_path)
    _write_graph(ctx, y=1720)
    check = qa._safe_area_check()
    assert check is not None
    assert check.name == "text_in_safe_area"
    assert check.value == 1
    assert check.passed  # advisory


def test_qa_safe_area_check_strict_fails(tmp_path: Path) -> None:
    ctx, qa = _qa_skill(tmp_path, safe_area_strict=True)
    _write_graph(ctx, y=1720)
    check = qa._safe_area_check()
    assert check is not None and not check.passed
    _write_graph(ctx, y=1376)
    check = qa._safe_area_check()
    assert check is not None and check.passed


# --- Named safe-area profiles ---

VERTICAL = (1080, 1920)


def test_auto_keeps_the_existing_vertical_insets() -> None:
    assert safe_insets(*VERTICAL) == safe_insets(*VERTICAL, profile="auto")
    assert safe_insets(*VERTICAL).bottom == 0.20


def test_strict_matches_the_platform_numbers() -> None:
    insets = PROFILES["vertical_short_strict"]
    assert insets.top == pytest.approx(0.1146)
    assert insets.bottom == pytest.approx(0.25)
    assert insets.left == pytest.approx(0.08)


def test_strict_box_leaves_220_px_top_and_480_px_bottom() -> None:
    x, y, width, height = safe_box(*VERTICAL, profile="vertical_short_strict")
    assert y == 220
    assert y + height == 1920 - 480
    assert x == 86
    assert x + width == 1080 - 86


def test_landscape_auto_stays_title_safe() -> None:
    assert safe_insets(1920, 1080) == PROFILES["title_safe"]


def test_an_unknown_profile_lists_the_known_ones() -> None:
    with pytest.raises(ValueError) as exc:
        safe_insets(*VERTICAL, profile="vertical_short_strictest")
    assert "vertical_short_strict" in str(exc.value)


def test_a_strict_profile_fits_a_text_layer_that_the_default_would_allow() -> None:
    # y=160 sits inside the loose profile's top inset (0.08 * 1920 = 154) and
    # above the strict one's (220), so only the strict profile moves it. A box
    # that both profiles moved would pass even with ``profile`` ignored.
    box = (86, 160, 908, 200)
    assert fit_box(box, *VERTICAL)[1] == 160
    assert fit_box(box, *VERTICAL, profile="vertical_short_strict")[1] == 220


def test_composition_violations_follow_the_profile():
    from video_editor_core.composition import (
        Canvas,
        Composition,
        CompositionLayer,
        CompositionScene,
        LayerTime,
    )
    from video_editor_core.render_graph import Rect
    from video_editor_core.safe_area import composition_safe_area_violations

    text = CompositionLayer(
        id="title",
        kind="text",
        rect=Rect(x=86, y=160, width=908, height=200),
        time=LayerTime(start=0.0, end=4.0),
    )
    shape = CompositionLayer(
        id="bar",
        kind="shape",
        rect=Rect(x=0, y=0, width=1080, height=40),
        time=LayerTime(start=0.0, end=4.0),
    )
    comp = Composition(
        job_id="job",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=4.0,
        scenes=[CompositionScene(id="s0", start=0.0, end=4.0, layers=[text, shape])],
    )
    assert composition_safe_area_violations(comp) == []
    strict = composition_safe_area_violations(comp, profile="vertical_short_strict")
    assert [item["name"] for item in strict] == ["title"]
