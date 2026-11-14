"""Unified project-level configuration loader.

Loads the full ``config.yaml`` and exposes typed sections for audio,
render, paths, and transcription, while delegating LLM and asset-provider
configuration to their existing dedicated loaders.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, TypeVar

import structlog
from pydantic import BaseModel, Field, field_validator
from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_core.config_paths import (
    CONFIG_ENV,
    CONFIG_NAME,
    find_config_path,
    load_yaml_with_overlay,
)
from video_editor_llm.config import LLMConfig
from video_editor_state.ledger import LedgerConfig, NullLedger, ProjectLedger

_T = TypeVar("_T", bound=BaseModel)

logger = structlog.get_logger(__name__)

DEFAULT_RENDERS = {
    "container": "mp4",
    "video_codec": "libx264",
    "audio_codec": "aac",
    "fps": 30,
    "width": 1920,
    "height": 1080,
    "crf": 23,
    "video_bitrate": "",
    "audio_bitrate": "192k",
    "output_name_template": "{stage}.mp4",
}


class AudioConfig(BaseModel):
    """Audio processing defaults."""

    loudness_target_lufs: float = -16.0
    true_peak_dbfs: float = -1.5
    loudness_range_lu: int = 11
    default_music_path: str = ""
    sample_rate: int = 48000


class HyperframesConfig(BaseModel):
    """Hyperframes rendering defaults."""

    cli: str = "hyperframes"
    timeout: int = 600

    @field_validator("timeout", mode="before")
    @classmethod
    def _coerce_int(cls, value: Any) -> int:
        if value is None:
            return 0
        return int(value)


class RenderConfig(BaseModel):
    """Render / export format defaults."""

    cache_dir: str = ".cache"
    default_preset: str = "short_explainer"
    container: str = "mp4"
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    fps: int = 30
    width: int = 1920
    height: int = 1080
    crf: int = 23
    video_bitrate: str = ""
    audio_bitrate: str = "192k"
    preset: str = "fast"
    hwaccel: str = ""
    # Composition render engine: "auto" (router), "ffmpeg", or "hyperframes".
    engine: str = "auto"
    output_name_template: str = "{stage}.mp4"
    hyperframes: HyperframesConfig = Field(default_factory=HyperframesConfig)

    @field_validator("fps", "width", "height", "crf", mode="before")
    @classmethod
    def _coerce_int(cls, value: Any) -> int:
        if value is None:
            return 0
        return int(value)


class PathsConfig(BaseModel):
    """Repository-relative path defaults."""

    projects: str = "projects"
    outputs: str = "outputs"
    presets: str = "presets"
    assets: str = "assets"
    music: str = ""


class TranscriptionConfig(BaseModel):
    """Transcription engine defaults."""

    engine: str = "whisperx"
    model: str = "small"
    language: str = "en"
    device: str = "cpu"
    compute_type: str = "int8"


class VoiceInfo(BaseModel):
    """A named voice entry for the brand profile."""

    id: str
    label: str


class BrandConfig(BaseModel):
    """Brand profile defaults (voices, colors, fonts)."""

    voices: dict[str, VoiceInfo] = Field(default_factory=dict)


class QueueConfig(BaseModel):
    """Worker queue settings (#275)."""

    #: A running job whose worker has not heartbeated for this long is
    #: considered crashed and is returned to the pending queue.
    stale_seconds: float = Field(default=900.0, gt=0.0)


class McpConfig(BaseModel):
    """Spending limits for jobs started through the MCP server (#277).

    MCP arguments are written by an agent, so every manager or pipeline the
    server creates carries a budget: ``default_budget_usd`` per job (a caller's
    ``budget`` may lower it, never raise it), and a single generation estimated
    above ``per_call_cap_usd`` is refused unless the call passes a matching
    ``approve_usd``.
    """

    default_budget_usd: float = Field(default=1.0, ge=0.0)
    per_call_cap_usd: float = Field(default=0.5, ge=0.0)


class LayerizeConfig(BaseModel):
    """The GPU subprocess that turns a still into depth + fg + plate.

    ``command`` runs in the model environment (not the engine's) and must
    accept ``--images S --out D --process-res N --depth-model M --cutout C``.
    Leave it empty to keep ``layers: auto`` packs refused with a clear error.
    """

    command: list[str] = Field(default_factory=list)
    timeout: float = Field(default=600.0, gt=0.0)
    depth_model: str = "depth-anything/DA3MONO-LARGE"
    process_res: int = 1280
    cutout: str = "birefnet"


class ProjectConfig(BaseModel):
    """Full project configuration loaded from ``config.yaml``."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    assets: AssetProviderConfig = Field(default_factory=AssetProviderConfig)
    brand: BrandConfig = Field(default_factory=BrandConfig)
    ledger: LedgerConfig = Field(default_factory=LedgerConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    layerize: LayerizeConfig = Field(default_factory=LayerizeConfig)

    @classmethod
    def load(cls, path: Path | str | None = None) -> ProjectConfig:
        """Load full project configuration from a YAML file.

        If no path is given, look for ``config.yaml`` in the current working
        directory and then for ``VIDEO_EDITOR_CONFIG`` in the environment.
        """
        target = cls._resolve_path(path)
        if target is None:
            return cls()

        raw = load_yaml_with_overlay(target)
        return cls(
            llm=cls._load_llm_section(raw, target),
            transcription=cls._load_section(raw, "transcription", TranscriptionConfig, target),
            audio=cls._load_section(raw, "audio", AudioConfig, target),
            render=cls._load_section(raw, "render", RenderConfig, target),
            paths=cls._load_section(raw, "paths", PathsConfig, target),
            assets=cls._load_assets_section(raw, target),
            brand=cls._load_section(raw, "brand", BrandConfig, target),
            ledger=cls._load_ledger_section(raw, target),
            mcp=cls._load_section(raw, "mcp", McpConfig, target),
            layerize=cls._load_section(raw, "layerize", LayerizeConfig, target),
        )

    @classmethod
    def _load_llm_section(cls, raw: dict[str, Any], path: Path) -> LLMConfig:
        """Parse the ``llm:`` section directly from the already-loaded raw dict.

        Avoids re-reading the file with ``LLMConfig.load(path)``, which would
        warn about unrelated top-level keys like ``assets:``.
        """
        section = raw.get("llm")
        if not isinstance(section, dict):
            # No ``llm:`` section — maybe the file uses the historical
            # top-level providers/tasks layout. Validate the whole raw dict.
            llm_keys = {
                "providers",
                "tasks",
                "default_provider",
                "max_tokens_per_job",
                "max_cost_usd_per_job",
                "max_llm_attempts_per_job",
                "primary_attempts",
                "fallback_attempts",
                "circuit_breaker_failure_threshold",
                "retry_backoff_base_seconds",
                "retry_backoff_max_seconds",
                "retry_jitter",
            }
            if any(k in raw for k in llm_keys):
                LLMConfig._warn_unknown_keys(raw, path, section="")
                try:
                    return LLMConfig.model_validate(raw)
                except Exception as exc:  # noqa: BLE001 - a malformed overlay falls back to the base config
                    logger.warning(
                        f"invalid top-level LLM config; using defaults: {exc}",
                        config_path=str(path),
                    )
                    return LLMConfig()
            return LLMConfig()
        LLMConfig._warn_unknown_keys(section, path, section="llm")
        try:
            return LLMConfig.model_validate(section)
        except Exception as exc:  # noqa: BLE001 - a malformed overlay falls back to the base config
            logger.warning(
                f"invalid llm config section; using defaults: {exc}",
                config_path=str(path),
            )
            return LLMConfig()

    @classmethod
    def _load_assets_section(cls, raw: dict[str, Any], path: Path) -> AssetProviderConfig:
        """Parse the ``assets:`` section directly from the already-loaded raw dict."""
        section = raw.get("assets")
        if not isinstance(section, dict):
            return AssetProviderConfig()
        AssetProviderConfig._warn_unknown_keys(section, path)
        try:
            return AssetProviderConfig.model_validate(section)
        except Exception as exc:  # noqa: BLE001 - a malformed overlay falls back to the base config
            logger.warning(
                f"invalid assets config section; using defaults: {exc}",
                config_path=str(path),
            )
            return AssetProviderConfig()

    @classmethod
    def _load_ledger_section(cls, raw: dict[str, Any], path: Path) -> LedgerConfig:
        """Parse the ``ledger:`` section from the raw config dict."""
        section = raw.get("ledger")
        if section is None:
            return LedgerConfig()
        if not isinstance(section, dict):
            logger.warning(
                "config section 'ledger' must be a mapping; using defaults",
                config_path=str(path),
            )
            return LedgerConfig()
        try:
            cfg = LedgerConfig(**section)
            # Resolve a configured relative path against the repo root (config file dir).
            if cfg.path is not None and not cfg.path.is_absolute():
                cfg.path = (path.resolve().parent / cfg.path).resolve()
            return cfg
        except Exception as exc:  # noqa: BLE001 - a malformed overlay falls back to the base config
            logger.warning(
                f"invalid ledger config section; using defaults: {exc}",
                config_path=str(path),
            )
            return LedgerConfig()

    def ledger_instance(
        self,
        repo_root: Path | str | None = None,
    ) -> ProjectLedger | NullLedger:
        """Return an initialized ledger instance based on config.

        When the ledger is disabled or no path is configured, returns a
        no-op ``NullLedger``. Otherwise resolves the configured path
        (default ``outputs/projects.db`` under the repo root) and returns a
        ``ProjectLedger`` with tables initialized.
        """
        if not self.ledger.enabled:
            return NullLedger()
        repo_root_path = (
            Path(__file__).resolve().parents[4] if repo_root is None else Path(repo_root)
        )
        repo_root_path = repo_root_path.resolve()
        path = self.ledger.path
        if path is None:
            path = repo_root_path / "outputs" / "projects.db"
        if not path.is_absolute():
            path = (repo_root_path / path).resolve()
            if not path.is_relative_to(repo_root_path):
                logger.warning(
                    f"ledger path {path} resolves outside repo root; using default",
                    repo_root=str(repo_root_path),
                )
                path = repo_root_path / "outputs" / "projects.db"
        ledger = ProjectLedger(path)
        ledger.init_db()
        return ledger

    @classmethod
    def _load_section(
        cls,
        raw: dict[str, Any],
        key: str,
        model: type[_T],
        path: Path,
    ) -> _T:
        """Load and validate a single top-level config section."""
        section = raw.get(key)
        if section is None:
            return model()
        if not isinstance(section, dict):
            logger.warning(
                f"config section {key!r} must be a mapping; using defaults",
                config_path=str(path),
            )
            return model()
        try:
            return model.model_validate(section)
        except Exception as exc:  # noqa: BLE001 - a malformed overlay falls back to the base config
            logger.warning(
                f"invalid {key} config; using defaults: {exc}",
                config_path=str(path),
            )
            return model()

    @classmethod
    def _resolve_path(cls, path: Path | str | None) -> Path | None:
        env_path = os.getenv(CONFIG_ENV) if path is None else None
        target = find_config_path(path)
        if target is not None and not target.exists():
            label = "config file" if path is not None else CONFIG_ENV
            logger.warning(
                f"{label} not found: {target}; using defaults",
                config_path=str(target),
            )
            return None
        if env_path and target is not None and Path(env_path).expanduser() != target:
            logger.warning(
                f"{CONFIG_ENV} file not found: {env_path}; searching for {CONFIG_NAME}",
                config_path=str(env_path),
            )
        return target

    def resolve_path(
        self,
        value: str | None,
        default_relative_to_repo: Path | str | None = None,
        repo_root: Path | str | None = None,
    ) -> Path | None:
        """Expand a config path string into an absolute path contained in the repo.

        ``value`` can be an absolute path or a path relative to the repo
        root. When ``value`` is empty/None and ``default_relative_to_repo`` is
        given, that default is expanded instead. Returns ``None`` when both
        are empty/None.

        The returned path is always resolved and checked to be inside the
        repo root. Values that escape the repo (absolute paths outside it or
        relative paths containing ``..``) fall back to ``default_relative_to_repo``
        so a malicious or mis-typed config cannot read arbitrary files.
        """
        repo_root_path = (
            Path(__file__).resolve().parents[4] if repo_root is None else Path(repo_root)
        )
        repo_root_path = repo_root_path.resolve()

        if not value:
            if not default_relative_to_repo:
                return None
            value = str(default_relative_to_repo)

        candidate = Path(value)
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (repo_root_path / candidate).resolve()
        )

        if resolved.is_relative_to(repo_root_path):
            return resolved

        logger.warning(
            f"configured path {value!r} resolves outside repo root {repo_root_path}; "
            f"falling back to default {default_relative_to_repo!r}",
            config_value=value,
            repo_root=str(repo_root_path),
        )
        if default_relative_to_repo:
            return (repo_root_path / default_relative_to_repo).resolve()
        return None

    def presets_dir(self, repo_root: Path | str | None = None) -> Path:
        """Return the configured presets directory as an absolute path."""
        resolved = self.resolve_path(
            self.paths.presets,
            default_relative_to_repo="presets",
            repo_root=repo_root,
        )
        if resolved is None:
            raise RuntimeError(f"presets directory could not be resolved; repo_root={repo_root}")
        return resolved

    def default_music_path(
        self,
        repo_root: Path | str | None = None,
    ) -> Path | None:
        """Return the configured default background music file, if any."""
        # Explicit audio.default_music_path takes priority over paths.music.
        music_value = self.audio.default_music_path or self.paths.music
        return self.resolve_path(music_value, repo_root=repo_root)


@dataclass(frozen=True)
class Settings:
    """The project config plus the file it came from, resolved once per process."""

    config: ProjectConfig
    path: Path | None = None

    _cache: ClassVar[dict[tuple[str, int, int], Settings]] = {}

    @classmethod
    def load(cls, path: Path | str | None = None) -> Settings:
        target = ProjectConfig._resolve_path(path)
        if target is None:
            key = ("", 0, 0)
        else:
            stat = target.stat()
            key = (str(target.resolve()), stat.st_mtime_ns, stat.st_size)
        cached = cls._cache.get(key)
        if cached is not None:
            return cached
        config = ProjectConfig.load(target) if target is not None else ProjectConfig()
        settings = cls(config=config, path=target)
        cls._cache[key] = settings
        return settings


__all__ = [
    "AudioConfig",
    "LedgerConfig",
    "McpConfig",
    "PathsConfig",
    "ProjectConfig",
    "RenderConfig",
    "Settings",
    "TranscriptionConfig",
]
