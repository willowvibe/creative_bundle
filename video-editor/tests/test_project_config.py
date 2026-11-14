"""Regression tests for the unified project-level configuration loader.

Phase 1 of the config-driven defaults work introduced ``video_editor_config``.
These tests verify that ``ProjectConfig`` loads the new ``audio:``, ``render:``,
``paths:`` sections, resolves repo-relative paths, and that ``SkillsPipeline``
and ``AssetIngestionManager`` consume those defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_config import ProjectConfig
from video_editor_pipelines.pipeline import SkillsPipeline
from video_editor_state.job import JobContext


def _write_config(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture
def full_config(tmp_path: Path) -> Path:
    return _write_config(
        tmp_path,
        {
            "llm": {
                "default_provider": "test_provider",
                "providers": {
                    "test_provider": {
                        "api_key_env": "TEST_API_KEY",
                        "default_model": "test-model",
                    }
                },
                "tasks": {
                    "fallback": {
                        "task_type": "fallback",
                        "provider": "test_provider",
                        "model": "test-model",
                    }
                },
            },
            "audio": {
                "loudness_target_lufs": -14.0,
                "default_music_path": str(tmp_path / "assets" / "music" / "custom.mp3"),
            },
            "render": {
                "cache_dir": "projects/{job_id}/.cache",
                "output_name_template": "{stage}_deliverable.mp4",
                "width": 1280,
                "height": 720,
            },
            "paths": {
                "presets": str(tmp_path / "custom_presets"),
                "music": "shared/music",
            },
            "assets": {
                "providers": {},
                "defaults": {},
            },
        },
    )


def test_project_config_loads_sections(full_config: Path, tmp_path: Path) -> None:
    """All typed sections are parsed from the YAML without dropping fields."""
    cfg = ProjectConfig.load(full_config)

    assert cfg.llm.default_provider == "test_provider"
    assert cfg.audio.loudness_target_lufs == -14.0
    assert cfg.audio.default_music_path == str(tmp_path / "assets" / "music" / "custom.mp3")
    assert cfg.render.cache_dir == "projects/{job_id}/.cache"
    assert cfg.render.output_name_template == "{stage}_deliverable.mp4"
    assert cfg.render.width == 1280
    assert cfg.paths.presets == str(tmp_path / "custom_presets")
    assert cfg.paths.music == "shared/music"


def test_project_config_presets_dir_resolution(full_config: Path, tmp_path: Path) -> None:
    """``presets_dir`` returns an absolute path, expanding repo-relative values."""
    cfg = ProjectConfig.load(full_config)
    presets = cfg.presets_dir(repo_root=tmp_path)
    assert presets == (tmp_path / "custom_presets").resolve()


def test_project_config_presets_dir_relative_expansion(tmp_path: Path) -> None:
    """A repo-relative ``paths.presets`` value is joined against the repo root."""
    config = _write_config(tmp_path, {"paths": {"presets": "rel/presets"}})
    cfg = ProjectConfig.load(config)
    assert cfg.presets_dir(repo_root=tmp_path) == (tmp_path / "rel" / "presets").resolve()


def test_project_config_default_music_path(full_config: Path, tmp_path: Path) -> None:
    """``default_music_path`` prefers ``audio.default_music_path`` and expands it."""
    cfg = ProjectConfig.load(full_config)
    music = cfg.default_music_path(repo_root=tmp_path)
    assert music == (tmp_path / "assets" / "music" / "custom.mp3").resolve()


def test_project_config_default_music_path_falls_back_to_paths_music(
    tmp_path: Path,
) -> None:
    """When ``audio.default_music_path`` is empty, ``paths.music`` is used."""
    config = _write_config(
        tmp_path,
        {
            "audio": {"default_music_path": ""},
            "paths": {"music": "shared/tracks"},
        },
    )
    cfg = ProjectConfig.load(config)
    music = cfg.default_music_path(repo_root=tmp_path)
    assert music == (tmp_path / "shared" / "tracks").resolve()


def test_project_config_resolve_path_contains_absolute_to_repo_root(
    tmp_path: Path,
) -> None:
    """Absolute paths outside repo_root fall back to the repo-relative default."""
    cfg = ProjectConfig.load()
    # Use a path that is genuinely outside the provided repo_root.
    outside = Path("/etc") / "outside-repo"
    resolved = cfg.resolve_path(
        str(outside),
        default_relative_to_repo="presets",
        repo_root=tmp_path,
    )
    assert resolved == (tmp_path / "presets").resolve()


def test_project_config_resolve_path_contains_relative_traversal(
    tmp_path: Path,
) -> None:
    """Relative paths with ``..`` that escape the repo root are contained."""
    cfg = ProjectConfig.load()
    resolved = cfg.resolve_path(
        "../etc/passwd",
        default_relative_to_repo="presets",
        repo_root=tmp_path,
    )
    assert resolved == (tmp_path / "presets").resolve()


def test_project_config_resolve_path_absolute_inside_repo_root(tmp_path: Path) -> None:
    """An absolute path inside the repo root is accepted."""
    cfg = ProjectConfig.load()
    inside = tmp_path / "assets" / "music"
    inside.mkdir(parents=True)
    resolved = cfg.resolve_path(str(inside), repo_root=tmp_path)
    assert resolved == inside.resolve()


def test_project_config_resolve_path_empty_returns_none() -> None:
    """Empty value with no default returns ``None``."""
    cfg = ProjectConfig.load()
    assert cfg.resolve_path("") is None
    assert cfg.resolve_path(None, default_relative_to_repo=None) is None


def test_project_config_load_missing_path_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Loading a non-existent explicit config path warns before falling back."""
    missing = tmp_path / "does_not_exist.yaml"
    cfg = ProjectConfig.load(missing)
    assert cfg.render.output_name_template == "{stage}.mp4"
    assert "config file not found" in caplog.text


