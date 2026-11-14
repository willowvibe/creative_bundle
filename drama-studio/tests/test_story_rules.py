"""Tests for the drama story gate (docs/DRAMA_STORY_AND_CONTINUITY.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.story_rules import evaluate, main, write_report


def _episode(
    tmp_path: Path,
    shots: list[dict],
    visuals: list[dict] | None = None,
) -> Path:
    (tmp_path / "shots.json").write_text(
        json.dumps({"shots": shots}, ensure_ascii=False), encoding="utf-8"
    )
    if visuals is not None:
        (tmp_path / "v2").mkdir()
        (tmp_path / "v2" / "visuals.json").write_text(
            json.dumps({"visuals": visuals}, ensure_ascii=False), encoding="utf-8"
        )
    return tmp_path


def _line(shot_id: str, scene: int, en: str, emotion: str, turn: str = "") -> dict:
    shot = {
        "id": shot_id,
        "scene": scene,
        "speaker": "abhinav",
        "line": {"who": "abhinav", "te": "", "en": en, "emotion": emotion},
    }
    if turn:
        shot["turn"] = turn
    return shot


@pytest.fixture
def good_episode(tmp_path: Path) -> Path:
    """All five rules satisfied: question, selfless act, irony, turns, reopen."""
    shots = [
        _line("s01", 1, "Why did he come back?", "worried", "up"),
        _line("s02", 2, "He runs into the road to save the child.", "alarm", "down"),
        _line("s03", 3, "The lorry is already behind him.", "warm", "down"),
        _line("s04", 4, "Will he come back to his mother?", "hopeful", "up"),
    ]
    visuals = [
        {
            "scene": 1,
            "kind": "establishing",
            "cam": "slow push-in",
            "prompt": "A quiet street at dawn.",
        },
        {
            "scene": 2,
            "kind": "action",
            "cam": "handheld",
            "prompt": "A boy darts into the road, unaware of the lorry.",
        },
        {
            "scene": 3,
            "kind": "shock",
            "cam": "low angle",
            "prompt": "The lorry approaching, hidden from the child.",
        },
        {
            "scene": 4,
            "kind": "speak",
            "cam": "slow push-in",
            "prompt": "A hospital room, dawn light.",
        },
    ]
    return _episode(tmp_path, shots, visuals)


def _rule(results, number):
    return next(r for r in results if r.number == number)


def test_all_five_rules_pass_on_a_good_episode(good_episode: Path) -> None:
    results, _ = evaluate(good_episode)

    assert [r.status for r in results] == ["pass", "pass", "pass", "pass", "pass"]


def test_missing_question_fails_rule_one(tmp_path: Path) -> None:
    episode = _episode(tmp_path, [_line("s01", 1, "He walks home in the rain.", "calm")])

    results, _ = evaluate(episode)

    assert _rule(results, 1).status == "fail"
    assert "never ask a question" in _rule(results, 1).detail


def test_question_mark_anywhere_in_the_opener_passes(tmp_path: Path) -> None:
    episode = _episode(tmp_path, [_line("s01", 1, "Who left this at the door?", "unease")])

    results, _ = evaluate(episode)

    assert _rule(results, 1).status == "pass"


def test_kindness_idiom_does_not_count(tmp_path: Path) -> None:
    """'take care' is a farewell; it must not satisfy 'make us care'."""
    episode = _episode(
        tmp_path,
        [
            _line("s01", 1, "Take care, Nanna, I will be back with sweets.", "warm"),
            _line("s02", 2, "The office is the same as every day.", "calm"),
        ],
    )

    results, _ = evaluate(episode)

    assert _rule(results, 2).status == "fail"


def test_selfless_act_passes_with_evidence(tmp_path: Path) -> None:
    episode = _episode(
        tmp_path,
        [
            _line("s01", 1, "He swerves the bike to save the little girl.", "alarm"),
            _line("s02", 2, "He is carried away on a stretcher.", "despair"),
        ],
    )

    results, _ = evaluate(episode)

    rule = _rule(results, 2)
    assert rule.status == "pass"
    assert "save" in rule.evidence[0]


def test_unannotated_turns_are_review_not_fail(tmp_path: Path) -> None:
    """Without `turn` fields the gate cannot judge direction; it must not fail."""
    episode = _episode(
        tmp_path,
        [
            _line("s01", 1, "Why is he late?", "worried"),
            _line("s02", 2, "He saves a child from the road.", "alarm"),
        ],
    )

    results, _ = evaluate(episode)

    assert _rule(results, 4).status == "review"


def test_three_flat_beats_fail_when_turns_are_declared(tmp_path: Path) -> None:
    episode = _episode(
        tmp_path,
        [
            _line("s01", 1, "Why is the ward so quiet?", "despair", "down"),
            _line("s02", 2, "The doctor cannot find a pulse.", "panic", "down"),
            _line("s03", 3, "The machine keeps beeping.", "grief", "down"),
        ],
    )

    results, _ = evaluate(episode)

    assert _rule(results, 4).status == "fail"
    assert "scenes 1–3" in _rule(results, 4).evidence[0]


def test_missing_forward_hook_fails_rule_five(tmp_path: Path) -> None:
    episode = _episode(
        tmp_path,
        [
            _line("s01", 1, "Why did it happen?", "grave"),
            _line("s02", 2, "He saves the girl, and the ambulance takes him away.", "despair"),
            _line("s03", 3, "The family sits quietly at the table.", "grief"),
        ],
    )

    results, _ = evaluate(episode)

    assert _rule(results, 5).status == "fail"


def test_report_writes_markdown_and_json(good_episode: Path) -> None:
    md, js = write_report(good_episode)

    assert md.exists() and js.exists()
    data = json.loads(js.read_text(encoding="utf-8"))
    assert len(data["rules"]) == 5
    assert len(data["beats"]) == 4
    assert "## Beat table" in md.read_text(encoding="utf-8")


def test_strict_exit_code_follows_failures(tmp_path: Path) -> None:
    bad = _episode(tmp_path, [_line("s01", 1, "He walks home in the rain.", "calm")])

    assert main([str(bad)]) == 0
    assert main([str(bad), "--strict"]) == 1


def test_json_mode_writes_nothing(tmp_path: Path) -> None:
    bad = _episode(tmp_path, [_line("s01", 1, "He walks home in the rain.", "calm")])

    assert main([str(bad), "--json"]) == 0
    assert not (bad / "review").exists()


def test_episode_without_visuals_still_builds_beats(tmp_path: Path) -> None:
    episode = _episode(
        tmp_path,
        [
            _line("s01", 1, "Why is she crying?", "worried"),
            _line("s02", 2, "He saves the child.", "alarm"),
        ],
    )

    _, beats = evaluate(episode)

    assert [b.scene for b in beats] == [1, 2]
    assert beats[0].kinds == []
