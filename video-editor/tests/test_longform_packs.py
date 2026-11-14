"""The authored long-form packs stay buildable (#315/#311)."""

from __future__ import annotations

from pathlib import Path

from video_editor_core.content_pack import load_content_pack
from video_editor_pipelines.longform import load_longform
from video_editor_pipelines.short_pipeline import preflight_short

ROOT = Path(__file__).resolve().parents[1] / "shorts" / "cosmic-saju-longform"


def test_every_chapter_pack_passes_preflight() -> None:
    plan = load_longform(ROOT / "longform.yaml")
    assert plan.job_id == "cosmic-saju-longform"
    assert len(plan.chapters) == 8
    for chapter in plan.chapters:
        pack = load_content_pack(chapter.pack)
        assert pack.aspect == "16:9"
        assert pack.captions.mode == "sidecar"
        assert pack.end_card.enabled is False
        preflight_short(pack)


def test_the_longform_plan_joins_with_a_crossfade() -> None:
    plan = load_longform(ROOT / "longform.yaml")
    assert plan.transition == "crossfade"
    assert plan.output_name == "final.mp4"
