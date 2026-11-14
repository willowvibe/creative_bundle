"""The committed pack: contract, cost shape and authoring guards."""

from __future__ import annotations

import json
from pathlib import Path

from video_editor_core.content_pack import (
    END_CARD_ID,
    cue_phrases,
    load_content_pack,
    spoken_lines,
    still_specs,
)
from video_editor_pipelines.short_pipeline import preflight_short, short_job_requests

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "shorts" / "saju-v1-your-animal" / "short.yaml"


def test_the_pack_loads_with_the_expected_shape() -> None:
    pack = load_content_pack(PACK)
    assert pack.job_id == "saju-v1-your-animal"
    assert pack.design == "saju_series"
    assert pack.aspect == "9:16"
    assert [scene.id for scene in pack.scenes] == [
        "hook",
        "eight_characters",
        "four_pillars",
        "year_branch",
        "day_master",
        "question",
    ]
    assert [still.key for still in still_specs(pack)] == [
        "rat",
        "tiles",
        "pillars",
        "gem",
        "bridge",
    ]


def test_every_chip_glyph_is_in_the_bundled_font() -> None:
    manifest = json.loads(
        (ROOT / "designs" / "glyphs" / "noto_serif_kr.json").read_text(encoding="utf-8")
    )["characters"]
    pack = load_content_pack(PACK)
    missing = {
        character for scene in pack.scenes for character in scene.chip if character not in manifest
    }
    assert not missing, f"chips need glyphs the bundle cannot draw: {sorted(missing)}"


def test_the_pack_passes_preflight_against_the_shipped_design() -> None:
    tokens = preflight_short(load_content_pack(PACK))
    assert tokens.layers.caption_preset == "karaoke"


def test_the_pack_ends_on_the_call_to_action() -> None:
    pack = load_content_pack(PACK)
    spoken = spoken_lines(pack)
    assert spoken[-1] == (END_CARD_ID, "Find yours free at cosmicsaju.com.")
    assert all(text.strip() for _, text in spoken)


def test_every_cue_phrase_is_actually_said() -> None:
    pack = load_content_pack(PACK)
    ids = [scene.id for scene in pack.scenes]
    for key, phrase in cue_phrases(pack).items():
        scene_id, index = key.split(":")
        spoken = pack.scenes[ids.index(scene_id)].lines[int(index)].spoken
        assert phrase.lower() in spoken.lower(), (
            f"cue {key!r} names {phrase!r}, which its line never says"
        )


def test_the_build_is_five_stills_and_seven_lines() -> None:
    requests = short_job_requests(load_content_pack(PACK))
    assert len(requests) == 12


def test_the_stills_are_told_not_to_letter_the_frame() -> None:
    pack = load_content_pack(PACK)
    assert "no text" in pack.image.style_suffix
