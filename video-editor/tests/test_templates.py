"""Tests for the template layer (base, eleven_templates, router)."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_core.templates import (
    TEMPLATE_NAMES,
    TEMPLATES,
    TEMPLATES_DIR,
    TemplateResult,
    TemplateRouter,
    load_yaml_templates,
    yaml_template_map,
    yaml_template_names,
)
from video_editor_core.templates.eleven_templates import TEMPLATE_MAP
from video_editor_core.templates.router import TemplateSelectionError


def test_template_names_match_eleven_templates() -> None:
    """TEMPLATE_NAMES and TEMPLATES stay in sync."""
    assert len(TEMPLATES) == 14
    assert [t.name for t in TEMPLATES] == TEMPLATE_NAMES
    assert sorted(TEMPLATE_NAMES) == sorted(TEMPLATE_MAP.keys())


def test_all_templates_declare_required_inputs() -> None:
    """Every template has at least one required input."""
    for t in TEMPLATES:
        required = [i for i in t.inputs if i.required]
        assert required, f"template {t.name!r} has no required inputs"


def test_all_templates_have_caveats() -> None:
    """Caveats are non-empty so docs can warn users."""
    for t in TEMPLATES:
        assert t.caveats, f"template {t.name!r} has no caveats"


def test_router_lists_eleven_templates() -> None:
    """Router exposes every YAML template name."""
    router = TemplateRouter()
    assert len(router.template_names) == len(yaml_template_names(TEMPLATES_DIR))
    assert sorted(router.template_names) == sorted(yaml_template_names(TEMPLATES_DIR))


def test_router_select_explicit_template(tmp_path: Path) -> None:
    """Explicit --template wins over any source inference."""
    router = TemplateRouter()
    result = router.select(source=tmp_path, template_name="social_hook")
    assert result.template_name == "social_hook"
    assert result.pipeline_kind == "script_to_video"
    assert result.preset == "short_explainer"
    assert result.aspect_ratio == "9:16"
    assert result.providers["image"] == "fal_ai"
    assert "template selected explicitly" in result.notes


def test_router_select_unknown_template_raises() -> None:
    """Unknown template name gives a helpful error."""
    router = TemplateRouter()
    with pytest.raises(TemplateSelectionError, match="Unknown template 'not_real'."):
        router.select(template_name="not_real")


def test_router_select_without_source_or_template_raises() -> None:
    """At least a source or a template name is required."""
    router = TemplateRouter()
    with pytest.raises(TemplateSelectionError, match="No source or template provided"):
        router.select()


def test_router_infer_video_to_podium_cut(tmp_path: Path) -> None:
    """A single video file maps to podium_cut."""
    router = TemplateRouter()
    source = tmp_path / "talk.mp4"
    source.write_bytes(b"")
    result = router.select(source=source)
    assert result.template_name == "podium_cut"
    assert result.pipeline_kind == "v2_skills"
    assert result.aspect_ratio == "16:9"


@pytest.mark.parametrize("ext", [".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"])
def test_router_infer_audio_to_voice_canvas(tmp_path: Path, ext: str) -> None:
    """Audio files map to voice_canvas."""
    router = TemplateRouter()
    source = tmp_path / f"vo{ext}"
    source.write_bytes(b"")
    result = router.select(source=source)
    assert result.template_name == "voice_canvas"
    assert result.pipeline_kind == "script_to_video"


@pytest.mark.parametrize("ext", [".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"])
def test_router_infer_audio_with_media_dir_to_musical_video(tmp_path: Path, ext: str) -> None:
    """Audio files with a media directory map to musical_video."""
    router = TemplateRouter()
    media_dir = tmp_path / "visuals"
    media_dir.mkdir()
    source = tmp_path / f"song{ext}"
    source.write_bytes(b"")
    result = router.select(source=source, overrides={"media_dir": str(media_dir)})
    assert result.template_name == "musical_video"
    assert result.pipeline_kind == "music_video"


@pytest.mark.parametrize("ext", [".md", ".txt", ".markdown"])
def test_router_infer_text_to_explainer_reel(tmp_path: Path, ext: str) -> None:
    """Markdown/text files default to explainer_reel."""
    router = TemplateRouter()
    source = tmp_path / f"script{ext}"
    source.write_text("# Hook\n\nHello world.", encoding="utf-8")
    result = router.select(source=source)
    assert result.template_name == "explainer_reel"
    if ext == ".md":
        assert result.concept == source.read_text(encoding="utf-8")


def test_router_infer_directory_to_raw_batch(tmp_path: Path) -> None:
    """Directories map to the v1 raw_batch template."""
    router = TemplateRouter()
    result = router.select(source=tmp_path)
    assert result.template_name == "raw_batch"
    assert result.pipeline_kind == "v1_raw"


def test_router_unknown_extension_raises(tmp_path: Path) -> None:
    """Unsupported file extensions ask the user to use --template."""
    router = TemplateRouter()
    source = tmp_path / "data.csv"
    source.write_bytes(b"")
    with pytest.raises(TemplateSelectionError, match="Cannot infer a template"):
        router.select(source=source)


def test_router_missing_source_raises(tmp_path: Path) -> None:
    """Non-existent source path is rejected."""
    router = TemplateRouter()
    missing = tmp_path / "does_not_exist.mp4"
    with pytest.raises(TemplateSelectionError, match="Source path does not exist"):
        router.select(source=missing)


def test_router_apply_overrides() -> None:
    """Preset/aspect/providers/music_mood overrides pass through."""
    router = TemplateRouter()
    result = router.select(
        template_name="explainer_reel",
        overrides={
            "preset": "short_explainer",
            "aspect_ratio": "9:16",
            "providers": {"image": "fal_ai", "tts": "openai_tts"},
            "music_mood": "calm",
        },
    )
    assert result.preset == "short_explainer"
    assert result.aspect_ratio == "9:16"
    assert result.providers["tts"] == "openai_tts"
    assert result.music_mood == "calm"


def test_router_list_templates_schema() -> None:
    """list_templates returns a stable shape."""
    router = TemplateRouter()
    summary = router.list_templates()
    assert len(summary) == len(yaml_template_names(TEMPLATES_DIR))
    for entry in summary:
        assert set(entry.keys()) == {
            "name",
            "description",
            "pipeline",
            "preset",
            "aspect_ratio",
            "required_inputs",
            "optional_inputs",
        }


def test_template_resolve_returns_template_result() -> None:
    """Template.resolve builds a TemplateResult with defaults."""
    tmpl = TEMPLATE_MAP["product_spot"]
    result = tmpl.resolve()
    assert isinstance(result, TemplateResult)
    assert result.template_name == "product_spot"
    assert result.preset == "short_explainer"
    assert result.aspect_ratio == "9:16"
    assert result.providers["image"] == "fal_ai"
    assert result.providers["video"] == "hyperframes_video"


def test_image_story_build_concept() -> None:
    """image_story returns a structured starter concept."""
    tmpl = TEMPLATE_MAP["image_story"]
    assert tmpl.build_concept is not None
    path = Path("/tmp/dummy.md")
    concept = tmpl.build_concept(path)
    assert "Image Story" in concept
    assert "Scene 1" in concept


def test_voice_canvas_build_concept() -> None:
    """voice_canvas returns a script scaffold."""
    tmpl = TEMPLATE_MAP["voice_canvas"]
    assert tmpl.build_concept is not None
    concept = tmpl.build_concept(Path("/tmp/vo.md"))
    assert "Voice Canvas" in concept
    assert "Narration:" in concept


def test_input_spec_example_present() -> None:
    """Every input spec carries an example for CLI help generation."""
    for t in TEMPLATES:
        for inp in t.inputs:
            assert inp.example, f"{t.name}.{inp.name} missing example"


# ---------------------------------------------------------------------------
# YAML manifest loader tests
# ---------------------------------------------------------------------------


def test_yaml_templates_load_all_manifests() -> None:
    """Every non-underscore YAML file under templates/ becomes a Template."""
    yaml_templates = load_yaml_templates(TEMPLATES_DIR)
    expected_names = yaml_template_names(TEMPLATES_DIR)
    assert len(yaml_templates) == len(expected_names)
    names = {t.name for t in yaml_templates}
    assert names == set(expected_names)
    assert names >= set(TEMPLATE_NAMES)


def test_yaml_template_names_and_map() -> None:
    """Helpers return the expected names and mapping."""
    expected = yaml_template_names(TEMPLATES_DIR)
    names = yaml_template_names(TEMPLATES_DIR)
    mapping = yaml_template_map(TEMPLATES_DIR)
    assert sorted(names) == sorted(expected)
    assert set(mapping.keys()) == set(expected)
    assert set(expected) >= set(TEMPLATE_NAMES)
    for t in mapping.values():
        assert t.description
        assert t.inputs


def test_yaml_templates_match_builtin_metadata() -> None:
    """YAML manifests carry the same core metadata as the built-ins."""
    builtin_map = {t.name: t for t in TEMPLATES}
    yaml_map = yaml_template_map(TEMPLATES_DIR)
    for name, builtin in builtin_map.items():
        yaml_tmpl = yaml_map[name]
        assert yaml_tmpl.pipeline_kind == builtin.pipeline_kind
        assert yaml_tmpl.default_preset == builtin.default_preset
        assert yaml_tmpl.default_aspect_ratio == builtin.default_aspect_ratio
        assert yaml_tmpl.default_providers == builtin.default_providers
        assert [i.name for i in yaml_tmpl.inputs] == [i.name for i in builtin.inputs]


def test_yaml_template_validation_rejects_missing_keys(tmp_path: Path) -> None:
    """Manifests missing required fields raise a clear ValueError."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: broken\ndescription: no inputs or pipeline\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing keys"):
        load_yaml_templates(tmp_path)


def test_yaml_template_validation_rejects_bad_pipeline_kind(tmp_path: Path) -> None:
    """Unknown pipeline_kind is rejected."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: broken\ndescription: x\ninputs: []\npipeline_kind: not_real\ndefault_preset: p\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid pipeline_kind"):
        load_yaml_templates(tmp_path)


def test_yaml_templates_dir_missing_returns_empty(tmp_path: Path) -> None:
    """A missing templates directory returns an empty list without crashing."""
    missing = tmp_path / "no_templates"
    assert load_yaml_templates(missing) == []
    assert yaml_template_names(missing) == []
    assert yaml_template_map(missing) == {}


def test_template_router_defaults_to_yaml() -> None:
    """The default router loads templates from the YAML manifests."""
    router = TemplateRouter()
    assert sorted(router.template_names) == sorted(yaml_template_names(TEMPLATES_DIR))
    # Pick one and verify it resolves the same way as the built-in.
    result = router.select(template_name="product_spot")
    assert result.preset == "short_explainer"
    assert result.aspect_ratio == "9:16"
    assert result.providers["image"] == "fal_ai"
