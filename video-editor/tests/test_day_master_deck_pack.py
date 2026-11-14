"""Video 3's Day Master deck: contract, cost shape and authoring guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_core.content_pack import (
    END_CARD_ID,
    cue_phrases,
    display_lines,
    load_content_pack,
    spoken_lines,
    still_specs,
)
from video_editor_core.cues import find_cue
from video_editor_core.glyphs import cjk_characters
from video_editor_pipelines.short_pipeline import preflight_short, short_job_requests

ROOT = Path(__file__).resolve().parents[1]
# The pack is content, kept in ../video-projects and reached through the `shorts` symlink.
PACK = ROOT / "shorts" / "day-master-deck" / "short.yaml"
if not PACK.exists():
    pytest.skip("shorts/ content (../video-projects) is not present", allow_module_level=True)

STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]


def test_the_pack_is_the_ten_day_masters() -> None:
    pack = load_content_pack(PACK)
    assert [scene.id for scene in pack.scenes] == [
        "intro",
        "yang_wood",
        "yin_wood",
        "yang_fire",
        "yin_fire",
        "yang_earth",
        "yin_earth",
        "yang_metal",
        "yin_metal",
        "yang_water",
        "yin_water",
        "deck_grid",
    ]
    assert [still.key for still in still_specs(pack)] == [
        "deck_fan",
        "yang_wood",
        "yin_wood",
        "yang_fire",
        "yin_fire",
        "yang_earth",
        "yin_earth",
        "yang_metal",
        "yin_metal",
        "yang_water",
        "yin_water",
        "deck_grid",
    ]
    assert [scene.chip for scene in pack.scenes[1:11]] == STEMS


def test_every_chip_glyph_is_declared_for_the_bundled_font() -> None:
    manifest = json.loads(
        (ROOT / "designs" / "glyphs" / "noto_serif_kr.json").read_text(encoding="utf-8")
    )["characters"]
    pack = load_content_pack(PACK)
    missing = {
        character
        for scene in pack.scenes
        for character in cjk_characters(scene.chip)
        if character not in manifest
    }
    assert not missing, f"chips need glyphs the bundle cannot draw: {sorted(missing)}"


def test_the_pack_passes_preflight_with_the_deck_design() -> None:
    tokens = preflight_short(load_content_pack(PACK))
    assert tokens.name == "day_master_deck"
    assert tokens.type.display == "Montserrat"
    assert tokens.layers.caption_preset == "karaoke"


def test_the_pack_ends_on_the_call_to_action() -> None:
    pack = load_content_pack(PACK)
    assert spoken_lines(pack)[-1] == (
        END_CARD_ID,
        "Find yours free at Cosmic Sah-joo dot com.",
    )
    assert display_lines(pack)[-1] == (
        END_CARD_ID,
        "Find yours free at cosmicsaju.com.",
    )


def test_every_cue_phrase_is_actually_said() -> None:
    pack = load_content_pack(PACK)
    ids = [scene.id for scene in pack.scenes]
    for key, phrase in cue_phrases(pack).items():
        scene_id, index = key.split(":")
        spoken = pack.scenes[ids.index(scene_id)].lines[int(index)].spoken
        words = [{"text": word, "start": 0.0, "end": 0.1} for word in spoken.split()]
        assert find_cue(words, phrase, prefix=True) is not None, (
            f"cue {key!r} names {phrase!r}, which its line never says"
        )


def test_the_build_is_twelve_agy_first_stills_and_thirteen_lines() -> None:
    requests = short_job_requests(load_content_pack(PACK))
    assert len(requests) == 25
    images = [request for request in requests if request.asset_type.value == "image"]
    assert {request.provider for request in images} == {"auto"}
    assert all(request.params["width"] == 1080 for request in images)
    assert all(request.params["height"] == 1920 for request in images)
