from __future__ import annotations

import copy
import textwrap
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from video_editor_core.content_pack import (
    END_CARD_ID,
    Alignment,
    ContentPack,
    ContentPackError,
    PackLine,
    PackScene,
    PackStill,
    PackVoice,
    TimedScene,
    TimedWord,
    brief_warnings,
    cue_phrases,
    display_lines,
    load_content_pack,
    spoken_lines,
    still_for,
    still_specs,
)

MINIMAL = """\
job_id: demo-short
design: saju_series
aspect: "9:16"
voice:
  voice_id: "sarvam:sarang_en_narration"
  gap_seconds: 0.28
image:
  provider: stub_image
  style_suffix: "ink wash"
end_card:
  say: "Find yours free at cosmicsaju.com."
  text: "Free chart snapshot"
scenes:
  - id: hook
    still:
      key: rat
      prompt: "A rat on a plum branch"
    lines:
      - say: "You think you're a Rat?"
        show: "You're not just a Rat."
        cue: "Rat"
  - id: branch
    still: {reuse: rat}
    lines:
      - say: "Your animal is the year branch."
        speak_as: "Your animal is the year bunch."
"""

PACK = yaml.safe_load(textwrap.dedent(MINIMAL))


def write_pack(tmp_path: Path, body: str = MINIMAL) -> Path:
    path = tmp_path / "short.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_load_content_pack_reads_voice_scenes_and_end_card(tmp_path: Path) -> None:
    pack = load_content_pack(write_pack(tmp_path))
    assert pack.job_id == "demo-short"
    assert pack.voice.voice_id == "sarvam:sarang_en_narration"
    assert [scene.id for scene in pack.scenes] == ["hook", "branch"]
    assert pack.end_card.text == "Free chart snapshot"


def test_spoken_text_prefers_speak_as(tmp_path: Path) -> None:
    pack = load_content_pack(write_pack(tmp_path))
    assert pack.scenes[1].lines[0].spoken == "Your animal is the year bunch."


def test_a_line_with_blank_spoken_text_is_rejected() -> None:
    with pytest.raises(ValidationError, match="needs spoken text"):
        PackLine(say="You think you're a Rat?", speak_as="   ")


def test_layers_default_to_off_and_accept_auto() -> None:
    raw = copy.deepcopy(PACK)
    assert ContentPack.model_validate(raw).scenes[0].layers == "off"
    raw["scenes"][0]["layers"] = "auto"
    assert ContentPack.model_validate(raw).scenes[0].layers == "auto"
    raw["scenes"][0]["layers"] = "half"
    with pytest.raises(ValidationError):
        ContentPack.model_validate(raw)


def test_a_still_needs_exactly_one_of_key_or_reuse() -> None:
    with pytest.raises(ValidationError):
        PackStill()
    with pytest.raises(ValidationError):
        PackStill(key="rat", prompt="x", reuse="rat")
    with pytest.raises(ValidationError):
        PackStill(reuse="rat", prompt="a prompt the alias cannot carry")


def test_a_reuse_before_its_generated_still_is_rejected(tmp_path: Path) -> None:
    body = MINIMAL.replace(
        'still:\n      key: rat\n      prompt: "A rat on a plum branch"',
        "still: {reuse: gem}",
    )
    with pytest.raises(ContentPackError, match="reuses still 'gem'"):
        load_content_pack(write_pack(tmp_path, body))


def test_a_pack_cannot_carry_styling(tmp_path: Path) -> None:
    body = MINIMAL.replace("aspect:", "layers:\n  safe_area: vertical_short_strict\naspect:")
    with pytest.raises(ContentPackError, match="extra_forbidden|layers"):
        load_content_pack(write_pack(tmp_path, body))


def test_load_reports_the_path_of_a_broken_pack(tmp_path: Path) -> None:
    path = write_pack(tmp_path, "job_id: [oops")
    with pytest.raises(ContentPackError, match=str(path)):
        load_content_pack(path)


