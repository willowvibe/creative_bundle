"""Engine-wide motion quality: narration cues, spotlights, karaoke, ambient motion.

Covers the features that keep generated videos from reading as static slides:
``video_editor_core.cues`` (phrase -> time, spotlight shape layers), the
HyperFrames ``shape`` layer and karaoke upgrades, motion-graphics cue timing /
ambient layers / camera drift / draw-on edges, varied Ken Burns moves, and the
QA freeze detector.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from captions.captions import caption_graph_layers
from captions.models import CaptionLine, CaptionStyle, CaptionWord
from motion_graphics.motion_graphics import DiagramSpec, MotionGraphicsSkill, _plan_to_html
from qa.qa import detect_freeze_regions
from video_editor_core.cues import find_cue, resolve_cues, spotlight_layer
from video_editor_core.hyperframes_project import compile_hyperframes_project
from video_editor_core.render_graph import Cut, Rect, RenderGraph
from video_editor_core.scene_plan_composition import compile_scene_plan_project
from video_editor_state.job import JobContext

WORDS = [
    {"word": "The", "start": 0.0, "end": 0.2},
    {"word": "Sun's", "start": 0.2, "end": 0.5},
    {"word": "gravity", "start": 0.5, "end": 1.0},
    {"word": "pulls,", "start": 1.0, "end": 1.3},
    {"word": "while", "start": 1.3, "end": 1.5},
    {"word": "planets", "start": 1.5, "end": 2.0},
    {"word": "move", "start": 2.0, "end": 2.3},
    {"word": "sideways.", "start": 2.3, "end": 2.9},
    {"word": "Gravity", "start": 3.5, "end": 4.0},
    {"word": "wins.", "start": 4.0, "end": 4.4},
]


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    job = JobContext("motion-quality", work_root=tmp_path / "work", output_root=tmp_path / "out")
    job.ensure_dirs()
    return job


# --- cues ---------------------------------------------------------------------


def test_find_cue_ignores_case_and_punctuation() -> None:
    assert find_cue(WORDS, "PULLS") == pytest.approx(1.0)
    assert find_cue(WORDS, "sun's gravity") == pytest.approx(0.2)


def test_find_cue_prefix_and_window() -> None:
    assert find_cue(WORDS, "planet") == pytest.approx(1.5)  # prefix hit on "planets"
    assert find_cue(WORDS, "planet", prefix=False) is None
    assert find_cue(WORDS, "gravity", after=1.0) == pytest.approx(3.5)
    assert find_cue(WORDS, "gravity", after=1.0, before=3.0) is None


def test_find_cue_matches_numbers_by_value() -> None:
    words = [
        {"word": "About", "start": 6.0, "end": 6.2},
        {"word": "60", "start": 6.2, "end": 6.6},
        {"word": "%", "start": 6.6, "end": 6.9},
        {"word": "thirty", "start": 7.0, "end": 7.2},
        {"word": "seven", "start": 7.2, "end": 7.4},
        {"word": "trillion", "start": 7.4, "end": 7.8},
        {"word": "cells,", "start": 7.8, "end": 8.0},
        {"word": "billions", "start": 8.2, "end": 8.6},
        {"word": "6", "start": 8.7, "end": 8.8},
    ]
    assert find_cue(words, "sixty percent") == pytest.approx(6.2)
    assert find_cue(words, "60%") == pytest.approx(6.2)
    assert find_cue(words, "37000000000000 cells") == pytest.approx(7.0)
    assert find_cue(words, "billions") == pytest.approx(8.2)
    # Digits never prefix-match: "6" must not hit "60".
    assert find_cue(words, "six") == pytest.approx(8.7)


def test_resolve_cues_applies_lead_and_drops_missing() -> None:
    cues = resolve_cues(WORDS, {"pull": "pulls", "side": "sideways", "x": "neptune"}, lead=0.1)
    assert cues == {"pull": pytest.approx(0.9), "side": pytest.approx(2.2)}
    # Never earlier than the window start.
    assert resolve_cues(WORDS, {"a": "The"}, lead=0.5) == {"a": 0.0}


def test_spotlight_layer_is_a_hyperframes_shape() -> None:
    layer = spotlight_layer("calcium", Rect(x=10, y=20, width=200, height=80), 1.0, 3.0)
    assert layer.kind == "shape"
    assert layer.style["effect"] == "spotlight"
    assert layer.style["requires_motion"] is True
    assert (layer.timing.start, layer.timing.end) == (1.0, 3.0)


# --- HyperFrames compiler ---------------------------------------------------------


def _graph(tmp_path: Path, overlays: list) -> RenderGraph:
    source = tmp_path / "base.mp4"
    source.write_bytes(b"video")
    return RenderGraph(
        job_id="mq",
        width=1920,
        height=1080,
        fps=30,
        source=str(source),
        has_audio=False,
        cuts=[Cut(source_start=0.0, source_end=5.0, timeline_start=0.0, timeline_end=5.0)],
        overlays=overlays,
    )


def test_spotlight_shape_compiles_with_glow_and_fade(tmp_path: Path) -> None:
    layer = spotlight_layer(
        "box", Rect(x=100, y=200, width=300, height=120), 1.0, 3.0, color="#38BDF8"
    )
    html = compile_hyperframes_project(_graph(tmp_path, [layer]), tmp_path / "p").read_text()
    clip = next(line for line in html.splitlines() if 'id="box_0"' in line)
    assert 'data-start="1.000" data-duration="2.000"' in clip
    assert "border:5px solid #38BDF8" in clip
    assert "ve_spot_in" in clip and "ve_spot_breathe" in clip
    assert "ve_spot_out 0.3s ease-in 1.700s forwards" in clip
    assert "@keyframes ve_spot_in" in html


def test_karaoke_preset_dims_upcoming_words_and_pops(tmp_path: Path) -> None:
    style = CaptionStyle.preset("karaoke")
    assert style.upcoming_opacity == pytest.approx(0.6)
    line = CaptionLine(
        words=[
            CaptionWord(text="Hello", start=1.0, end=1.4),
            CaptionWord(text="world", start=1.5, end=2.0),
        ]
    )
    layers = caption_graph_layers([line], Rect(y=900, width=1920, height=150), style)
    assert layers[0].style["upcoming_opacity"] == pytest.approx(0.6)
    html = compile_hyperframes_project(_graph(tmp_path, layers), tmp_path / "p").read_text()
    assert html.count("opacity:0.60;") == 2
    assert "ve_word_pop" in html and "ve_word_seen 0.001s linear 0.500s forwards" in html
    assert ".ve-line {" in html and "ve_line_in" in html


def test_karaoke_without_dimming_keeps_full_opacity(tmp_path: Path) -> None:
    style = CaptionStyle.preset("karaoke", upcoming_opacity=1.0)
    line = CaptionLine(words=[CaptionWord(text="Hi", start=0.0, end=0.4)])
    layers = caption_graph_layers([line], Rect(y=900, width=1920, height=150), style)
    html = compile_hyperframes_project(_graph(tmp_path, layers), tmp_path / "p").read_text()
    assert "ve_word_seen 0.001s" not in html
    assert "ve_word_hl 0.400s step-end 0.000s" in html


# --- motion graphics --------------------------------------------------------------


def _diagram(**kw: object) -> DiagramSpec:
    base: dict[str, object] = {
        "title": "Why planets orbit",
        "duration": 6.0,
        "nodes": [
            {
                "id": "a",
                "label": "Gravity",
                "x": 100,
                "y": 300,
                "width": 400,
                "height": 100,
                "at": 1.25,
            },
            {
                "id": "b",
                "label": "Sideways",
                "x": 100,
                "y": 600,
                "width": 400,
                "height": 100,
                "emphasize": [4.0],
            },
            {
                "id": "sat",
                "label": "",
                "x": 1300,
                "y": 400,
                "width": 200,
                "height": 200,
                "shape": "circle",
                "ring": True,
            },
        ],
        "edges": [{"from": "a", "to": "b", "arrow": True, "at": 2.0}],
    }
    base.update(kw)
    return DiagramSpec(**base)  # type: ignore[arg-type]


def test_cue_times_override_default_stagger(ctx: JobContext) -> None:
    plan = MotionGraphicsSkill(ctx).build_diagram_plan(_diagram())
    by_name = {layer["name"]: layer for layer in plan["layers"]}
    assert by_name["node_a"]["delay"] == pytest.approx(1.25)
    assert by_name["label_a"]["delay"] == pytest.approx(1.25)
    assert by_name["edge_a_b"]["delay"] == pytest.approx(2.0)
    assert by_name["node_b"]["emphasize"] == [4.0]


def test_ambient_layer_sits_directly_above_background(ctx: JobContext) -> None:
    plan = MotionGraphicsSkill(ctx).build_diagram_plan(_diagram(ambient="stars"))
    assert [layer["name"] for layer in plan["layers"][:2]] == ["background", "ambient"]
    html = _plan_to_html(plan)
    assert "mg_twinkle" in html and "mg_star_drift_2" in html
    none_plan = MotionGraphicsSkill(ctx).build_diagram_plan(_diagram(ambient="none"))
    assert all(layer["name"] != "ambient" for layer in none_plan["layers"])


def test_camera_drift_moves_content_not_background(ctx: JobContext) -> None:
    plan = MotionGraphicsSkill(ctx).build_diagram_plan(_diagram())
    html = _plan_to_html(plan)
    assert "@keyframes mg_cam" in html
    background = next(line for line in html.splitlines() if 'id="background_0"' in line)
    title = next(line for line in html.splitlines() if 'id="title_' in line)
    assert "mg_cam" not in background
    assert "mg_cam 6.000s" in title
    # Disabled -> no layer pans.
    still = _plan_to_html(MotionGraphicsSkill(ctx).build_diagram_plan(_diagram(camera_drift=False)))
    assert "mg_cam 6.000s" not in still


def test_html_is_deterministic(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx)
    first = _plan_to_html(skill.build_diagram_plan(_diagram(ambient="stars")))
    second = _plan_to_html(skill.build_diagram_plan(_diagram(ambient="stars")))
    assert first == second


def test_edges_draw_on_and_stop_at_node_outline(ctx: JobContext) -> None:
    html = _plan_to_html(MotionGraphicsSkill(ctx).build_diagram_plan(_diagram()))
    edge = next(line for line in html.splitlines() if "<line" in line)
    assert "mg_draw 0.7s" in edge and 'pathLength="1"' in edge
    assert "<polygon" in edge  # arrow head
    y1 = float(re.search(r'y1="([\d.]+)"', edge).group(1))
    y2 = float(re.search(r'y2="([\d.]+)"', edge).group(1))
    # Node a spans y 300-400, node b 600-700: the edge runs between the boxes.
    assert 400 < y1 < 420 and 580 < y2 < 600


def test_emphasis_pulse_and_planet_ring(ctx: JobContext) -> None:
    html = _plan_to_html(MotionGraphicsSkill(ctx).build_diagram_plan(_diagram()))
    node_b = next(line for line in html.splitlines() if 'id="node_b_' in line)
    assert "mg_pulse 0.7s ease-out 4.000s" in node_b
    saturn = next(line for line in html.splitlines() if 'id="node_sat_' in line)
    assert "rotate(-16deg)" in saturn and "radial-gradient" in saturn and "mg_breathe" in saturn


# --- Ken Burns variety ------------------------------------------------------------


def test_ken_burns_moves_alternate_between_scenes(tmp_path: Path) -> None:
    image = tmp_path / "still.png"
    image.write_bytes(b"png")
    plan = {
        "job_id": "kb",
        "aspect_ratio": "16:9",
        "scenes": [
            {"duration_seconds": 2.0, "assets": [{"asset_type": "image", "local_path": str(image)}]}
            for _ in range(4)
        ],
    }
    html = compile_scene_plan_project(plan, tmp_path / "proj").read_text()
    frames = re.findall(r"@keyframes ve_drift_[0-9a-f]{8} \{ (.*?) \}\s*$", html, flags=re.M)
    assert len(frames) == 4 and len(set(frames)) == 4
    assert "ease-in-out both" in html


# --- QA freeze detection ------------------------------------------------------------


def test_detect_freeze_regions_flags_a_held_frame(tmp_path: Path) -> None:
    video = tmp_path / "held.mp4"
    # 1s of moving test pattern, then 2.5s of a solid colour.
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=25",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x334455:duration=2.5:size=320x240:rate=25",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1[v]",
            "-map",
            "[v]",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    regions = detect_freeze_regions(video)
    assert regions, "expected a freeze region"
    start, _ = regions[0]
    assert 0.9 <= start <= 1.2


def test_word_pop_never_grows_a_word_sideways_over_its_space(tmp_path: Path) -> None:
    # #338: a horizontal scale on an inline-block word paints over the single
    # space beside it, so "kilometres a" read as "kilometresa" mid-pop.
    style = CaptionStyle.preset("karaoke")
    line = CaptionLine(
        words=[
            CaptionWord(text="kilometres", start=0.0, end=0.5),
            CaptionWord(text="a", start=0.5, end=0.7),
        ]
    )
    layers = caption_graph_layers([line], Rect(y=900, width=1920, height=150), style)
    html = compile_hyperframes_project(_graph(tmp_path, layers), tmp_path / "p").read_text()
    pop = html.split("@keyframes ve_word_pop", 1)[1].split("@keyframes", 1)[0]
    assert "scale" not in pop
    assert "translateY" in pop
