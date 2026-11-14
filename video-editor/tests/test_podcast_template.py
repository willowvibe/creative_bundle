"""Tests for the podcast video template + preset."""

from __future__ import annotations

import json
from pathlib import Path

from graphics.preset import PRESET_NAMES, load_brand, load_preset
from video_editor_core.templates import TEMPLATES_DIR, Template, load_yaml_templates

REPO_ROOT = Path(__file__).resolve().parents[1]


def _podcast_template() -> Template:
    templates = [
        t for t in load_yaml_templates(TEMPLATES_DIR) if t.pipeline_kind == "podcast_to_video"
    ]
    assert templates, "no podcast_to_video template registered"
    return templates[0]


# ---------------------------------------------------------------------------
# Template manifest
# ---------------------------------------------------------------------------


def test_podcast_template_registered() -> None:
    template = _podcast_template()
    assert template.name == "podcast_video"


def test_podcast_template_resolves() -> None:
    template = _podcast_template()
    result = template.resolve()
    assert result.pipeline_kind == "podcast_to_video"
    assert result.preset == "podcast_studio"
    assert result.aspect_ratio == "16:9"


def test_podcast_template_inputs() -> None:
    template = _podcast_template()
    required = [i.name for i in template.inputs if i.required]
    optional = [i.name for i in template.inputs if not i.required]
    assert "host_track.mp4" in required
    # guest_track and transcript should be optional
    assert "guest_track.mp4" in optional
    assert "transcript.json" in optional


def test_podcast_template_scene_type_hints() -> None:
    template = _podcast_template()
    hints = set(template.scene_type_hints)
    assert {"host_close", "guest_close", "split_screen", "chapter_break"} <= hints


def test_podcast_template_has_caveats() -> None:
    template = _podcast_template()
    assert template.caveats
    # At least one caveat should mention chapters (Task 13 integration).
    assert any("chapter" in c.lower() for c in template.caveats)


# ---------------------------------------------------------------------------
# Preset
# ---------------------------------------------------------------------------


def test_podcast_studio_in_preset_names() -> None:
    assert "podcast_studio" in PRESET_NAMES


def test_podcast_studio_preset_loads() -> None:
    presets_dir = REPO_ROOT / "presets"
    brand = load_brand(presets_dir)
    preset = load_preset("podcast_studio", presets_dir, brand=brand)
    assert preset.name == "podcast_studio"
    assert preset.aspect_ratio == "16:9"
    assert preset.width == 1920
    assert preset.height == 1080


def test_podcast_studio_layout_has_split_screen() -> None:
    presets_dir = REPO_ROOT / "presets"
    brand = load_brand(presets_dir)
    preset = load_preset("podcast_studio", presets_dir, brand=brand)
    # Both host_main and guest_main should be present for split-screen.
    assert "host_main" in preset.layout
    assert "guest_main" in preset.layout
    # The host_main rect should occupy the left half of the frame.
    host_rect = preset.layout["host_main"]
    assert host_rect.x == 0
    assert host_rect.width == 960
    guest_rect = preset.layout["guest_main"]
    assert guest_rect.x == 960
    assert guest_rect.width == 960


def test_podcast_studio_audio_defaults() -> None:
    """Verify the preset JSON has audio mixing defaults documented."""
    preset_path = REPO_ROOT / "presets" / "podcast_studio.json"
    payload = json.loads(preset_path.read_text(encoding="utf-8"))
    assert "audio" in payload
    audio = payload["audio"]
    assert audio["target_lufs"] == -16
    assert audio["duck_music_under_speech"] is True
    assert audio["duck_amount_db"] == 12


def test_podcast_studio_chapters_config() -> None:
    """Verify the preset enables chapters + YouTube format."""
    preset_path = REPO_ROOT / "presets" / "podcast_studio.json"
    payload = json.loads(preset_path.read_text(encoding="utf-8"))
    assert payload["chapters"]["enabled"] is True
    assert payload["chapters"]["youtube_format"] is True
    assert payload["chapters"]["max_chapters"] >= 1


# ---------------------------------------------------------------------------
# Dispatch integration
# ---------------------------------------------------------------------------


def test_podcast_template_in_yaml_template_names() -> None:
    """The greedy loader used by the CLI dispatcher should see podcast_video."""
    from video_editor_core.templates.yaml_templates import yaml_template_names

    names = yaml_template_names()
    assert "podcast_video" in names


def test_podcast_template_uses_podcast_to_video_kind() -> None:
    """Sanity: ensure the template's pipeline_kind is the discriminator the
    CLI dispatch in ``main.py`` switches on."""
    template = _podcast_template()
    assert template.pipeline_kind == "podcast_to_video"
