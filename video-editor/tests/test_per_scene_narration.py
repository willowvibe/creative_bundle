"""`narration_mode: per_scene`: exact scene timing for synthetic narration.

Whisper is a poor clock for synthetic Telugu (it skipped whole passages in
chai-stall-pilot-1), but each scene's own clip has a measured length.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_asset_ingestion.models import AssetType
from video_editor_pipelines.explainer_pipeline import join_scene_narration
from video_editor_pipelines.explainer_plan import build_explainer_plan, explainer_requests
from video_editor_pipelines.explainer_script import ScriptError, parse_explainer_script
from video_editor_pipelines.explainer_timing import (
    FINAL_TAIL_SECONDS,
    SCENE_GAP_SECONDS,
    per_scene_timings,
)

SCRIPT = """---
title: Chai
voice_language: te
narration_mode: per_scene
pronunciations:
  "₹15": పదిహేను రూపాయలు
---

## Scene 1 — One
visual: typographic
layout: big_stat
headline: ₹15
narration: ఒక కప్పు ₹15.

## Scene 2 — Two
visual: typographic
layout: big_stat
headline: "400"
narration: రోజుకి నాలుగు వందల కప్పులు.
"""


def test_the_mode_is_read_from_front_matter() -> None:
    assert parse_explainer_script(SCRIPT).narration_mode == "per_scene"
    plain = SCRIPT.replace("narration_mode: per_scene\n", "")
    assert parse_explainer_script(plain).narration_mode == "single"


def test_an_unknown_mode_is_rejected() -> None:
    with pytest.raises(ScriptError, match="narration_mode"):
        parse_explainer_script(SCRIPT.replace("per_scene", "chunks"))


def test_per_scene_mode_asks_for_one_spoken_clip_per_scene() -> None:
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job")

    tts = [r for r in explainer_requests(plan, script, "job") if r.asset_type is AssetType.TTS]

    assert [(r.asset_id, r.scene_id, r.prompt) for r in tts] == [
        ("narration_scene_1", "job", "ఒక కప్పు పదిహేను రూపాయలు."),
        ("narration_scene_2", "job", "రోజుకి నాలుగు వందల కప్పులు."),
    ]
    assert all(r.params.get("language") == "te" for r in tts)


def test_timings_put_every_scene_on_its_own_clip() -> None:
    narrations = ["ఒక కప్పు ₹15.", "రోజుకి నాలుగు వందల కప్పులు."]
    words, timings = per_scene_timings(narrations, [2.0, 3.0])

    second_clip = 2.0 + SCENE_GAP_SECONDS
    assert timings[0].start == 0.0
    assert abs(timings[1].start - second_clip) <= 0.35
    assert timings[0].end == timings[1].start
    assert timings[1].end == pytest.approx(second_clip + 3.0 + FINAL_TAIL_SECONDS)
    # Words stay inside their own clip and keep reading order.
    assert [w["word"] for w in words] == " ".join(narrations).split()
    assert words[0]["start"] == 0.0 and words[2]["end"] == pytest.approx(2.0)
    assert words[3]["start"] == pytest.approx(second_clip)
    assert words[-1]["end"] == pytest.approx(second_clip + 3.0)
    assert all(a["end"] <= b["start"] + 1e-9 for a, b in zip(words, words[1:], strict=False))


def test_per_scene_spoken_words_delay_later_words() -> None:
    narrations = ["₹100 ok"]

    plain, _ = per_scene_timings(narrations, [10.0])
    spoken, _ = per_scene_timings(
        narrations,
        [10.0],
        spoken=lambda word: "వంద రూపాయలు" if word == "₹100" else word,
    )

    assert [w["word"] for w in plain] == [w["word"] for w in spoken] == ["₹100", "ok"]
    assert plain[1]["start"] == pytest.approx(10.0 * 3 / 5)
    assert spoken[1]["start"] > plain[1]["start"]
    assert spoken[1]["start"] == pytest.approx(
        10.0 * len("వందరూపాయలు") / (len("వందరూపాయలు") + 2), abs=0.001
    )


def _tone(path: Path, seconds: float) -> Path:
    from video_editor_ffmpeg.run import run_ffmpeg

    run_ffmpeg(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            str(path),
        ]
    )
    return path


def test_joined_narration_is_the_clips_plus_the_pauses(tmp_path: Path) -> None:
    from video_editor_ffmpeg.probe import probe_media

    clips = [_tone(tmp_path / "a.wav", 1.0), _tone(tmp_path / "b.wav", 2.0)]
    out = tmp_path / "narration.wav"

    durations = join_scene_narration(clips, out)

    assert durations == pytest.approx([1.0, 2.0], abs=0.03)
    total = probe_media(out).duration_ms() / 1000.0
    assert total == pytest.approx(3.0 + SCENE_GAP_SECONDS, abs=0.06)


@pytest.mark.parametrize(
    ("voice", "pace", "expected"),
    [
        ("sarvam:shubh", 1.2, {"pace": 1.2}),
        ("elevenlabs:EohubqvAiTiSkWCp1vu7", 1.1, {"speed": 1.1}),
        ("elevenlabs:EohubqvAiTiSkWCp1vu7", 1.5, {"speed": 1.2}),  # ElevenLabs caps at 1.2
        ("EohubqvAiTiSkWCp1vu7", 0.5, {"speed": 0.7}),  # no prefix routes to ElevenLabs
    ],
)
def test_voice_pace_reaches_each_provider_under_its_own_name(
    voice: str, pace: float, expected: dict[str, float]
) -> None:
    text = SCRIPT.replace(
        "voice_language: te\n", f"voice_language: te\nvoice: {voice}\nvoice_pace: {pace}\n"
    )
    script = parse_explainer_script(text)
    plan, _ = build_explainer_plan(script, "job")
    tts = next(r for r in explainer_requests(plan, script, "job") if r.asset_type is AssetType.TTS)

    assert {k: tts.params[k] for k in ("pace", "speed") if k in tts.params} == expected
