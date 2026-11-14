"""Tests for the tech tutorial + explainer motion templates and preset."""

from __future__ import annotations

import json
from pathlib import Path

from graphics.preset import PRESET_NAMES, load_brand, load_preset
from video_editor_core.templates import TEMPLATES_DIR, Template, load_yaml_templates

REPO_ROOT = Path(__file__).resolve().parents[1]


def _by_name(name: str) -> Template:
    templates = {t.name: t for t in load_yaml_templates(TEMPLATES_DIR)}
    template = templates.get(name)
    assert template is not None, f"template {name!r} not registered"
    return template


# ---------------------------------------------------------------------------
# tech_tutorial template
# ---------------------------------------------------------------------------


def test_tech_tutorial_registered() -> None:
    template = _by_name("tech_tutorial")
    assert template.pipeline_kind == "screen_to_video"
    assert template.default_preset == "tech_tutorial"
    assert template.default_aspect_ratio == "16:9"


def test_tech_tutorial_inputs() -> None:
    template = _by_name("tech_tutorial")
    required = [i.name for i in template.inputs if i.required]
    optional = [i.name for i in template.inputs if not i.required]
    assert "screen.mp4" in required
    assert "webcam.mp4" in optional
    assert "script.md" in optional


def test_tech_tutorial_scene_hints_include_screen_and_webcam() -> None:
    template = _by_name("tech_tutorial")
    hints = set(template.scene_type_hints)
    assert "screen_capture" in hints
    assert "webcam_popup" in hints
    assert "code_walkthrough" in hints


def test_tech_tutorial_caveats_mention_screen_skill() -> None:
    template = _by_name("tech_tutorial")
    assert any("screen tutorial" in c.lower() or "task 11" in c.lower() for c in template.caveats)


def test_tech_tutorial_resolves() -> None:
    template = _by_name("tech_tutorial")
    result = template.resolve()
    assert result.pipeline_kind == "screen_to_video"
    assert result.preset == "tech_tutorial"


# ---------------------------------------------------------------------------
# explainer_motion template
# ---------------------------------------------------------------------------


def test_explainer_motion_registered() -> None:
    template = _by_name("explainer_motion")
    assert template.pipeline_kind == "script_to_video"
    assert template.default_preset == "tech_tutorial"


def test_explainer_motion_inputs() -> None:
    template = _by_name("explainer_motion")
    required = [i.name for i in template.inputs if i.required]
    optional = [i.name for i in template.inputs if not i.required]
    assert "concept.md" in required
    assert "media/" in optional


def test_explainer_motion_scene_hints_are_real_scene_types() -> None:
    template = _by_name("explainer_motion")
    hints = set(template.scene_type_hints)
    assert hints <= {"opening", "narration", "broll", "closing"}
    # Motion graphics are a per-scene ``motion:`` prop documented in the
    # caveats, not a scene type — ``Scene.type`` rejects them.
    assert "motion_graphics" not in hints
    assert "code_highlight" not in hints
    assert any("motion: code_highlight" in caveat for caveat in template.caveats)


def test_explainer_motion_caveats_mention_motion_graphics_skill() -> None:
    template = _by_name("explainer_motion")
    assert any("motion_graphics" in c.lower() or "task 12" in c.lower() for c in template.caveats)


# ---------------------------------------------------------------------------
# tech_tutorial preset
# ---------------------------------------------------------------------------


def test_tech_tutorial_in_preset_names() -> None:
    assert "tech_tutorial" in PRESET_NAMES


def test_tech_tutorial_preset_loads() -> None:
    presets_dir = REPO_ROOT / "presets"
    brand = load_brand(presets_dir)
    preset = load_preset("tech_tutorial", presets_dir, brand=brand)
    assert preset.name == "tech_tutorial"
    assert preset.aspect_ratio == "16:9"
    assert preset.width == 1920
    assert preset.height == 1080


def test_tech_tutorial_layout_has_webcam_pip() -> None:
    presets_dir = REPO_ROOT / "presets"
    brand = load_brand(presets_dir)
    preset = load_preset("tech_tutorial", presets_dir, brand=brand)
    # Webcam PiP bubble should be in the bottom-right corner.
    assert "webcam_pip" in preset.layout
    pip = preset.layout["webcam_pip"]
    assert pip.x > 1000, "webcam_pip should be on the right side"
    assert pip.y > 500, "webcam_pip should be in the lower half"
    # Screen main should be full-frame.
    screen = preset.layout["screen_main"]
    assert screen.x == 0
    assert screen.y == 0
    assert screen.width == 1920
    assert screen.height == 1080


def test_tech_tutorial_preset_has_motion_graphics_config() -> None:
    preset_path = REPO_ROOT / "presets" / "tech_tutorial.json"
    payload = json.loads(preset_path.read_text(encoding="utf-8"))
    assert payload["motion_graphics"]["enabled"] is True
    assert "code_highlight_accent" in payload["motion_graphics"]
    assert "diagram_accent" in payload["motion_graphics"]


def test_tech_tutorial_preset_chapters_from_script() -> None:
    preset_path = REPO_ROOT / "presets" / "tech_tutorial.json"
    payload = json.loads(preset_path.read_text(encoding="utf-8"))
    assert payload["chapters"]["enabled"] is True
    assert payload["chapters"]["source"] == "script_headings"


# ---------------------------------------------------------------------------
# Dispatch integration
# ---------------------------------------------------------------------------


def test_tech_templates_in_yaml_template_names() -> None:
    from video_editor_core.templates.yaml_templates import yaml_template_names

    names = yaml_template_names()
    assert "tech_tutorial" in names
    assert "explainer_motion" in names
