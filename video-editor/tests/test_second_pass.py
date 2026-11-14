"""Tests for the second pass skill."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from graphics.graphics import GraphicsSkill
from graphics.models import Brand
from second_pass.editor import InvalidBoundsError, SegmentNotFoundError, apply_segment_edit
from second_pass.models import SegmentEdit
from second_pass.second_pass import SecondPassSkill
from video_editor_core.timeline import EDL, compute_timeline_segments
from video_editor_ffmpeg.probe import probe_media
from video_editor_state.job import JobContext


def _build_edl(source_path: str, duration: float = 3.0) -> EDL:
    segments = compute_timeline_segments(
        [
            (0.0, 1.0, "intro"),
            (2.0, duration, "outro"),
        ]
    )
    return EDL(job_id="second-pass-job", source_path=source_path, segments=segments)


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="second-pass-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


def test_apply_segment_edit_updates_bounds_and_recomputes_timeline() -> None:
    edl = _build_edl("locked.mp4")
    edit = SegmentEdit(segment_id="seg_0000", new_source_end=0.8)
    new_edl = apply_segment_edit(edl, edit, source_duration=3.0)

    first = new_edl.segment_map()["seg_0000"]
    second = new_edl.segment_map()["seg_0001"]
    assert first.source_end == 0.8
    assert first.timeline_end == 0.8
    assert second.timeline_start == 0.8
    assert second.timeline_end == pytest.approx(0.8 + (3.0 - 2.0), abs=1e-3)


def test_apply_segment_edit_not_found() -> None:
    edl = _build_edl("locked.mp4")
    edit = SegmentEdit(segment_id="seg_9999", new_label="nope")
    with pytest.raises(SegmentNotFoundError):
        apply_segment_edit(edl, edit, source_duration=3.0)


def test_apply_segment_edit_too_short() -> None:
    edl = _build_edl("locked.mp4")
    edit = SegmentEdit(segment_id="seg_0000", new_source_start=0.9, new_source_end=0.905)
    with pytest.raises(InvalidBoundsError):
        apply_segment_edit(edl, edit, source_duration=3.0)


def test_segment_edit_requires_at_least_one_change() -> None:
    with pytest.raises(ValueError):
        SegmentEdit(segment_id="seg_0000")


def _run_graphics(ctx: JobContext, tmp_media: Path, brand: Brand | None = None) -> None:
    transcript_path = ctx.artifact("transcript", "transcript.json")
    transcript_path.write_text(
        json.dumps(
            {
                "words": [
                    {"word": "Hello", "start": 0.1, "end": 0.5},
                    {"word": "world", "start": 2.1, "end": 2.5},
                ]
            }
        ),
        encoding="utf-8",
    )

    edl = _build_edl(str(tmp_media))
    edl_path = ctx.artifact("plan", "edl.json")
    edl_path.write_text(json.dumps(edl.model_dump()), encoding="utf-8")

    skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    skill.run(
        preset_name="short_explainer",
        edl=edl,
        source_video=tmp_media,
        transcript_path=transcript_path,
        brand=brand,
    )


def test_second_pass_renders_output(ctx: JobContext, tmp_media: Path) -> None:
    _run_graphics(ctx, tmp_media)

    first_output = probe_media(ctx.work_dir / "graphics" / "with_graphics.mp4")
    assert first_output.duration is not None
    first_duration = first_output.duration

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    skill = SecondPassSkill(ctx, graphics_skill=graphics_skill)
    edit = SegmentEdit(segment_id="seg_0000", new_source_end=0.8)
    output = skill.run(edit)

    assert output.exists()
    second_output = probe_media(output)
    assert second_output.duration is not None
    assert second_output.duration < first_duration

    # EDL and plan were updated.
    assert ctx.artifact("plan", "edl.json").exists()
    assert ctx.artifact("graphics", "plan.json").exists()


def test_second_pass_partial_rerender_reuses_unchanged_segment(
    ctx: JobContext,
    tmp_media: Path,
) -> None:
    _run_graphics(ctx, tmp_media)

    segment_dir = ctx.work_dir / "graphics" / "segments"
    unchanged_before = (segment_dir / "seg_0001.mp4").stat().st_mtime

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    skill = SecondPassSkill(ctx, graphics_skill=graphics_skill)
    edit = SegmentEdit(segment_id="seg_0000", new_source_end=0.8)
    skill.run(edit)

    unchanged_after = (segment_dir / "seg_0001.mp4").stat().st_mtime
    # The unchanged segment file should not be regenerated.
    assert unchanged_after == unchanged_before

    changed_after = (segment_dir / "seg_0000.mp4").stat().st_mtime
    # The changed segment should have been regenerated.
    assert changed_after > unchanged_before


def test_second_pass_runs_per_segment_qa_when_enabled(
    ctx: JobContext,
    tmp_media: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #88 regression: when the GraphicsSkill has run_qa_per_segment=True,
    the second-pass renderer must invoke check_segment_boundary at every cut.
    """
    _run_graphics(ctx, tmp_media)

    calls: list[tuple[Path | None, Path, str | None, str]] = []

    def _fake_check(
        prev_path: Path | None,
        curr_path: Path,
        prev_segment_id: str | None,
        curr_segment_id: str,
        work_dir: Path,
    ) -> None:
        calls.append((prev_path, curr_path, prev_segment_id, curr_segment_id))

    monkeypatch.setattr("qa.segment_check.check_segment_boundary", _fake_check)

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"), run_qa_per_segment=True)
    skill = SecondPassSkill(ctx, graphics_skill=graphics_skill)
    edit = SegmentEdit(segment_id="seg_0000", new_source_end=0.8)
    output = skill.run(edit)

    assert output.exists()
    # Two segments → two calls: the first is skipped (no previous segment),
    # the second is the real boundary check.
    assert len(calls) == 2
    assert calls[0][0] is None  # prev_path
    prev_path, curr_path, prev_id, curr_id = calls[1]
    assert prev_path is not None
    assert prev_id == "seg_0000"
    assert curr_id == "seg_0001"
    assert curr_path.exists()


