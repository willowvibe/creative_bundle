"""Tests for the vlogger templates and presets."""

from __future__ import annotations

import json
from pathlib import Path

from graphics.models import Preset
from graphics.preset import PRESET_NAMES, load_brand, load_preset
from video_editor_core.templates import TEMPLATES_DIR, load_yaml_templates

REPO_ROOT = Path(__file__).resolve().parents[1]


def _vlog_templates() -> list:
    return [t for t in load_yaml_templates(TEMPLATES_DIR) if t.pipeline_kind == "raw_to_video"]


def test_vlog_templates_registered() -> None:
    names = {t.name for t in _vlog_templates()}
    assert {"vlog_reel", "talking_head_polish"} <= names


def test_vlog_templates_pipeline_kind() -> None:
    for template in _vlog_templates():
        assert template.pipeline_kind == "raw_to_video"
        assert template.default_aspect_ratio in {"16:9", "9:16"}
        assert template.caveats


def test_vlog_reel_requires_directory_input() -> None:
    vlog = next(t for t in _vlog_templates() if t.name == "vlog_reel")
    required = [i.name for i in vlog.inputs if i.required]
    assert "clips/" in required


def test_vlog_templates_resolve_scene_type_hints() -> None:
    vlog = next(t for t in _vlog_templates() if t.name == "vlog_reel")
    assert "broll" in vlog.scene_type_hints
    assert "talking_head" in vlog.scene_type_hints
    result = vlog.resolve()
    assert result.pipeline_kind == "raw_to_video"
    assert result.preset == "vlogger_youtube"


def test_all_vlog_presets_load_with_brand() -> None:
    presets_dir = REPO_ROOT / "presets"
    brand = load_brand(presets_dir)
    for name in ("vlogger_youtube", "vlogger_shorts", "cooking_show", "event_recap"):
        assert name in PRESET_NAMES, f"preset {name!r} not registered"
        preset = load_preset(name, presets_dir, brand=brand)
        assert isinstance(preset, Preset)
        assert preset.width > 0 and preset.height > 0
        assert preset.captions and preset.branding


def test_vlogger_youtube_is_16_9() -> None:
    presets_dir = REPO_ROOT / "presets"
    preset = load_preset("vlogger_youtube", presets_dir)
    assert preset.aspect_ratio == "16:9"
    assert (preset.width, preset.height) == (1920, 1080)


def test_vlogger_shorts_is_9_16() -> None:
    presets_dir = REPO_ROOT / "presets"
    preset = load_preset("vlogger_shorts", presets_dir)
    assert preset.aspect_ratio == "9:16"
    assert (preset.width, preset.height) == (1080, 1920)


def test_preset_files_are_valid_json() -> None:
    presets_dir = REPO_ROOT / "presets"
    for name in ("vlogger_youtube", "vlogger_shorts", "cooking_show", "event_recap"):
        data = json.loads((presets_dir / f"{name}.json").read_text(encoding="utf-8"))
        assert data["name"] == name
        assert {"aspect_ratio", "width", "height", "layout", "captions", "branding"} <= set(
            data.keys()
        )


def test_talking_head_polish_inputs() -> None:
    polish = next(t for t in _vlog_templates() if t.name == "talking_head_polish")
    assert polish.inputs[0].name == "take.mp4"