def test_still_specs_are_ordered_and_aliases_resolve(tmp_path: Path) -> None:
    pack = load_content_pack(write_pack(tmp_path))
    assert [still.key for still in still_specs(pack)] == ["rat"]
    assert still_for(pack, "branch") == "rat"


def test_still_for_ignores_scene_id_collisions() -> None:
    pack = ContentPack(
        job_id="demo-short",
        voice=PackVoice(voice_id="sarvam:sarang_en_narration"),
        scenes=[
            PackScene(
                id="a",
                still=PackStill(key="gem", prompt="A gem on a plum branch"),
                lines=[PackLine(say="one")],
            ),
            PackScene(id="rat", still=PackStill(reuse="gem"), lines=[PackLine(say="two")]),
            PackScene(
                id="other",
                still=PackStill(key="rat", prompt="A rat on a plum branch"),
                lines=[PackLine(say="three")],
            ),
        ],
    )
    assert still_for(pack, "other") == "rat"


def test_cue_phrases_key_each_line(tmp_path: Path) -> None:
    pack = load_content_pack(write_pack(tmp_path))
    assert cue_phrases(pack) == {"hook:0": "Rat"}


def test_spoken_lines_put_the_end_card_last(tmp_path: Path) -> None:
    pack = load_content_pack(write_pack(tmp_path))
    assert spoken_lines(pack) == [
        ("hook", "You think you're a Rat?"),
        ("branch", "Your animal is the year bunch."),
        (END_CARD_ID, "Find yours free at cosmicsaju.com."),
    ]


def test_display_lines_keep_the_packs_spelling(tmp_path: Path) -> None:
    pack = load_content_pack(write_pack(tmp_path))
    assert display_lines(pack) == [
        ("hook", "You think you're a Rat?"),
        ("branch", "Your animal is the year branch."),
        (END_CARD_ID, "Find yours free at cosmicsaju.com."),
    ]


def test_the_end_card_can_carry_its_own_phonetic_spelling(tmp_path: Path) -> None:
    body = MINIMAL.replace(
        '  say: "Find yours free at cosmicsaju.com."',
        '  say: "Find yours free at cosmicsaju.com."\n'
        '  speak_as: "Find yours free at Cosmic Sah-joo dot com."',
    )
    pack = load_content_pack(write_pack(tmp_path, body))
    assert pack.end_card.spoken == "Find yours free at Cosmic Sah-joo dot com."
    assert spoken_lines(pack)[-1] == (
        END_CARD_ID,
        "Find yours free at Cosmic Sah-joo dot com.",
    )
    assert display_lines(pack)[-1] == (
        END_CARD_ID,
        "Find yours free at cosmicsaju.com.",
    )


def test_an_unknown_aspect_is_refused_at_load() -> None:
    # ``canvas_for_aspect`` would silently render a "9x16" pack at 1920×1080.
    with pytest.raises(ValidationError, match="aspect"):
        ContentPack.model_validate({**PACK, "aspect": "9x16"})


def test_alignment_scenes_must_tile_the_duration() -> None:
    words = [TimedWord(text="hi", start=0.0, end=0.4)]
    alignment = Alignment(
        duration=1.0,
        scenes=[TimedScene(id="hook", start=0.0, end=1.0, words=words)],
    )
    assert [w["word"] for w in alignment.word_dicts()] == ["hi"]
    with pytest.raises(ValidationError, match="expected 1.000"):
        Alignment(
            duration=2.0,
            scenes=[
                TimedScene(id="hook", start=0.0, end=1.0, words=words),
                TimedScene(id="tail", start=1.5, end=2.0),
            ],
        )
    with pytest.raises(ValidationError, match="scenes cover 1.000"):
        Alignment(
            duration=2.0,
            scenes=[TimedScene(id="hook", start=0.0, end=1.0, words=words)],
        )
    with pytest.raises(ValidationError, match="expected 1.000"):
        Alignment(
            duration=2.001,
            scenes=[
                TimedScene(id="hook", start=0.0, end=1.0, words=words),
                TimedScene(id="tail", start=1.001, end=2.001),
            ],
        )


