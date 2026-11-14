"""Tests for the parity harness helpers (no rendering)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.parity_rebuild import (
    PREREQUISITES,
    caption_delays,
    check,
    missing_prerequisites,
    prerequisites_for,
    scene_windows,
    within_one_frame,
)


def test_scene_windows_come_from_the_timeline():
    timeline = {"scenes": [{"start": 0.0, "end": 3.0}, {"start": 3.0, "end": 5.5}]}
    assert scene_windows(timeline) == [(0.0, 3.0), (3.0, 5.5)]


def test_scene_windows_ignore_an_empty_timeline():
    assert scene_windows({}) == []


def test_one_frame_tolerance_accepts_rounding():
    assert within_one_frame(5.0, 5.033, 30)
    assert not within_one_frame(5.0, 5.1, 30)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_non_finite_duration_is_not_accepted(bad):
    assert not within_one_frame(5.0, bad, 30)


def test_a_case_with_no_scene_windows_fails_without_measuring() -> None:
    case = {
        "name": "empty",
        "reference": Path("/nonexistent/reference.mp4"),
        "candidate": Path("/nonexistent/candidate.mp4"),
        "windows": [],
    }
    assert check(case) is False


def test_ssim_needs_duration_with_start() -> None:
    from scripts.parity_rebuild import ssim

    with pytest.raises(ValueError, match="both start and duration"):
        ssim(Path("a.mp4"), Path("b.mp4"), start=1.0)


def test_caption_delays_read_the_delay_of_each_shorthand() -> None:
    html = (
        '<span class="ve-word" style="opacity:0.60;animation:ve_word_hl 0.120s '
        "step-end 0.000s none, ve_word_pop 0.120s ease-out 0.000s none, "
        've_word_seen 0.001s linear 0.000s forwards">one</span>'
        '<span class="ve-word" style="animation:ve_word_hl 0.500s step-end '
        '1.320s none">two</span>'
        '<span class="ve-word" style="opacity:1">three</span>'
    )
    assert caption_delays(html) == [[0.0, 0.0, 0.0], [1.32], []]


def test_missing_prerequisites_names_absent_files_with_their_remedy(tmp_path: Path) -> None:
    present = tmp_path / "present.mp4"
    present.write_bytes(b"x")
    absent = tmp_path / "absent.mp4"
    entries = ((present, "not needed"), (absent, "render it first"))
    assert missing_prerequisites(entries) == [(absent, "render it first")]


def test_every_prerequisite_names_a_remedy_and_a_case() -> None:
    assert set(PREREQUISITES) == {"scene-plan", "render-graph", "musical"}
    for entries in PREREQUISITES.values():
        assert entries
        assert all(remedy.strip() for _, remedy in entries)


def test_a_reference_render_is_always_a_declared_prerequisite() -> None:
    """The two tables name the same files; a preflight miss would be silent."""
    from scripts.parity_rebuild import REFERENCE_PAIRS

    declared = {path for entries in PREREQUISITES.values() for path, _ in entries}
    for entries in REFERENCE_PAIRS.values():
        assert {source for source, _ in entries} <= declared


def test_a_case_only_requires_its_own_inputs() -> None:
    scene_plan = prerequisites_for("scene-plan")
    render_graph = prerequisites_for("render-graph")
    musical = prerequisites_for("musical")
    assert scene_plan and render_graph and musical
    sets = [{path for path, _ in entries} for entries in (scene_plan, render_graph, musical)]
    assert not (sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
    assert len(prerequisites_for("all")) == sum(len(entries) for entries in sets)


def test_a_missing_prerequisite_stops_the_run_before_any_case(monkeypatch, capsys) -> None:
    """Exit 2 is "cannot start", distinct from a parity failure's exit 1."""
    from scripts import parity_rebuild

    monkeypatch.setattr(
        parity_rebuild,
        "prerequisites_for",
        lambda case: ((Path("/nonexistent/input.mp4"), "produce the input"),),
    )
    monkeypatch.setattr(
        parity_rebuild,
        "copy_references",
        lambda case="all": pytest.fail("a case must not start without its inputs"),
    )
    assert parity_rebuild.main(["--case", "scene-plan"]) == 2
    printed = capsys.readouterr()
    assert "/nonexistent/input.mp4" in printed.out
    assert "produce the input" in printed.out
