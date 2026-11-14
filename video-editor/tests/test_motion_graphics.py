"""Tests for the motion graphics skill."""

from __future__ import annotations

from pathlib import Path

import motion_graphics.motion_graphics as mg_module
import pytest
from motion_graphics.motion_graphics import (
    CalloutSpec,
    CodeHighlightSpec,
    DiagramSpec,
    MotionGraphicsError,
    MotionGraphicsSkill,
    _fallback_filters,
    _plan_to_html,
)
from video_editor_core.hyperframes_cli import HyperFramesLintError, RenderSegmentError
from video_editor_state.job import JobContext

# A CLI name that is never on PATH, so render tests stay on the offline fallback.
_MISSING_CLI = "hyperframes-not-installed-for-tests"


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="mg-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


# ---------------------------------------------------------------------------
# Plan builders
# ---------------------------------------------------------------------------


def test_build_code_highlight_plan_basic(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx)
    plan = skill.build_code_highlight_plan(
        CodeHighlightSpec(
            code="for i in range(3):\n    print(i)",
            highlight_lines=[1],
            duration=4.0,
        )
    )
    assert plan["template"] == "code_highlight"
    assert plan["width"] == 1920
    assert plan["height"] == 1080
    assert plan["duration"] == 4.0
    layer_names = {layer["name"] for layer in plan["layers"]}
    assert "background" in layer_names
    assert "title" not in layer_names  # no title set
    assert "highlight_0" in layer_names  # first line is highlighted
    assert "line_0" in layer_names
    assert "line_1" in layer_names


def test_build_code_highlight_plan_with_title(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx)
    plan = skill.build_code_highlight_plan(
        CodeHighlightSpec(code="x = 1", title="Setup", duration=2.0)
    )
    layer_names = {layer["name"] for layer in plan["layers"]}
    assert "title" in layer_names


def test_build_diagram_plan_basic(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx)
    plan = skill.build_diagram_plan(
        DiagramSpec(
            nodes=[
                {"id": "a", "label": "Source", "x": 100, "y": 200, "width": 160, "height": 80},
                {"id": "b", "label": "Output", "x": 400, "y": 200, "width": 160, "height": 80},
            ],
            edges=[{"from": "a", "to": "b"}],
            title="Flow",
            duration=3.0,
        )
    )
    assert plan["template"] == "diagram"
    layer_names = {layer["name"] for layer in plan["layers"]}
    assert {"node_a", "node_b", "label_a", "label_b", "edge_a_b"} <= layer_names
    assert "title" in layer_names


def test_build_diagram_plan_skips_unknown_edge(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx)
    plan = skill.build_diagram_plan(
        DiagramSpec(
            nodes=[{"id": "a", "label": "A"}],
            edges=[{"from": "a", "to": "ghost"}],  # ghost not declared
        )
    )
    layer_names = {layer["name"] for layer in plan["layers"]}
    assert "node_a" in layer_names
    assert "edge_a_ghost" not in layer_names


def test_build_callout_plan(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx)
    plan = skill.build_callout_plan(
        CalloutSpec(name="Dr. Smith", role="Lead Engineer", duration=2.5)
    )
    assert plan["template"] == "callout"
    layer_names = {layer["name"] for layer in plan["layers"]}
    assert {"bar", "name", "role"} <= layer_names


def test_build_callout_plan_skips_empty_role(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx)
    plan = skill.build_callout_plan(CalloutSpec(name="Host"))  # no role
    layer_names = {layer["name"] for layer in plan["layers"]}
    assert "name" in layer_names
    assert "role" not in layer_names


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


def test_lint_clean_plan_passes(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx)
    plan = skill.build_callout_plan(CalloutSpec(name="Host"))
    assert skill.lint(plan) == []


def test_lint_detects_missing_required_fields() -> None:
    skill = MotionGraphicsSkill.__new__(MotionGraphicsSkill)  # bypass __init__ for unit test
    plan = {"template": "x"}  # missing width/height/fps/duration/layers
    problems = skill.lint(plan)
    assert any("width" in p for p in problems)
    assert any("height" in p for p in problems)
    assert any("fps" in p for p in problems)
    assert any("duration" in p for p in problems)