def test_sfx_defaults_to_off():
    assert ContentPack.model_validate(PACK).sfx == "off"


def test_a_pack_can_request_sfx():
    assert ContentPack.model_validate({**PACK, "sfx": "auto"}).sfx == "auto"


def test_an_unknown_sfx_mode_is_refused():
    with pytest.raises(ValidationError, match="sfx"):
        ContentPack.model_validate({**PACK, "sfx": "always"})


def test_the_directors_brief_fields_are_optional() -> None:
    pack = ContentPack.model_validate(
        {**PACK, "logline": "A rat finds its year branch.", "direction": "one container never cuts"}
    )
    assert pack.logline == "A rat finds its year branch."
    assert pack.direction == "one container never cuts"
    # The rest is still missing, so the preflight has an advisory to report.
    assert brief_warnings(pack) == [
        "director's brief is missing: references, characters, beat_sheet, "
        "on_screen_text, deliverables"
    ]


def test_a_complete_brief_has_no_warnings() -> None:
    pack = ContentPack.model_validate(
        {
            **PACK,
            "logline": "A rat finds its year branch.",
            "references": ["https://example.test/reference"],
            "characters": [
                {"name": "Rat", "description": "ink-wash rat", "reference_images": ["rat.png"]}
            ],
            "beat_sheet": [{"at_seconds": 0.0, "label": "hook"}],
            "on_screen_text": ["You're not just a Rat."],
            "deliverables": [{"aspect": "9:16", "seconds": 15.0}],
            "direction": "one container never cuts",
        }
    )
    assert brief_warnings(pack) == []


def test_a_character_needs_a_name() -> None:
    with pytest.raises(ValidationError):
        ContentPack.model_validate({**PACK, "characters": [{"name": ""}]})


def test_a_line_can_hold_a_pause_after_it() -> None:
    line = PackLine(say="one", pause_after=0.4)
    assert line.pause_after == 0.4
    with pytest.raises(ValidationError):
        PackLine(say="one", pause_after=11.0)


def test_captions_default_to_burned_and_accept_sidecar() -> None:
    assert ContentPack.model_validate(PACK).captions.mode == "burned"
    assert (
        ContentPack.model_validate({**PACK, "captions": {"mode": "sidecar"}}).captions.mode
        == "sidecar"
    )
    with pytest.raises(ValidationError, match="captions"):
        ContentPack.model_validate({**PACK, "captions": {"mode": "floating"}})


def test_a_video_scene_needs_no_still() -> None:
    pack = ContentPack.model_validate(
        {
            "job_id": "video-pack",
            "voice": {"voice_id": "sarvam:test"},
            "scenes": [
                {"id": "hook", "video": "clip.mp4", "lines": [{"say": "one"}]},
            ],
        }
    )
    assert pack.scenes[0].still is None
    assert still_specs(pack) == []

    with pytest.raises(ValidationError, match="exactly one"):
        ContentPack.model_validate(
            {
                "job_id": "video-pack",
                "voice": {"voice_id": "sarvam:test"},
                "scenes": [{"id": "hook", "lines": [{"say": "one"}]}],
            }
        )
    with pytest.raises(ValidationError, match="exactly one"):
        ContentPack.model_validate(
            {
                "job_id": "video-pack",
                "voice": {"voice_id": "sarvam:test"},
                "scenes": [
                    {
                        "id": "hook",
                        "video": "clip.mp4",
                        "still": {"key": "a", "prompt": "a"},
                        "lines": [{"say": "one"}],
                    }
                ],
            }
        )


