"""Tests for the new storyteller templates and tier-pair metadata."""

from __future__ import annotations

from pathlib import Path

from video_editor_core.templates.eleven_templates import TEMPLATE_MAP


def test_storyteller_hd_is_registered_with_standard_pair() -> None:
    tmpl = TEMPLATE_MAP["storyteller_hd"]
    assert tmpl.pipeline_kind == "storyteller"
    assert tmpl.default_aspect_ratio == "16:9"
    assert tmpl.default_providers["music"] == "poyo_music"
    assert tmpl.quality_tier_pair == ("standard", "standard")
    assert tmpl.caveats


def test_storyteller_budget_is_registered_with_budget_pair() -> None:
    tmpl = TEMPLATE_MAP["storyteller_budget"]
    assert tmpl.pipeline_kind == "storyteller"
    assert tmpl.quality_tier_pair == ("budget", "budget")
    assert tmpl.caveats


def test_storyteller_premium_is_registered_with_premium_pair() -> None:
    tmpl = TEMPLATE_MAP["storyteller_premium"]
    assert tmpl.pipeline_kind == "storyteller"
    assert tmpl.quality_tier_pair == ("premium", "premium")
    assert tmpl.caveats


def test_storyteller_build_concept_returns_valid_markdown(tmp_path) -> None:
    from video_editor_core.templates.storyteller_concepts import build_storyteller_concept

    md = build_storyteller_concept(tmp_path / "story.md")
    assert "cast:" in md
    assert "## Speaker: narrator" in md
    assert "## Speaker: king" in md


def test_storyteller_build_concept_is_parseable(tmp_path: Path) -> None:
    from video_editor_core.templates.storyteller_concepts import build_storyteller_concept
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    story = tmp_path / "my_story.md"
    md = build_storyteller_concept(story)
    story.write_text(md, encoding="utf-8")
    script = parse_storyteller_script(story)
    assert [s.speaker for s in script.scenes] == ["narrator", "king"]
    assert script.music_mood == "cinematic"


def test_storyteller_build_concept_attaches_ambient_sfx_to_first_scene(
    tmp_path: Path,
) -> None:
    """Bug I5: the concept's ``> sfx: gentle ambient atmosphere`` used to sit
    BEFORE the first ``## Speaker: narrator`` heading, so the parser silently
    dropped it. Moving the directive inside the first scene makes
    ``scene_001.sfx_prompts`` carry the ambient SFX.
    """
    from video_editor_core.templates.storyteller_concepts import build_storyteller_concept
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    story = tmp_path / "my_story.md"
    md = build_storyteller_concept(story)
    story.write_text(md, encoding="utf-8")
    script = parse_storyteller_script(story)
    assert script.scenes[0].sfx_prompts == ["gentle ambient atmosphere"]