# ---------------------------------------------------------------------------
# Render fallback (no HyperFrames in tests)
# ---------------------------------------------------------------------------


def test_render_uses_ffmpeg_fallback(ctx: JobContext) -> None:
    """The fallback path should produce a file with a non-zero duration."""
    skill = MotionGraphicsSkill(ctx, cli_path=_MISSING_CLI)
    plan = skill.build_callout_plan(CalloutSpec(name="Host", role="Anchor", duration=1.0))
    output = skill.render(plan, output_path=ctx.work_dir / "callout.mp4")
    assert output.exists()
    assert output.stat().st_size > 0


def test_render_rejects_lint_failures(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx)
    bad_plan = skill.build_code_highlight_plan(
        CodeHighlightSpec(code="x = 1", width=-100, height=1080, fps=30, duration=1.0)
    )
    # Lint flags negative width.
    with pytest.raises(HyperFramesLintError):
        skill.render(bad_plan, output_path=ctx.work_dir / "bad.mp4")


# ---------------------------------------------------------------------------
# Skill protocol entrypoint
# ---------------------------------------------------------------------------


def test_skill_name(ctx: JobContext) -> None:
    assert MotionGraphicsSkill(ctx).name == "motion_graphics"


def test_run_batch_mixed_kinds(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx, cli_path=_MISSING_CLI)
    items = [
        {"kind": "code_highlight", "spec": CodeHighlightSpec(code="x = 1", duration=1.0)},
        {"kind": "diagram", "spec": DiagramSpec(nodes=[{"id": "a", "label": "A"}])},
        {"kind": "callout", "spec": CalloutSpec(name="Host")},
    ]
    result = skill.run(items)
    assert result["count"] == 3
    assert len(result["items"]) == 3
    manifest_path = Path(result["manifest"])
    assert manifest_path.exists()
    import json as _json

    payload = _json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["count"] == 3
    assert {item["kind"] for item in payload["items"]} == {"code_highlight", "diagram", "callout"}


def test_run_batch_rejects_unknown_kind(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx)
    with pytest.raises(MotionGraphicsError):
        skill.run([{"kind": "bogus", "spec": CalloutSpec(name="x")}])


def test_run_batch_skip_render(ctx: JobContext) -> None:
    """``render_outputs=False`` should not invoke ffmpeg and produce no MP4s."""
    skill = MotionGraphicsSkill(ctx)
    items = [{"kind": "callout", "spec": CalloutSpec(name="Host")}]
    result = skill.run(items, render_outputs=False)
    assert result["count"] == 1
    assert result["items"][0]["renderer"] == "none"
    assert result["items"][0]["output"] is None


# ---------------------------------------------------------------------------
# Fallback robustness
# ---------------------------------------------------------------------------


def test_fallback_handles_quotes_and_filter_metacharacters(ctx: JobContext) -> None:
    """Apostrophes, colons, percents and commas must not break drawtext."""
    skill = MotionGraphicsSkill(ctx, cli_path=_MISSING_CLI)
    plan = skill.build_diagram_plan(
        DiagramSpec(
            title="The Sun's share: 99.8% of mass, roughly",
            nodes=[{"id": "a", "label": "Earth's year = 365 days"}],
            duration=1.0,
        )
    )
    output = skill.render(plan, output_path=ctx.work_dir / "quotes.mp4")
    assert output.exists()
    assert output.stat().st_size > 0


def test_fallback_draws_rect_layers(ctx: JobContext, tmp_path: Path) -> None:
    """Node boxes and callout bars are drawn, not silently dropped."""
    skill = MotionGraphicsSkill(ctx, cli_path=_MISSING_CLI)
    plan = skill.build_callout_plan(CalloutSpec(name="Host", role="Anchor"))
    filters = _fallback_filters(plan, tmp_path)
    assert any(f.startswith("drawbox=") for f in filters)
    assert any(f.startswith("drawtext=") for f in filters)