def test_an_html_scene_needs_no_still_and_is_sandboxed() -> None:
    pack = ContentPack.model_validate(
        {
            "job_id": "html-pack",
            "voice": {"voice_id": "sarvam:test"},
            "scenes": [
                {
                    "id": "chart",
                    "html": "<div style='color:red'>chart</div>",
                    "lines": [{"say": "one"}],
                }
            ],
        }
    )
    assert pack.scenes[0].still is None
    assert still_specs(pack) == []

    for bad in (
        "<script>alert(1)</script>",
        "<img src='x.png'>",
        "<div onclick='run()'>x</div>",
        "<a href='https://x'>x</a>",
    ):
        with pytest.raises(ValidationError):
            ContentPack.model_validate(
                {
                    "job_id": "html-pack",
                    "voice": {"voice_id": "sarvam:test"},
                    "scenes": [{"id": "chart", "html": bad, "lines": [{"say": "one"}]}],
                }
            )


def test_shots_need_ordered_at_line_and_skip_scene_visuals() -> None:
    base = {
        "job_id": "shots",
        "voice": {"voice_id": "sarvam:test"},
        "scenes": [
            {
                "id": "hook",
                "lines": [{"say": "one"}, {"say": "two"}],
                "shots": [
                    {"still": {"key": "a", "prompt": "a"}, "at_line": 0},
                    {"still": {"key": "b", "prompt": "b"}, "at_line": 1},
                ],
            }
        ],
    }
    pack = ContentPack.model_validate(base)
    assert [still.key for still in still_specs(pack)] == ["a", "b"]
    with pytest.raises(ValidationError, match="first shot"):
        ContentPack.model_validate(
            {
                **base,
                "scenes": [
                    {
                        "id": "hook",
                        "lines": [{"say": "one"}],
                        "shots": [{"still": {"key": "a", "prompt": "a"}, "at_line": 1}],
                    }
                ],
            }
        )
    with pytest.raises(ValidationError, match="cannot mix"):
        ContentPack.model_validate(
            {
                **base,
                "scenes": [
                    {
                        "id": "hook",
                        "still": {"key": "x", "prompt": "x"},
                        "lines": [{"say": "one"}],
                        "shots": [{"still": {"key": "a", "prompt": "a"}, "at_line": 0}],
                    }
                ],
            }
        )


def test_generated_layers_need_three_prompts_and_no_still() -> None:
    base = {
        "job_id": "generated",
        "voice": {"voice_id": "sarvam:test"},
        "scenes": [
            {
                "id": "hook",
                "layers": "generated",
                "generated": {"bg": "background", "mg": "mid", "fg": "foreground"},
                "lines": [{"say": "one"}],
            }
        ],
    }
    pack = ContentPack.model_validate(base)
    assert pack.scenes[0].generated is not None
    assert still_specs(pack) == []
    with pytest.raises(ValidationError, match="generated"):
        ContentPack.model_validate(
            {
                **base,
                "scenes": [{"id": "hook", "layers": "generated", "lines": [{"say": "one"}]}],
            }
        )
    with pytest.raises(ValidationError, match="requires layers"):
        ContentPack.model_validate(
            {
                **base,
                "scenes": [
                    {
                        "id": "hook",
                        "still": {"key": "a", "prompt": "a"},
                        "generated": {"bg": "b", "mg": "m", "fg": "f"},
                        "lines": [{"say": "one"}],
                    }
                ],
            }
        )


def test_a_chapter_pack_can_disable_the_end_card() -> None:
    pack = ContentPack.model_validate(
        {
            "job_id": "chapter",
            "voice": {"voice_id": "sarvam:test"},
            "end_card": {"enabled": False},
            "scenes": [
                {"id": "hook", "still": {"key": "a", "prompt": "a"}, "lines": [{"say": "one"}]}
            ],
        }
    )
    assert pack.end_card.enabled is False
    assert spoken_lines(pack) == [("hook", "one")]
