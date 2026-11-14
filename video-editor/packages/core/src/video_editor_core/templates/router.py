"""TemplateRouter: pick the best supported template from user inputs.

The router is intentionally lightweight. It inspects the source path(s) and
optional explicit template name, then returns a ``TemplateResult`` that the
caller can use to build a CLI command, validate assumptions, or generate a
starter concept file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from video_editor_core.templates.base import Template, TemplateResult
from video_editor_core.templates.eleven_templates import TEMPLATES
from video_editor_core.templates.yaml_templates import (
    TEMPLATES_DIR,
    load_yaml_templates,
)


class TemplateSelectionError(ValueError):
    """Raised when the router cannot find a matching template."""


class TemplateRouter:
    """Route an incoming request to one of the supported templates."""

    def __init__(self, templates_dir: Path | str | None = None) -> None:
        yaml_dir = Path(templates_dir) if templates_dir else TEMPLATES_DIR
        if yaml_dir.exists():
            self._templates = load_yaml_templates(yaml_dir)
        else:
            # Fallback to built-in Python definitions when the YAML directory
            # is missing (e.g., packaged installs without the templates folder).
            self._templates = TEMPLATES
        self._by_name = {t.name: t for t in self._templates}

    @property
    def template_names(self) -> list[str]:
        """Return all supported template names."""
        return list(self._by_name.keys())

    def get_template(self, name: str) -> Template:
        """Return a template by name or raise a clear error."""
        if name not in self._by_name:
            raise TemplateSelectionError(
                f"Unknown template {name!r}. Available: {', '.join(sorted(self._by_name))}"
            )
        return self._by_name[name]

    def select(
        self,
        source: Path | str | None = None,
        template_name: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> TemplateResult:
        """Pick a template and resolve its configuration.

        Args:
            source: The primary user-supplied path (video file, directory,
                markdown script, or audio file). Optional when a template is
                explicitly named.
            template_name: Optional explicit template selection. When given,
                it wins over source inference.
            overrides: Optional ``{"preset": ..., "aspect_ratio": ...,
                "providers": {...}}`` overrides.

        Returns:
            A ``TemplateResult`` carrying the chosen pipeline, preset, aspect
            ratio, provider defaults, and any warnings.
        """
        if template_name:
            template = self.get_template(template_name)
            result = template.resolve(source=Path(source) if source else None, overrides=overrides)
            result.notes.append("template selected explicitly")
            return result

        if source is None:
            raise TemplateSelectionError(
                "No source or template provided. Pass --template or a source path."
            )

        source_path = Path(source)
        template = self._infer(source_path, overrides=overrides)
        result = template.resolve(source=source_path, overrides=overrides)
        result.notes.append(f"template inferred from {source_path}")

        if template.build_concept and source_path.is_file() and source_path.suffix == ".md":
            result.concept = template.build_concept(source_path)
        return result

    def _infer(
        self,
        source: Path,
        overrides: dict[str, Any] | None = None,
    ) -> Template:
        """Infer the best template from a source path."""
        if not source.exists():
            raise TemplateSelectionError(f"Source path does not exist: {source}")

        if source.is_dir():
            return self._by_name["raw_batch"]

        if source.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}:
            return self._by_name["podium_cut"]

        if source.suffix.lower() in {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}:
            media_dir = (overrides or {}).get("media_dir")
            if media_dir and Path(media_dir).is_dir():
                return self._by_name["musical_video"]
            return self._by_name["voice_canvas"]

        if source.suffix.lower() in {".md", ".txt", ".markdown"}:
            # A markdown with a `cast:` frontmatter block is a storyteller script.
            if source.is_file():
                try:
                    text = source.read_text(encoding="utf-8")
                    if text.startswith("---"):
                        parts = text.split("---", 2)
                        fm_body = parts[1] if len(parts) >= 3 else ""
                        if "cast:" in fm_body:
                            return self._by_name["storyteller_hd"]
                except OSError:
                    pass
            # Default markdown → explainer reel. The user can override with
            # --template image_story, social_hook, etc.
            return self._by_name["explainer_reel"]

        raise TemplateSelectionError(
            f"Cannot infer a template for {source.suffix!r} files. "
            f"Use --template with one of: {', '.join(sorted(self._by_name))}"
        )

    def list_templates(self) -> list[dict[str, Any]]:
        """Return a human-readable summary of every template."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "pipeline": t.pipeline_kind,
                "preset": t.default_preset,
                "aspect_ratio": t.default_aspect_ratio,
                "required_inputs": [i.name for i in t.inputs if i.required],
                "optional_inputs": [i.name for i in t.inputs if not i.required],
            }
            for t in self._templates
        ]