# ---------------------------------------------------------------------------
# HyperFrames HTML project path
# ---------------------------------------------------------------------------


def test_plan_to_html_follows_composition_contract(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx)
    plan = skill.build_diagram_plan(
        DiagramSpec(
            title="Orbits",
            nodes=[
                {
                    "id": "sun",
                    "label": "Sun",
                    "x": 860,
                    "y": 440,
                    "width": 200,
                    "height": 200,
                    "shape": "circle",
                },
                {"id": "b", "label": "Out", "x": 1400, "y": 200},
            ],
            edges=[{"from": "sun", "to": "b"}],
            orbits=[{"cx": 960, "cy": 540, "radius": 300, "label": "Earth", "period": 6.0}],
            duration=3.0,
        )
    )
    assert skill.lint(plan) == []
    doc = _plan_to_html(plan)
    assert 'data-composition-id="main"' in doc
    assert 'data-duration="3.000"' in doc
    assert doc.count('class="clip"') == len(plan["layers"])
    assert "<line" in doc  # the edge is drawn as an SVG line
    assert "border-radius:50%" in doc  # circle node and orbit ring
    assert "@keyframes mg_spin" in doc
    assert "Earth" in doc


def test_render_uses_hyperframes_project_when_available(
    ctx: JobContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def fake_render_project(project_dir: Path, output_path: Path, **kwargs: object) -> Path:
        calls.append(Path(project_dir))
        assert (Path(project_dir) / "index.html").exists()
        assert (Path(project_dir) / "assets" / "fonts" / "Inter.ttf").exists()
        assert "@font-face" in (Path(project_dir) / "index.html").read_text(encoding="utf-8")
        Path(output_path).write_bytes(b"fake")
        return Path(output_path)

    monkeypatch.setattr(mg_module, "hyperframes_available", lambda cli_path="": True)
    monkeypatch.setattr(mg_module, "render_project", fake_render_project)
    skill = MotionGraphicsSkill(ctx)
    result = skill.run([{"kind": "callout", "spec": CalloutSpec(name="Host")}])
    assert len(calls) == 1
    assert result["items"][0]["renderer"] == "hyperframes"


def test_run_reports_fallback_when_hyperframes_fails(
    ctx: JobContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_render_project(*args: object, **kwargs: object) -> Path:
        raise RenderSegmentError("boom")

    monkeypatch.setattr(mg_module, "hyperframes_available", lambda cli_path="": True)
    monkeypatch.setattr(mg_module, "render_project", failing_render_project)
    skill = MotionGraphicsSkill(ctx)
    result = skill.run([{"kind": "callout", "spec": CalloutSpec(name="Host", duration=1.0)}])
    item = result["items"][0]
    assert item["renderer"] == "ffmpeg-fallback"
    assert Path(item["output"]).stat().st_size > 0


def test_unlabeled_node_gets_no_text_layer(ctx: JobContext) -> None:
    skill = MotionGraphicsSkill(ctx)
    plan = skill.build_diagram_plan(
        DiagramSpec(nodes=[{"id": "sun", "label": "", "shape": "circle"}])
    )
    names = {layer["name"] for layer in plan["layers"]}
    assert "node_sun" in names
    assert "label_sun" not in names
    assert skill.lint(plan) == []


def test_diagram_edges_render_beneath_nodes(ctx: JobContext) -> None:
    """Edges join node centres, so they must paint before (under) the nodes."""
    skill = MotionGraphicsSkill(ctx)
    plan = skill.build_diagram_plan(
        DiagramSpec(
            nodes=[{"id": "a", "label": "A"}, {"id": "b", "label": "B", "x": 600}],
            edges=[{"from": "a", "to": "b"}],
        )
    )
    names = [layer["name"] for layer in plan["layers"]]
    assert names.index("edge_a_b") < names.index("node_a")


def test_html_root_opts_out_of_timeline_poll(ctx: JobContext) -> None:
    plan = MotionGraphicsSkill(ctx).build_callout_plan(CalloutSpec(name="Host"))
    assert "data-no-timeline" in _plan_to_html(plan)
