"""Tests for parse_storyteller_script()."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "storyteller" / "sample_story.md"


def test_parse_returns_three_scenes() -> None:
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    script = parse_storyteller_script(FIXTURE)
    assert len(script.scenes) == 3
    assert [s.speaker for s in script.scenes] == ["narrator", "king", "princess"]


def test_parse_resolves_per_scene_language_and_voice() -> None:
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    script = parse_storyteller_script(FIXTURE)
    assert script.scenes[0].language == "en"
    assert script.scenes[0].voice_id == "elevenlabs:Brian"
    assert script.scenes[1].language == "hi-IN"
    assert script.scenes[1].voice_id == "sarvam:shubh"
    # Princess's > language: en override beats the cast default of en-IN
    assert script.scenes[2].language == "en"
    assert script.scenes[2].voice_id == "sarvam:anushka"


def test_parse_extracts_sfx_prompts() -> None:
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    script = parse_storyteller_script(FIXTURE)
    # Narrator scene: one SFX from the bare blockquote under the heading
    assert script.scenes[0].sfx_prompts == ["gentle sea waves rolling in"]
    # King scene: one SFX from `> sfx:` and motion override true
    assert script.scenes[1].sfx_prompts == ["heavy wooden doors opening"]
    assert script.scenes[1].motion_override is True


def test_parse_visual_override() -> None:
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    script = parse_storyteller_script(FIXTURE)
    assert "aerial wide shot" in (script.scenes[0].visual_override or "")


def test_parse_unknown_speaker_raises(tmp_path: Path) -> None:
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    bad = tmp_path / "bad.md"
    bad.write_text(
        "---\nlanguage: en\ncast:\n  narrator: {voice_id: elevenlabs:Brian, language: en}\n---\n\n## Speaker: stranger\nHello.\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not in cast"):
        parse_storyteller_script(bad)


def test_parse_missing_frontmatter_raises(tmp_path: Path) -> None:
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    bad = tmp_path / "no_fm.md"
    bad.write_text("## Speaker: narrator\nHello.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        parse_storyteller_script(bad)


def test_multi_line_visual_continuation_is_attached_to_visual_override() -> None:
    """Bug I2: multi-line ``> visual:`` continuation lines used to leak into
    ``scene.text`` (truncating the image prompt AND injecting visual prose
    into the TTS narration). The parser must fold continuation bare-blockquote
    lines into ``visual_override`` until a blank line or non-blockquote line.

    Uses the bundled fixture so the sample users copy keeps working.
    """
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    script = parse_storyteller_script(FIXTURE)
    narrator = script.scenes[0]
    # Both visual lines must be in the visual override...
    assert "aerial wide shot" in (narrator.visual_override or "")
    assert "golden hour" in (narrator.visual_override or "")
    # ...and NOT leaked into the TTS narration text.
    assert "kingdom at golden hour" not in narrator.text
    assert "> kingdom at golden hour" not in narrator.text


def test_multi_line_sfx_continuation_joins_into_one_prompt(tmp_path: Path) -> None:
    """Symmetric rule for ``> sfx:`` continuation lines (cheap to apply, same
    parser path): a following bare-blockquote line extends the SFX prompt.
    """
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    script_path = tmp_path / "sfx_cont.md"
    script_path.write_text(
        "---\nlanguage: en\ncast:\n  narrator: {voice_id: elevenlabs:Brian, language: en}\n---\n\n"
        "## Speaker: narrator\n"
        "A line of narration.\n"
        "> sfx: gentle wind through\n"
        "> the cedar canopy\n\n",
        encoding="utf-8",
    )
    script = parse_storyteller_script(script_path)
    assert script.scenes[0].sfx_prompts == ["gentle wind through the cedar canopy"]
    assert "the cedar canopy" not in script.scenes[0].text
