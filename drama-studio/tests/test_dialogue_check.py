"""dialogue_check: vote aggregation, id normalisation and reply parsing; no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dialogue_check as dc  # noqa: E402

IDS = ["c01a_vo", "c05b_line", "c07c_line"]


def _flag(i, problem="odd"):
    return {"id": i, "problem": problem, "rewrite": "x"}


def test_normalise_maps_suffixless_ids_and_drops_unknown():
    out = dc.normalise([_flag("c05b"), _flag("c07c_line"), _flag("c99z_line")], IDS)
    assert [f["id"] for f in out] == ["c05b_line", "c07c_line"]


def test_tally_respects_min_votes():
    runs = [[_flag("c05b_line"), _flag("c07c_line")], [_flag("c05b_line")], []]
    assert set(dc.tally(runs, 2)) == {"c05b_line"}
    assert len(dc.tally(runs, 2)["c05b_line"]) == 2
    assert set(dc.tally(runs, 1)) == {"c05b_line", "c07c_line"}


def test_tally_counts_duplicate_within_a_run_once():
    assert dc.tally([[_flag("c01a_vo"), _flag("c01a_vo")], []], 2) == {}


def test_parse_flags_array_wrapped_in_text():
    text = 'Here you go:\n```json\n[{"id": "c05b", "problem": "p", "rewrite": "r"}]\n```\nDone.'
    assert dc.parse_flags(text) == [{"id": "c05b", "problem": "p", "rewrite": "r"}]


def test_parse_flags_no_array_or_bad_json():
    assert dc.parse_flags("nothing to flag") is None
    assert dc.parse_flags("[not json]") is None
    assert dc.parse_flags("[]") == []


def test_main_end_to_end_with_mocked_call(tmp_path, monkeypatch, capsys):
    shots = [
        {
            "id": "c01a",
            "speaker": "narrator",
            "vo": {"who": "narrator", "te": "ఒకటి", "en": "one", "emotion": "warm"},
        },
        {
            "id": "c05b",
            "speaker": "lakshmi",
            "line": {"who": "lakshmi", "te": "రెండు", "en": "two", "emotion": "sad"},
        },
    ]
    (tmp_path / "shots.json").write_text(json.dumps({"shots": shots}))
    replies = iter(
        [
            '[{"id": "c05b", "problem": "stiff", "rewrite": "r1"}]',
            'Sure: [{"id": "c05b_line", "problem": "stiff", "rewrite": "r2"}] ok',
            "I found no issues.",
        ]
    )  # third run has no array
    monkeypatch.setattr(dc, "call", lambda prompt, key: next(replies))
    monkeypatch.setenv("SARVAM_API", "test-key")
    monkeypatch.setattr(sys, "argv", ["dialogue_check.py", str(tmp_path)])
    dc.main()
    md = (tmp_path / "review" / "dialogue_check.md").read_text()
    assert (
        "## c05b_line (lakshmi) - 2/3" in md
        and "hints only" in md
        and "c01a_vo" not in md.split("##", 1)[1]
    )
    assert "Runs with no parsable reply (counted as zero flags): 3" in md
    raw = json.loads((tmp_path / "review" / "dialogue_check.json").read_text())
    assert raw["kept"] == ["c05b_line"] and raw["runs"][2]["flags"] is None
    assert "1 of 2 lines" in capsys.readouterr().out
