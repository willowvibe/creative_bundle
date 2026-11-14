"""Tests for the StorytellerScript Pydantic model."""

from __future__ import annotations


def test_cast_member_defaults() -> None:
    from video_editor_core.models.storyteller_script import CastMember

    cast = CastMember(voice_id="sarvam:shubh", language="hi-IN")
    assert cast.volume == 1.0


def test_storyteller_scene_requires_speaker_and_text() -> None:
    from pydantic import ValidationError
    from video_editor_core.models.storyteller_script import StorytellerScene

    try:
        StorytellerScene(
            speaker="king",
            text="Bring me the traveller.",
            language="hi-IN",
            voice_id="sarvam:shubh",
            id="",
            visual_override=None,
            sfx_prompts=[],
            motion_override=None,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("expected ValidationError when `id` is empty")


def test_storyteller_script_round_trip() -> None:
    from video_editor_core.models.storyteller_script import (
        CastMember,
        StorytellerScene,
        StorytellerScript,
    )

    script = StorytellerScript(
        language="en",
        music_mood="cinematic",
        title="The Traveller",
        cast={"narrator": CastMember(voice_id="elevenlabs:Brian", language="en")},
        scenes=[
            StorytellerScene(
                id="scene_001",
                speaker="narrator",
                text="Long ago...",
                language="en",
                voice_id="elevenlabs:Brian",
                visual_override=None,
                sfx_prompts=[],
                motion_override=None,
            )
        ],
    )
    assert script.scenes[0].id == "scene_001"
    assert script.cast["narrator"].voice_id == "elevenlabs:Brian"
