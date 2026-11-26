"""Tests for the composition gates."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from video_editor_core.adapters.from_scene_plan import from_scene_plan
from video_editor_core.composition import (
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
    MotionSpec,
    WordSpan,
)
from video_editor_core.hyperframes_cli import HyperFramesCheckError
from video_editor_core.render_graph import Rect
from video_editor_pipelines import composition_qa
from video_editor_pipelines.composition_qa import (
    MIN_FRAME_STDDEV,
    CompositionQaInputs,
    cue_problems,
    evaluate_composition_qa,
    frame_difference,
    frame_stddev,
    gather_composition_qa,
    layer_texts,
)


def _ok_inputs(**overrides) -> CompositionQaInputs:
    base = {
        "composition_report": "0 issues",
        "frame_stddevs": (40.0, 38.0),
    }
    base.update(overrides)
    return CompositionQaInputs(**base)  # type: ignore[arg-type]


def test_a_clean_build_passes_every_gate():
    checks = evaluate_composition_qa(_ok_inputs())
    assert [check.name for check in checks] == [
        "glyphs",
        "cues",
        "safe_area",
        "composition",
        "visual",
        "layers",
        "flourishes",
    ]
    assert all(check.ok for check in checks)


def test_a_gate_that_could_not_run_fails():
    checks = evaluate_composition_qa(CompositionQaInputs(ran=False))
    assert len(checks) == 1
    assert checks[0].name == "composition_gates"
    assert checks[0].ok is False


def test_an_undeclared_hanja_fails_the_glyph_gate():
    checks = evaluate_composition_qa(_ok_inputs(undeclared=(("龍", "天龍"),)))
    glyphs = next(check for check in checks if check.name == "glyphs")
    assert glyphs.ok is False and "龍" in glyphs.detail


def test_a_missing_cue_fails_the_cue_gate():
    checks = evaluate_composition_qa(_ok_inputs(missing_cues=("s3: 'bamboo'",)))
    assert next(c for c in checks if c.name == "cues").ok is False


def test_a_blank_frame_fails_the_visual_gate():
    checks = evaluate_composition_qa(_ok_inputs(frame_stddevs=(40.0, 1.2)))
    assert next(c for c in checks if c.name == "visual").ok is False


def test_no_frames_at_all_fails_the_visual_gate():
    checks = evaluate_composition_qa(_ok_inputs(frame_stddevs=()))
    assert next(c for c in checks if c.name == "visual").ok is False


def test_a_failed_check_report_fails_the_composition_gate():
    checks = evaluate_composition_qa(_ok_inputs(composition_report="FAILED: overflow"))
    assert next(c for c in checks if c.name == "composition").ok is False


def test_an_out_of_bounds_text_rect_fails_the_safe_area_gate():
    checks = evaluate_composition_qa(_ok_inputs(safe_area=("title at (0, 0, 1080, 200)",)))
    safe_area = next(c for c in checks if c.name == "safe_area")
    assert safe_area.ok is False
    assert "title at (0, 0, 1080, 200)" in safe_area.detail


def test_cue_problems_finds_a_phrase_the_scene_never_says():
    plan = {
        "scenes": [
            {
                "id": "s0",
                "props": {
                    "cue": "bamboo",
                    "word_times": [{"word": "plum", "start": 0.2, "end": 0.4}],
                },
            }
        ]
    }
    assert cue_problems(plan) == ["s0: 'bamboo'"]


def test_cue_problems_ignores_a_scene_without_a_cue():
    assert cue_problems({"scenes": [{"id": "s0", "props": {}}]}) == []


# ---------------------------------------------------------------------------
# The layout cue gate: the renderer's own cues, not just props["cue"]
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
WIZ_PLAN = REPO_ROOT / "projects" / "wiz-explainer" / "plan" / "scene_plan.json"


def _spoken(*words: str) -> list[dict[str, Any]]:
    return [
        {"word": word, "start": 0.1 * index, "end": 0.1 * index + 0.05}
        for index, word in enumerate(words, start=1)
    ]


def _wiz_plan() -> dict[str, Any]:
    if not WIZ_PLAN.is_file():
        # ``projects/`` is runtime scratch space; a clean checkout may not carry
        # the shipped plan. The constructed plans below still cover the gate.
        pytest.skip(f"shipped wiz plan is not present at {WIZ_PLAN}")
    return json.loads(WIZ_PLAN.read_text(encoding="utf-8"))


def test_the_shipped_wiz_plan_has_no_unanswered_cues():
    """The plan was corrected on 2026-10-26 (spec §7.1, "Cue content fix").

    Every scene leaves ``props["cue"]`` unset, so a pass here can only come
    from reading the renderer's own cue map — the mutation test below is the
    red direction, and it edits nothing except a bar label.
    """
    plan = _wiz_plan()
    assert all("cue" not in (scene.get("props") or {}) for scene in plan["scenes"])
    assert cue_problems(plan) == []


def test_mutating_a_resolving_bar_label_makes_the_cue_gate_go_red():
    plan = _wiz_plan()
    scene = next(scene for scene in plan["scenes"] if scene["id"] == "scene_3")
    # "Nest" resolves today; the mutated label must be reported by name.
    assert "scene_3: 'Nest'" not in cue_problems(plan)
    scene["props"]["bars"][1]["label"] = "Bananagrams"
    assert "scene_3: 'Bananagrams'" in cue_problems(plan)


def test_a_definition_term_the_scene_never_says_fails_the_cue_gate():
    plan = {
        "scenes": [
            {
                "id": "d1",
                "props": {
                    "layout": "definition",
                    "term": "Cloud security",
                    "gloss": "A digital X-ray.",
                    "word_times": _spoken("Cloud", "security", "means"),
                },
            }
        ]
    }
    assert cue_problems(plan) == []
    plan["scenes"][0]["props"]["term"] = "Quantum knitting"
    assert cue_problems(plan) == ["d1: 'Quantum knitting'"]


def test_a_sequence_step_the_scene_never_says_fails_the_cue_gate():
    plan = {
        "scenes": [
            {
                "id": "seq",
                "props": {
                    "layout": "sequence",
                    "steps": [
                        "Everyone moved to the cloud.",
                        "Every cloud became a maze.",
                        "A breach starts with a wrong key.",
                    ],
                    "word_times": _spoken(
                        "Everyone",
                        "moved",
                        "to",
                        "the",
                        "cloud.",
                        "Every",
                        "cloud",
                        "became",
                        "a",
                        "maze.",
                        "A",
                        "breach",
                        "starts",
                        "with",
                        "a",
                        "wrong",
                        "key.",
                    ),
                },
            }
        ]
    }
    assert cue_problems(plan) == []
    plan["scenes"][0]["props"]["steps"][1] = "Every cloud became a labyrinth."
    assert cue_problems(plan) == ["seq: 'Every cloud became a labyrinth.'"]


def test_an_unbuildable_layout_fails_the_cue_gate():
    plan = {"scenes": [{"id": "bad", "props": {"layout": "hologram"}}]}
    problems = cue_problems(plan)
    assert len(problems) == 1
    assert problems[0].startswith("bad: layout 'hologram' cues unreadable")


def test_a_cue_reported_by_both_paths_is_listed_once():
    plan = {
        "scenes": [
            {
                "id": "s",
                "props": {
                    "layout": "big_stat",
                    "headline": "Orbit",
                    "cue": "orbit",
                    "word_times": _spoken("plum"),
                },
            }
        ]
    }
    # ``props["cue"]`` and the big-stat item cue are the same phrase.
    assert cue_problems(plan) == ["s: 'orbit'"]


# ---------------------------------------------------------------------------
# gather_composition_qa: the integration path, with every external call stubbed
# ---------------------------------------------------------------------------


def _one_scene_plan() -> dict[str, Any]:
    """A minimal typographic plan whose single cue matches its spoken word."""
    return {
        "job_id": "clean",
        "aspect_ratio": "9:16",
        "scenes": [
            {
                "id": "s0",
                "type": "typographic",
                "duration_seconds": 2.0,
                "props": {
                    "layout": "big_stat",
                    "headline": "Orbit",
                    "cue": "orbit",
                    "word_times": [{"word": "orbit", "start": 0.1, "end": 0.4}],
                },
            }
        ],
    }


def _stub_externals(monkeypatch, *, snapshot=None, check=None, stddev=None, diff=None) -> None:
    monkeypatch.setattr(composition_qa, "load_glyph_manifest", lambda *a, **k: {})
    monkeypatch.setattr(composition_qa, "check_project", check or (lambda *a, **k: "0 issues"))
    monkeypatch.setattr(
        composition_qa,
        "snapshot_project",
        snapshot or (lambda *a, **k: [Path("frame.png")]),
    )
    monkeypatch.setattr(composition_qa, "frame_stddev", stddev or (lambda path: 40.0))
    monkeypatch.setattr(composition_qa, "frame_difference", diff or (lambda a, b: 4.0))


def test_gather_composition_qa_returns_the_ten_checks_for_a_clean_build(
    tmp_path: Path, monkeypatch
):
    _stub_externals(monkeypatch)
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        plan=_one_scene_plan(),
        frame_dir=tmp_path / "frames",
    )
    assert [check.name for check in checks] == [
        "glyphs",
        "cues",
        "safe_area",
        "composition",
        "visual",
        "layers",
        "flourishes",
        "map_motion",
        "map_markers",
        "advisories",
        "look_bans",
    ]
    assert all(check.ok for check in checks)
    assert next(check for check in checks if check.name == "map_motion").detail == (
        "no camera moves"
    )
    assert next(check for check in checks if check.name == "advisories").detail == "none"


def test_gather_composition_qa_fails_the_glyph_gate_when_the_manifest_is_missing(
    tmp_path: Path, monkeypatch
):
    def missing(*args, **kwargs):
        raise ValueError("manifest is missing")

    _stub_externals(monkeypatch)
    monkeypatch.setattr(composition_qa, "load_glyph_manifest", missing)
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        plan=_one_scene_plan(),
        frame_dir=tmp_path / "frames",
    )
    assert len(checks) == 1
    assert checks[0].name == "glyphs"
    assert checks[0].ok is False
    assert "manifest is missing" in checks[0].detail


def test_gather_composition_qa_fails_the_visual_gate_when_snapshotting_raises(
    tmp_path: Path, monkeypatch
):
    def no_browser(*args, **kwargs):
        raise RuntimeError("no browser")

    _stub_externals(monkeypatch, snapshot=no_browser)
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        plan=_one_scene_plan(),
        frame_dir=tmp_path / "frames",
    )
    visual = next(check for check in checks if check.name == "visual")
    assert visual.ok is False
    assert "no browser" in visual.detail


def test_gather_composition_qa_reports_a_failed_hyperframes_check(tmp_path: Path, monkeypatch):
    def failed(*args, **kwargs):
        raise HyperFramesCheckError(["layout: text overflows"])

    _stub_externals(monkeypatch, check=failed)
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        plan=_one_scene_plan(),
        frame_dir=tmp_path / "frames",
    )
    composition = next(check for check in checks if check.name == "composition")
    assert composition.ok is False
    assert "layout: text overflows" in composition.detail


def test_layer_texts_includes_rich_text_spans():
    composition = Composition(
        job_id="captions",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=1.0,
        scenes=[
            CompositionScene(
                id="s0",
                start=0.0,
                end=1.0,
                layers=[
                    CompositionLayer(
                        id="caption",
                        kind="rich_text",
                        rect=Rect(x=86, y=1400, width=900, height=120),
                        time=LayerTime(start=0.0, end=1.0),
                        content=LayerContent(spans=[WordSpan(text="四"), WordSpan(text="柱")]),
                    )
                ],
            )
        ],
    )
    assert layer_texts(composition) == ["四", "柱"]


def test_frame_stddev_separates_black_from_noise(tmp_path: Path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not available to generate test frames")
    black = tmp_path / "black.png"
    noise = tmp_path / "noise.png"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32",
            "-frames:v",
            "1",
            str(black),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32,noise=alls=100:allf=t",
            "-frames:v",
            "1",
            str(noise),
        ],
        check=True,
    )
    assert frame_stddev(black) < MIN_FRAME_STDDEV
    assert frame_stddev(noise) >= MIN_FRAME_STDDEV


def test_frame_stddev_rejects_a_failed_decode(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        composition_qa,
        "run_ffmpeg",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"\x00\x01"),
    )
    with pytest.raises(ValueError, match="exit 1"):
        frame_stddev(tmp_path / "broken.png")


def test_gather_composition_qa_fails_closed_without_a_cue_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_externals(monkeypatch)
    composition = Composition(
        job_id="demo",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=1.0,
        scenes=[CompositionScene(id="hook", start=0.0, end=1.0)],
    )
    checks = gather_composition_qa(
        composition_dir=tmp_path,
        composition=composition,
    )
    cues = next(check for check in checks if check.name == "cues")
    assert not cues.ok
    assert "cues were not measured" in cues.detail


def test_gather_composition_qa_accepts_resolved_cues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_externals(monkeypatch)
    composition = Composition(
        job_id="demo",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=1.0,
        scenes=[CompositionScene(id="hook", start=0.0, end=1.0)],
    )
    checks = gather_composition_qa(
        composition_dir=tmp_path,
        composition=composition,
        declared_cues=2,
        resolved_cues=2,
    )
    assert next(check for check in checks if check.name == "cues").ok


def test_gather_composition_qa_fails_the_cue_gate_on_a_shortfall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_externals(monkeypatch)
    composition = Composition(
        job_id="demo",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=1.0,
        scenes=[CompositionScene(id="hook", start=0.0, end=1.0)],
    )
    checks = gather_composition_qa(
        composition_dir=tmp_path,
        composition=composition,
        declared_cues=2,
        resolved_cues=1,
    )
    cues = next(check for check in checks if check.name == "cues")
    assert not cues.ok
    assert "1 declared cues did not resolve" in cues.detail


def test_gather_composition_qa_refuses_a_plan_and_a_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_externals(monkeypatch)
    composition = Composition(
        job_id="demo",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=1.0,
        scenes=[CompositionScene(id="hook", start=0.0, end=1.0)],
    )
    with pytest.raises(ValueError, match="pass plan or composition, not both"):
        gather_composition_qa(
            composition_dir=tmp_path,
            plan=_one_scene_plan(),
            composition=composition,
        )


def _parallax_composition() -> Composition:
    return Composition(
        job_id="layers",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=2.0,
        scenes=[
            CompositionScene(
                id="s0",
                start=0.0,
                end=2.0,
                layers=[
                    CompositionLayer(
                        id="bg",
                        kind="media",
                        rect=Rect(x=0, y=0, width=1080, height=1920),
                        time=LayerTime(start=0.0, end=2.0),
                        content=LayerContent(source="bg.png"),
                        motions=[MotionSpec(name="parallax", params={"rate": 0.35})],
                        media_kind="image",
                    )
                ],
            )
        ],
    )


def test_a_frozen_parallax_fails_the_layers_gate(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch, diff=lambda a, b: 0.0)
    composition = _parallax_composition()
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        composition=composition,
        frame_dir=tmp_path / "frames",
    )
    layers = next(check for check in checks if check.name == "layers")
    assert layers.ok is False
    assert "frozen" in layers.detail.lower()


def test_a_moving_parallax_passes_the_layers_gate(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch, diff=lambda a, b: 3.2)
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        composition=_parallax_composition(),
        frame_dir=tmp_path / "frames",
    )
    layers = next(check for check in checks if check.name == "layers")
    assert layers.ok is True
    assert "min luma diff 3.20" in layers.detail


def test_frame_difference_separates_black_from_white(tmp_path: Path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not available to generate test frames")
    first = tmp_path / "black.png"
    second = tmp_path / "white.png"
    for color, path in (("black", first), ("white", second)):
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=32x32",
                "-frames:v",
                "1",
                str(path),
            ],
            check=True,
        )
    assert frame_difference(first, first) == pytest.approx(0.0)
    assert frame_difference(first, second) == pytest.approx(255.0)


def test_three_flourishes_warn_but_pass(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch)
    composition = Composition(
        job_id="flourishes",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=1.0,
        scenes=[
            CompositionScene(
                id="s0",
                start=0.0,
                end=1.0,
                layers=[
                    CompositionLayer(
                        id=f"leak_{index}",
                        kind="html",
                        rect=Rect(x=0, y=0, width=1080, height=1920),
                        time=LayerTime(start=0.0, end=1.0),
                        style={"flourish": "light_leak"},
                    )
                    for index in range(3)
                ],
            )
        ],
    )
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        composition=composition,
        frame_dir=tmp_path / "frames",
    )
    flourishes = next(check for check in checks if check.name == "flourishes")
    assert flourishes.ok is True
    assert "3 flourish" in flourishes.detail


# ---------------------------------------------------------------------------
# Mid-motion map geometry and advisory records (#332, #334)
# ---------------------------------------------------------------------------

MALACCA_PLAN = REPO_ROOT / "projects" / "editorial-pilot-malacca-3" / "plan" / "scene_plan.json"


def _malacca_plan() -> dict[str, Any]:
    if not MALACCA_PLAN.is_file():
        pytest.skip(f"shipped Malacca pilot plan is not present at {MALACCA_PLAN}")
    return json.loads(MALACCA_PLAN.read_text(encoding="utf-8"))


def test_the_malacca_pilot_passes_the_map_motion_gate(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch)
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        plan=_malacca_plan(),
        frame_dir=tmp_path / "frames",
    )
    map_motion = next(check for check in checks if check.name == "map_motion")
    assert map_motion.ok is True, map_motion.detail
    assert map_motion.detail != "no camera moves"


def _moving_map_plan(*, zooms: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "job_id": "map_motion",
        "aspect_ratio": "16:9",
        "design": "paper_editorial",
        "scenes": [
            {
                "id": "map_1",
                "type": "map",
                "duration_seconds": 8.0,
                "narration": "The strait runs long and narrow.",
                "props": {
                    "country": "Malaysia",
                    "bbox": "95,0.5,119,8",
                    "markers": [
                        {
                            "text": "A very long place label that will not fit the frame " * 3,
                            "lon": 108.0,
                            "lat": 4.0,
                            "cue": False,
                        }
                    ],
                    "zooms": zooms,
                },
            }
        ],
    }


def test_a_label_that_leaves_the_map_frame_fails_the_map_motion_gate(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch)
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        plan=_moving_map_plan(zooms=[{"bbox": "100,0.7,112,5"}]),
        frame_dir=tmp_path / "frames",
    )
    map_motion = next(check for check in checks if check.name == "map_motion")
    assert map_motion.ok is False
    assert "label lands outside the map frame" in map_motion.detail


def test_a_still_map_reports_no_camera_moves(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch)
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        plan=_one_scene_plan(),
        frame_dir=tmp_path / "frames",
    )
    map_motion = next(check for check in checks if check.name == "map_motion")
    assert map_motion.ok is True
    assert map_motion.detail == "no camera moves"


def _advisories_plan() -> dict[str, Any]:
    return {
        "job_id": "advisories",
        "aspect_ratio": "16:9",
        "design": "paper_editorial",
        "scenes": [
            {
                "id": "scene_1",
                "type": "map",
                "duration_seconds": 2.0,
                "narration": "A short map scene.",
                "props": {
                    "country": "Malaysia",
                    "bbox": "95,0.5,119,8",
                    "zooms": [
                        {"bbox": "100.0,1.0,104.5,5.5"},
                        {"bbox": "100.0,1.0,104.5,5.5"},
                        {"bbox": "100.0,1.0,104.5,5.5"},
                    ],
                },
            }
        ],
    }


def test_advisories_lists_every_dropped_zoom_and_still_passes(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch)
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        plan=_advisories_plan(),
        frame_dir=tmp_path / "frames",
    )
    advisories = next(check for check in checks if check.name == "advisories")
    assert advisories.ok is True
    assert "dropped zooms:" in advisories.detail
    assert "zoom 1 dropped" in advisories.detail
    assert "zoom 2 dropped" in advisories.detail


def test_advisories_lists_silenced_cues_from_the_build_file(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch)
    composition_dir = tmp_path / "composition"
    build = tmp_path / "build"
    build.mkdir(parents=True)
    (build / "silenced_cues.json").write_text(
        json.dumps(
            [
                {"scene": "map_1", "item": "marker_1", "phrase": "Malacca"},
                {"scene": "map_1", "item": "marker_9", "phrase": "gone marker"},
            ]
        ),
        encoding="utf-8",
    )
    checks = gather_composition_qa(
        composition_dir=composition_dir,
        plan=_restored_and_silenced_plan(),
        frame_dir=tmp_path / "frames",
    )
    advisories = next(check for check in checks if check.name == "advisories")
    assert advisories.ok is True
    assert "silenced cues:" in advisories.detail
    assert "map_1: marker_1 silenced 'Malacca'" in advisories.detail
    assert "marker_9" not in advisories.detail


def test_advisories_says_none_when_nothing_was_dropped_or_silenced(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch)
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        plan=_one_scene_plan(),
        frame_dir=tmp_path / "frames",
    )
    advisories = next(check for check in checks if check.name == "advisories")
    assert advisories.ok is True
    assert advisories.detail == "none"


def _restored_and_silenced_plan() -> dict[str, Any]:
    return {
        "job_id": "stale_cues",
        "aspect_ratio": "16:9",
        "design": "paper_editorial",
        "scenes": [
            {
                "id": "map_1",
                "type": "map",
                "duration_seconds": 8.0,
                "narration": "Singapore and Malacca.",
                "props": {
                    "country": "Malaysia",
                    "bbox": "95,0.5,119,8",
                    "markers": [
                        {
                            "text": "Singapore",
                            "lon": 103.85,
                            "lat": 1.29,
                            "cue": "Singapore",
                        },
                        {
                            "text": "Malacca",
                            "lon": 102.25,
                            "lat": 2.19,
                            "cue": False,
                        },
                    ],
                    "word_times": [
                        {"word": "Singapore", "start": 1.0, "end": 1.4},
                        {"word": "Malacca", "start": 1.5, "end": 1.9},
                    ],
                },
            }
        ],
    }


def test_stale_silenced_cues_only_report_scenes_and_cues_still_off(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch)
    composition_dir = tmp_path / "composition"
    build = tmp_path / "build"
    build.mkdir(parents=True)
    (build / "silenced_cues.json").write_text(
        json.dumps(
            [
                {"scene": "gone", "item": "item_0", "phrase": "ghost"},
                {"scene": "map_1", "item": "marker_0", "phrase": "Singapore"},
                {"scene": "map_1", "item": "zoom_0", "phrase": "the strait"},
                {"scene": "map_1", "item": "marker_1", "phrase": "Malacca"},
            ]
        ),
        encoding="utf-8",
    )

    checks = gather_composition_qa(
        composition_dir=composition_dir,
        plan=_restored_and_silenced_plan(),
        frame_dir=tmp_path / "frames",
    )
    advisories = next(check for check in checks if check.name == "advisories")
    assert advisories.ok is True
    assert "ghost" not in advisories.detail
    assert "marker_0" not in advisories.detail
    assert "zoom_0" not in advisories.detail
    assert "marker_1" in advisories.detail
    assert "Malacca" in advisories.detail


def _time_base_plan() -> dict[str, Any]:
    return {
        "job_id": "time_base",
        "aspect_ratio": "16:9",
        "design": "paper_editorial",
        "scenes": [
            {
                "id": "still_1",
                "type": "narration",
                "duration_seconds": 6.5,
                "narration": "A first still.",
                "visual_description": "first still",
            },
            {
                "id": "still_2",
                "type": "narration",
                "duration_seconds": 7.75,
                "narration": "A second still.",
                "visual_description": "second still",
            },
            {
                "id": "map_1",
                "type": "map",
                "duration_seconds": 8.0,
                "narration": "The strait runs north.",
                "props": {
                    "country": "Malaysia",
                    "bbox": "95,0.5,119,8",
                    "markers": [
                        {
                            "text": "Malacca",
                            "lon": 102.25,
                            "lat": 2.19,
                            "cue": False,
                        }
                    ],
                    "zooms": [
                        {"bbox": "100,0.7,104,5", "cue": "the strait"},
                        {"bbox": "100,0.7,104,5", "cue": "north"},
                    ],
                    "word_times": [
                        {"word": "the", "start": 20.3283, "end": 20.4},
                        {"word": "strait", "start": 20.4, "end": 20.6},
                        {"word": "north", "start": 15.5, "end": 15.7},
                    ],
                },
            },
        ],
    }


def _compiled_camera_move_count(plan: dict[str, Any]) -> int:
    composition = from_scene_plan(plan)
    layer = next(
        layer for scene in composition.scenes for layer in scene.layers if layer.preset == "map"
    )
    css = str(layer.style.get("extra_css", ""))
    block = re.search(r"@keyframes map_camera_\d+ \{(.*?)\n      \}", css, re.S)
    assert block is not None
    # Each move adds one start stop carrying the easing curve. A shared
    # landing also appears as the next move's start, so count stops not scales.
    return block.group(1).count("animation-timing-function")


def test_qa_rebases_words_on_the_quantised_start_the_compiler_uses(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch)
    plan = _time_base_plan()
    compiled_moves = _compiled_camera_move_count(plan)
    assert compiled_moves == 2

    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        plan=plan,
        frame_dir=tmp_path / "frames",
    )
    advisories = next(check for check in checks if check.name == "advisories")
    dropped = len(re.findall(r"zoom \d+ dropped", advisories.detail))
    assert 2 - dropped == compiled_moves
    assert "zoom 1 dropped" not in advisories.detail

    map_motion = next(check for check in checks if check.name == "map_motion")
    assert map_motion.ok is True
    assert map_motion.detail != "no camera moves"


def test_map_motion_turns_an_unexpected_error_into_a_failing_check(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch)

    def boom(*args: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("geometry exploded")

    monkeypatch.setattr(composition_qa, "map_scene_motion_problems", boom)
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        plan=_moving_map_plan(zooms=[{"bbox": "100,0.7,112,5"}]),
        frame_dir=tmp_path / "frames",
    )
    map_motion = next(check for check in checks if check.name == "map_motion")
    assert map_motion.ok is False
    assert "geometry exploded" in map_motion.detail


def test_advisories_turns_an_unexpected_error_into_a_failing_check(tmp_path: Path, monkeypatch):
    _stub_externals(monkeypatch)

    def boom(*args: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("advisory exploded")

    monkeypatch.setattr(composition_qa, "map_scene_motion_warnings", boom)
    checks = gather_composition_qa(
        composition_dir=tmp_path / "composition",
        plan=_advisories_plan(),
        frame_dir=tmp_path / "frames",
    )
    advisories = next(check for check in checks if check.name == "advisories")
    assert advisories.ok is False
    assert "advisory exploded" in advisories.detail


def test_map_markers_gate_fails_a_city_in_the_sea():
    from video_editor_pipelines.composition_qa import _map_markers_check

    lost = {
        "scenes": [
            {
                "id": "s3",
                "type": "map",
                "props": {
                    "country": "India",
                    "markers": [{"text": "Port", "lon": 85.0, "lat": 15.0}],
                },
            }
        ]
    }
    fine = {
        "scenes": [
            {
                "id": "s3",
                "type": "map",
                "props": {"country": "India", "markers": [{"text": "Chennai"}]},
            }
        ]
    }
    check = _map_markers_check(lost)
    assert check.name == "map_markers" and not check.ok and "Port" in check.detail
    assert _map_markers_check(fine).ok
    assert _map_markers_check(None).ok


def test_repeated_concepts_flags_a_title_featured_in_two_distant_scenes():
    from video_editor_pipelines.composition_qa import repeated_concepts

    scenes = [
        {"id": "scene_9", "props": {"headline": "The monsoon"}},
        {"id": "scene_10", "props": {"headline": "7,500 km"}},
        {"id": "scene_11", "props": {"headline": "Where the land breaks apart"}},
        {"id": "scene_12", "props": {"term": "Monsoon"}},
    ]
    assert repeated_concepts(scenes) == ["scene_9 and scene_12 both feature 'monsoon'"]


def test_repeated_concepts_allows_adjacent_scenes_and_distinct_titles():
    from video_editor_pipelines.composition_qa import repeated_concepts

    scenes = [
        {"id": "a", "props": {"headline": "The Deccan"}},
        {"id": "b", "props": {"headline": "Deccan"}},
        {"id": "c", "props": {"headline": "The Thar"}},
    ]
    assert repeated_concepts(scenes) == []
