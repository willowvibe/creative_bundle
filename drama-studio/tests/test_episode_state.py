"""Tests for the series state ledger (docs/DRAMA_STORY_AND_CONTINUITY.md)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.episode_state import check, main, update


def _episode(root: Path, name: str, speakers=("abhinav", "lakshmi"), line="", prompt="") -> Path:
    ep = root / name
    ep.mkdir(parents=True, exist_ok=True)
    shots = [
        {
            "id": f"s0{i}",
            "scene": i,
            "speaker": who,
            "line": {"who": who, "te": "", "en": line, "emotion": "calm"},
        }
        for i, who in enumerate(speakers, 1)
    ]
    (ep / "shots.json").write_text(json.dumps({"shots": shots}), encoding="utf-8")
    if prompt:
        (ep / "v2").mkdir(exist_ok=True)
        (ep / "v2" / "visuals.json").write_text(
            json.dumps({"visuals": [{"scene": 1, "prompt": prompt}]}), encoding="utf-8"
        )
    return ep


def _ledger(path: Path, **overrides) -> Path:
    data = {
        "series": "show",
        "episode_order": ["show-ep1", "show-ep2", "show-ep3"],
        "characters": {
            "abhinav": {"aliases": ["Abhi"], "knows": [], "has": []},
            "lakshmi": {"aliases": [], "knows": [], "has": []},
        },
        "props": {"rudraksha": {"aliases": ["rudraksha", "bracelet"], "introduced_in": "show-ep1"}},
        "facts": {},
        "episodes": {},
    }
    data.update(overrides)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return path


def test_prop_after_introduction_is_clean(tmp_path: Path) -> None:
    ep = _episode(tmp_path, "show-ep2", prompt="His rudraksha bracelet catches the light.")
    ledger = _ledger(tmp_path / "state.json")

    assert check(ep, ledger) == []


def test_prop_before_introduction_fails(tmp_path: Path) -> None:
    ep = _episode(tmp_path, "show-ep1", prompt="His rudraksha bracelet catches the light.")
    ledger = _ledger(
        tmp_path / "state.json",
        props={"rudraksha": {"aliases": ["rudraksha"], "introduced_in": "show-ep3"}},
    )

    issues = check(ep, ledger)

    failures = [i for i in issues if i["severity"] == "fail"]
    assert len(failures) == 1
    assert "before show-ep3" in failures[0]["message"]


def test_unknown_speaker_warns(tmp_path: Path) -> None:
    ep = _episode(tmp_path, "show-ep1", speakers=("abhinav", "raghava"))
    ledger = _ledger(tmp_path / "state.json")

    issues = check(ep, ledger)

    assert [i["severity"] for i in issues] == ["warn"]
    assert "raghava" in issues[0]["message"]


def test_fact_before_reveal_fails(tmp_path: Path) -> None:
    ep = _episode(tmp_path, "show-ep1", line="Abhinav, your journey does not end here.")
    ledger = _ledger(
        tmp_path / "state.json",
        facts={"divine_voice": {"aliases": ["journey does not end"], "revealed_in": "show-ep2"}},
    )

    issues = check(ep, ledger)

    assert any(i["severity"] == "fail" and "divine_voice" in i["message"] for i in issues)


def test_speaker_who_does_not_know_the_fact_warns(tmp_path: Path) -> None:
    ep = _episode(tmp_path, "show-ep2", line="Lakshmi says the journey does not end.")
    ledger = _ledger(
        tmp_path / "state.json",
        facts={
            "divine_voice": {
                "aliases": ["journey does not end"],
                "revealed_in": "show-ep1",
                "known_by": ["abhinav"],
            }
        },
    )

    issues = check(ep, ledger)

    warnings = [i for i in issues if i["severity"] == "warn"]
    assert any("lakshmi" in w["message"] for w in warnings)


def test_missing_ledger_fails(tmp_path: Path) -> None:
    ep = _episode(tmp_path, "show-ep1")

    issues = check(ep, tmp_path / "missing.json")

    assert issues and issues[0]["severity"] == "fail"


def test_update_records_cast_props_and_hook(tmp_path: Path) -> None:
    ep1 = _episode(
        tmp_path,
        "show-ep1",
        line="Will he come back to his mother?",
        prompt="A rudraksha on his wrist.",
    )
    ledger = _ledger(tmp_path / "state.json")

    record = update(ep1, ledger)

    assert record["characters"] == ["abhinav", "lakshmi"]
    assert record["props"] == ["rudraksha"]
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["episodes"]["show-ep1"]["hook"]
    assert data["episode_order"] == ["show-ep1", "show-ep2", "show-ep3"]


def test_update_adds_a_new_episode_in_numeric_order(tmp_path: Path) -> None:
    ep4 = _episode(tmp_path, "show-ep4")
    ledger = _ledger(tmp_path / "state.json", episode_order=["show-ep1", "show-ep2"])

    update(ep4, ledger)

    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["episode_order"] == ["show-ep1", "show-ep2", "show-ep4"]


def test_strict_exit_code_only_counts_failures(tmp_path: Path) -> None:
    ep = _episode(tmp_path, "show-ep1", speakers=("abhinav", "raghava"))
    ledger = _ledger(tmp_path / "state.json")

    assert main(["check", str(ep), "--state", str(ledger)]) == 0
    assert main(["check", str(ep), "--state", str(ledger), "--strict"]) == 0

    broken = _ledger(
        tmp_path / "broken.json",
        props={"rudraksha": {"aliases": ["rudraksha"], "introduced_in": "show-ep3"}},
    )
    ep2 = _episode(tmp_path, "show-ep1", prompt="A rudraksha on his wrist.")
    assert main(["check", str(ep2), "--state", str(broken), "--strict"]) == 1