def test_second_pass_skips_per_segment_qa_when_disabled(
    ctx: JobContext,
    tmp_media: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """By default the second-pass renderer must not run the QA hook."""
    _run_graphics(ctx, tmp_media)

    calls: list[Any] = []

    def _fake_check(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr("qa.segment_check.check_segment_boundary", _fake_check)

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    skill = SecondPassSkill(ctx, graphics_skill=graphics_skill)
    edit = SegmentEdit(segment_id="seg_0000", new_source_end=0.8)
    output = skill.run(edit)

    assert output.exists()
    assert calls == []


def test_second_pass_label_only_change(ctx: JobContext, tmp_media: Path) -> None:
    _run_graphics(ctx, tmp_media)

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    skill = SecondPassSkill(ctx, graphics_skill=graphics_skill)
    edit = SegmentEdit(segment_id="seg_0000", new_label="updated intro")
    output = skill.run(edit)

    assert output.exists()

    edl_path = ctx.artifact("plan", "edl.json")
    with open(edl_path, encoding="utf-8") as f:
        data = json.load(f)
    segment_map = {seg["id"]: seg for seg in data["segments"]}
    assert segment_map["seg_0000"]["label"] == "updated intro"


def test_second_pass_label_only_change_with_regenerate_false_reuses_segment(
    ctx: JobContext,
    tmp_media: Path,
) -> None:
    """Bug #116 regression: a pure label change with ``regenerate_graphics=False``
    must reuse the cached segment render instead of re-rendering it.
    """
    _run_graphics(ctx, tmp_media)

    segment_dir = ctx.work_dir / "graphics" / "segments"
    seg_mtime_before = (segment_dir / "seg_0000.mp4").stat().st_mtime

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    skill = SecondPassSkill(ctx, graphics_skill=graphics_skill)
    edit = SegmentEdit(
        segment_id="seg_0000",
        new_label="updated intro",
        regenerate_graphics=False,
    )
    output = skill.run(edit)

    assert output.exists()
    # The segment file should be reused, not regenerated.
    assert (segment_dir / "seg_0000.mp4").stat().st_mtime == seg_mtime_before

    edl_path = ctx.artifact("plan", "edl.json")
    with open(edl_path, encoding="utf-8") as f:
        data = json.load(f)
    segment_map = {seg["id"]: seg for seg in data["segments"]}
    assert segment_map["seg_0000"]["label"] == "updated intro"


def test_apply_edit_preserves_brand_logo_layer(
    ctx: JobContext,
    tmp_media: Path,
) -> None:
    """Bug #57 regression: second pass re-planning must retain the brand logo.

    The initial graphics pass is built with an explicit brand; after a
    SecondPass edit the new composition plan should still contain a
    logo layer in every segment.
    """
    brand = Brand(
        brand="Test Brand",
        colors={"primary": "#00D8FF"},
        fonts={"headline": "Coolvetica"},
        logo={"path": "assets/logos/logo.png", "width": 160, "height": 160},
        defaults={},
    )

    # Build a plan.json with the brand without rendering (we only need
    # the composition plan to verify logo layers survive re-planning).
    transcript_path = ctx.artifact("transcript", "transcript.json")
    transcript_path.write_text(
        json.dumps({"words": [{"word": "Hello", "start": 0.1, "end": 0.5}]}),
        encoding="utf-8",
    )
    edl = _build_edl(str(tmp_media))
    edl_path = ctx.artifact("plan", "edl.json")
    edl_path.write_text(json.dumps(edl.model_dump()), encoding="utf-8")

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    graphics_skill.plan(
        edl=edl,
        preset_name="short_explainer",
        source_video=tmp_media,
        transcript_path=transcript_path,
        brand=brand,
    )

    skill = SecondPassSkill(ctx, graphics_skill=graphics_skill)

    def _count_logo_layers(plan_path: Path) -> int:
        with open(plan_path, encoding="utf-8") as f:
            data = json.load(f)
        return sum(
            1 for seg in data["segments"] for layer in seg["layers"] if layer.get("type") == "logo"
        )

    plan_path = ctx.artifact("graphics", "plan.json")
    before = _count_logo_layers(plan_path)
    assert before > 0

    skill.apply_edit(SegmentEdit(segment_id="seg_0000", new_source_end=0.8))

    after = _count_logo_layers(plan_path)
    assert after == before