def test_project_config_invalid_section_uses_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed section is logged and falls back to defaults."""
    config = _write_config(tmp_path, {"render": "not-a-mapping"})
    cfg = ProjectConfig.load(config)
    assert cfg.render.width == 1920


def test_project_config_loads_top_level_llm_layout(tmp_path: Path) -> None:
    """Historical top-level LLM keys are still accepted."""
    config = _write_config(
        tmp_path,
        {
            "default_provider": "legacy",
            "providers": {"legacy": {"api_key_env": "LEGACY_KEY", "default_model": "legacy-model"}},
            "tasks": {},
        },
    )
    cfg = ProjectConfig.load(config)
    assert cfg.llm.default_provider == "legacy"


def test_skills_pipeline_uses_config_presets_dir(tmp_path: Path, full_config: Path) -> None:
    """``SkillsPipeline`` resolves the presets directory from ``paths.presets``."""
    ctx = JobContext(
        job_id="config-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    pipeline = SkillsPipeline(ctx, config_path=full_config)
    assert pipeline.presets_dir == (tmp_path / "custom_presets").resolve()


def test_skills_pipeline_uses_config_default_music_path(tmp_path: Path, full_config: Path) -> None:
    """``SkillsPipeline`` falls back to the configured default music file."""
    ctx = JobContext(
        job_id="config-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    music_file = tmp_path / "assets" / "music" / "custom.mp3"
    music_file.parent.mkdir(parents=True)
    music_file.write_bytes(b"fake-music")

    pipeline = SkillsPipeline(ctx, config_path=full_config)
    assert pipeline.music_path == music_file.resolve()


def test_skills_pipeline_explicit_args_win_over_config(tmp_path: Path, full_config: Path) -> None:
    """CLI-level explicit ``presets_dir`` and ``music_path`` take precedence."""
    ctx = JobContext(
        job_id="config-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    explicit_presets = tmp_path / "explicit_presets"
    explicit_presets.mkdir()
    explicit_music = tmp_path / "explicit.mp3"
    explicit_music.write_bytes(b"fake")

    pipeline = SkillsPipeline(
        ctx,
        config_path=full_config,
        presets_dir=explicit_presets,
        music_path=explicit_music,
    )
    assert pipeline.presets_dir == explicit_presets
    assert pipeline.music_path == explicit_music


def test_skills_pipeline_export_style_from_config(tmp_path: Path, full_config: Path) -> None:
    """The export output filename template is read from ``render.output_name_template``."""
    ctx = JobContext(
        job_id="config-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    pipeline = SkillsPipeline(ctx, config_path=full_config)
    assert pipeline.project_config.render.output_name_template == "{stage}_deliverable.mp4"


def test_asset_ingestion_manager_uses_config_cache_dir(tmp_path: Path) -> None:
    """``AssetIngestionManager`` honors a repo-relative ``render.cache_dir`` override."""
    ctx = JobContext(
        job_id="cache-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    manager = AssetIngestionManager(ctx, cache_dir="projects/{job_id}/.cache")
    assert manager.cache_dir == ctx.work_dir / "projects/{job_id}/.cache"


def test_project_config_cli_resolve_presets_dir(tmp_path: Path) -> None:
    """The CLI helper ``_resolve_presets_dir`` reads ``paths.presets`` from config."""
    from video_editor_cli.main import _resolve_presets_dir

    presets_dir = tmp_path / "from_config"
    config = _write_config(
        tmp_path,
        {"paths": {"presets": str(presets_dir)}},
    )
    resolved = _resolve_presets_dir(None, config)
    assert resolved == presets_dir


def test_project_config_warns_unknown_llm_keys(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Bug #173: misspelled keys in the ``llm:`` section produce a warning."""
    config = _write_config(
        tmp_path,
        {"llm": {"default_provider": "test", "providors": {}}},
    )
    cfg = ProjectConfig.load(config)
    assert cfg.llm.default_provider == "test"
    assert "providors" in caplog.text


def test_project_config_warns_unknown_assets_keys(
    tmp_path: Path,
) -> None:
    """Bug #173: misspelled keys in the ``assets:`` section produce a warning."""

    config = _write_config(
        tmp_path,
        {"assets": {"providers": {}, "defaults": {}, "fal": {}}},
    )
    with pytest.warns(UserWarning, match="fal"):
        cfg = ProjectConfig.load(config)
    assert cfg.assets.providers == {}
    assert cfg.assets.defaults == {}


def test_project_config_loads_hyperframes_section(tmp_path: Path) -> None:
    """Bug #189: ``render.hyperframes`` is loaded and exposed on RenderConfig."""
    config = _write_config(
        tmp_path,
        {
            "render": {
                "hyperframes": {
                    "cli": "custom-hyperframes",
                    "timeout": 1200,
                }
            }
        },
    )
    cfg = ProjectConfig.load(config)
    assert cfg.render.hyperframes.cli == "custom-hyperframes"
    assert cfg.render.hyperframes.timeout == 1200
