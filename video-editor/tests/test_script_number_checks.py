"""The script refuses numbers that do not add up."""

from __future__ import annotations

import pytest
from video_editor_pipelines.explainer_script import ScriptError, parse_explainer_script

HEAD = "---\ntitle: T\n---\n\n"


def _scene(n: int, body: str) -> str:
    return (
        f"## Scene {n}\nvisual: typographic\nlayout: big_stat\nheadline: x\nnarration: y\n{body}\n"
    )


def test_a_tally_must_move_by_exactly_its_cash_rows() -> None:
    good = (
        HEAD
        + _scene(1, "tally: {label: c, value: 0}")
        + _scene(
            2,
            "tally: {label: c, value: -324}\n"
            "calc:\n  - {label: tea, expr: 1 kg × ₹180, amount: -180}\n"
            "  - {label: sugar, expr: 3.2 kg × ₹45, amount: -144}",
        )
    )
    parse_explainer_script(good)

    bad = good.replace("value: -324", "value: -300")
    with pytest.raises(ScriptError, match="tally moves by −₹300 but its cash rows add to −₹324"):
        parse_explainer_script(bad)


def test_non_tally_rows_do_not_count_toward_the_move() -> None:
    script = (
        HEAD
        + _scene(1, "tally: {label: c, value: 100}")
        + _scene(
            2,
            "tally: {label: c, value: 100}\n"
            "calc:\n  - {label: per cup, expr: '₹2,668 ÷ 400', amount: 6.67, in_tally: false}",
        )
    )
    parse_explainer_script(script)


def test_a_wrong_calculation_is_refused_with_the_scene_named() -> None:
    script = HEAD + _scene(1, "calc:\n  - {label: milk, expr: 24 L × ₹62, amount: -1500}")
    with pytest.raises(ScriptError, match=r"Scene 1.*24 L × ₹62 = 1,488, not 1,500"):
        parse_explainer_script(script)


def test_a_ledger_total_must_be_the_sum_of_its_rows() -> None:
    ledger = (
        "## Day\nvisual: typographic\nlayout: ledger\nnarration: y\n"
        "rows:\n  - {label: out, amount: -3568}\n  - {label: in, amount: 6000}\n"
        "total: {label: kept, amount: 2432}\n"
    )
    parse_explainer_script(HEAD + ledger)
    with pytest.raises(ScriptError, match="add up to ₹2,432, not ₹2,400"):
        parse_explainer_script(HEAD + ledger.replace("amount: 2432", "amount: 2400"))
    with pytest.raises(ScriptError, match="ledger needs `rows` and a `total`"):
        parse_explainer_script(HEAD + "## Day\nvisual: typographic\nlayout: ledger\nnarration: y\n")


def test_a_ledger_scene_has_no_cue_words_to_match() -> None:
    from video_editor_pipelines.composition_qa import cue_problems

    plan = {
        "scenes": [
            {
                "id": "day",
                "type": "typographic",
                "props": {
                    "layout": "ledger",
                    "rows": [{"label": "out", "amount": -1}],
                    "total": {"label": "kept", "amount": -1},
                    "word_times": [],
                },
            }
        ]
    }
    assert cue_problems(plan) == []
