"""Anti-generic look-ban checks over the Composition IR (P4.1)."""

from __future__ import annotations

from video_editor_core.composition import (
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
    MotionSpec,
)
from video_editor_core.look_bans import look_ban_findings
from video_editor_core.render_graph import Rect

CANVAS = Canvas(width=1080, height=1920, fps=30)


def _layer(
    layer_id: str,
    *,
    kind: str = "text",
    style: dict[str, object] | None = None,
    motions: list[MotionSpec] | None = None,
    rect: Rect | None = None,
    gradient: str = "",
) -> CompositionLayer:
    return CompositionLayer(
        id=layer_id,
        kind=kind,  # type: ignore[arg-type]
        rect=rect or Rect(x=140, y=140, width=800, height=160),
        time=LayerTime(start=0),
        content=LayerContent(text="x" if kind in {"text", "rich_text"} else "", gradient=gradient),
        style=style or {},
        motions=motions or [],
    )


def _composition(scenes: list[CompositionScene]) -> Composition:
    return Composition(
        job_id="look",
        canvas=CANVAS,
        duration=scenes[-1].end,
        scenes=scenes,
    )


def _scene(
    scene_id: str, start: float, end: float, layers: list[CompositionLayer]
) -> CompositionScene:
    return CompositionScene(id=scene_id, start=start, end=end, layers=layers)


def test_uniform_motion_is_flagged() -> None:
    layers = [_layer(f"t{index}", motions=[MotionSpec(name="rise")]) for index in range(4)]
    report = look_ban_findings(_composition([_scene("s0", 0.0, 2.0, layers)]))
    assert any("same motion" in finding for finding in report)


def test_one_font_weight_is_flagged() -> None:
    layers = [
        _layer(f"t{index}", style={"font_weight": 700, "font_size": 64}) for index in range(3)
    ]
    report = look_ban_findings(_composition([_scene("s0", 0.0, 2.0, layers)]))
    assert any("font weight" in finding for finding in report)


def test_centred_text_on_a_full_bleed_gradient_is_flagged() -> None:
    def scene(scene_id: str, start: float) -> CompositionScene:
        gradient = _layer(
            f"{scene_id}-bg",
            kind="shape",
            gradient="linear-gradient(black, transparent)",
            rect=Rect(x=0, y=0, width=1080, height=1920),
        )
        centred = _layer(
            f"{scene_id}-text",
            rect=Rect(x=340, y=860, width=400, height=200),
            motions=[MotionSpec(name="rise")],
        )
        return _scene(scene_id, start, start + 2.0, [gradient, centred])

    report = look_ban_findings(_composition([scene("s0", 0.0), scene("s1", 2.0)]))
    assert any("centre text on a full-bleed gradient" in finding for finding in report)


def test_uniform_scene_lengths_are_flagged() -> None:
    scenes = [
        _scene(f"s{index}", index * 2.0, (index + 1) * 2.0, [_layer(f"t{index}")])
        for index in range(3)
    ]
    report = look_ban_findings(_composition(scenes))
    assert any("same length" in finding for finding in report)


def test_a_2_5d_pack_without_depth_is_flagged() -> None:
    report = look_ban_findings(
        _composition([_scene("s0", 0.0, 2.0, [_layer("t0")])]), expect_depth=True
    )
    assert any("no depth/parallax" in finding for finding in report)


def test_a_varied_composition_is_clean() -> None:
    scenes = [
        _scene(
            "s0",
            0.0,
            1.2,
            [
                _layer("a", motions=[MotionSpec(name="rise")], style={"font_weight": 700}),
                _layer("b", motions=[MotionSpec(name="pop")], style={"font_weight": 400}),
            ],
        ),
        _scene(
            "s1",
            1.2,
            3.0,
            [_layer("c", motions=[MotionSpec(name="drift")], style={"font_weight": 900})],
        ),
        _scene(
            "s2",
            3.0,
            6.5,
            [_layer("d", motions=[MotionSpec(name="stamp")], style={"font_weight": 600})],
        ),
    ]
    assert look_ban_findings(_composition(scenes)) == ()


def test_strict_look_turns_a_finding_into_a_failed_check() -> None:
    from video_editor_pipelines.composition_qa import _look_ban_check

    composition = _composition(
        [
            _scene(
                "s0",
                0.0,
                2.0,
                [_layer(f"t{i}", motions=[MotionSpec(name="rise")]) for i in range(4)],
            )
        ]
    )
    advisory = _look_ban_check(composition, strict_look=False, expect_depth=False)
    strict = _look_ban_check(composition, strict_look=True, expect_depth=False)
    assert advisory.ok is True
    assert "same motion" in advisory.detail
    assert strict.ok is False


def test_the_end_screen_gate_flags_text_in_the_right_half() -> None:
    from video_editor_pipelines.composition_qa import _end_screen_check

    scene = _scene(
        "s0", 0.0, 30.0, [_layer("right", rect=Rect(x=600, y=100, width=300, height=100))]
    )
    check = _end_screen_check(_composition([scene]))
    assert check.ok is False
    assert "right" in check.detail


def test_the_end_screen_gate_ignores_backgrounds_and_left_text() -> None:
    from video_editor_pipelines.composition_qa import _end_screen_check

    scene = _scene(
        "s0",
        0.0,
        30.0,
        [
            _layer(
                "bg",
                kind="shape",
                rect=Rect(x=600, y=0, width=480, height=1920),
            ),
            _layer("left", rect=Rect(x=0, y=100, width=400, height=100)),
        ],
    )
    check = _end_screen_check(_composition([scene]))
    assert check.ok is True
