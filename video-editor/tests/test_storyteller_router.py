"""Tests that the router picks the right template for storyteller markdown."""

from __future__ import annotations

from pathlib import Path

from video_editor_core.templates import TemplateRouter


def test_markdown_with_cast_frontmatter_routes_to_storyteller_hd(tmp_path: Path) -> None:
    src = tmp_path / "story.md"
    src.write_text(
        "---\nlanguage: en\ncast:\n  narrator: {voice_id: elevenlabs:Brian, language: en}\n---\n\n## Speaker: narrator\nHello.\n",
        encoding="utf-8",
    )
    router = TemplateRouter()
    result = router.select(source=src)
    assert result.template_name == "storyteller_hd"
    assert result.pipeline_kind == "storyteller"


def test_plain_markdown_still_routes_to_explainer_reel(tmp_path: Path) -> None:
    src = tmp_path / "concept.md"
    src.write_text("# Hook\n\nHello world.\n", encoding="utf-8")
    router = TemplateRouter()
    result = router.select(source=src)
    assert result.template_name == "explainer_reel"
