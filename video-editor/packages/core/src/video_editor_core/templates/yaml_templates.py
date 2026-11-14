"""Load templates from YAML manifests on disk.

This module keeps the historical ``Template`` dataclass contract while
moving the actual definitions out of Python and into ``templates/*.yaml``.
The loader also validates the YAML shape and wires up concept builders
from ``templates/_concepts.yaml``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import structlog
import yaml

from video_editor_core.constants import REPO_ROOT
from video_editor_core.templates.base import InputSpec, PipelineKind, Template

logger = structlog.get_logger(__name__)


TEMPLATES_DIR = REPO_ROOT / "templates"
CONCEPTS_PATH = TEMPLATES_DIR / "_concepts.yaml"

# ``base`` uses ``from __future__ import annotations``, so reading the
# dataclass's ``__annotations__`` would yield the *string* "PipelineKind";
# the module-level ``PipelineKind`` is the real ``Literal``. Derive the
# allowed set from it so adding a kind to ``base.PipelineKind`` is enough.
_ALLOWED_KINDS: frozenset[str] = frozenset(get_args(PipelineKind))


def _load_concepts() -> dict[str, str]:
    """Load the shared concept body snippets."""
    if not CONCEPTS_PATH.exists():
        return {}
    with open(CONCEPTS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def _build_concept(kind: str) -> Callable[[Path], str]:
    """Return a concept builder for the named kind."""
    bodies = _load_concepts()

    def _builder(path: Path) -> str:
        if kind == "default" and path.exists():
            return path.read_text(encoding="utf-8")
        return bodies.get(kind, bodies.get("default", ""))

    return _builder


def _validate_template(raw: dict[str, Any]) -> None:
    """Raise a clear error when a manifest is missing required fields."""
    required = {"name", "description", "inputs", "pipeline_kind", "default_preset"}
    missing = required - set(raw.keys())
    if missing:
        raise ValueError(
            f"Template manifest {raw.get('name', raw)!r} missing keys: {sorted(missing)}"
        )

    if raw["pipeline_kind"] not in _ALLOWED_KINDS:
        raise ValueError(
            f"Template {raw['name']!r} has invalid pipeline_kind {raw['pipeline_kind']!r}"
        )

    for inp in raw["inputs"]:
        if not isinstance(inp, dict):
            raise ValueError(f"Template {raw['name']!r} input is not a mapping: {inp!r}")
        if "name" not in inp or "description" not in inp:
            raise ValueError(f"Template {raw['name']!r} input missing name/description: {inp!r}")


def load_yaml_templates(templates_dir: Path | str | None = None) -> list[Template]:
    """Discover and validate all template YAML files in the templates directory."""
    directory = Path(templates_dir) if templates_dir else TEMPLATES_DIR
    if not directory.exists():
        logger.warning("templates_dir_missing", path=str(directory))
        return []

    templates: list[Template] = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        _validate_template(raw)

        inputs = [
            InputSpec(
                name=str(inp["name"]),
                description=str(inp["description"]),
                required=bool(inp.get("required", True)),
                example=str(inp.get("example", "")),
            )
            for inp in raw["inputs"]
        ]

        quality_pair = raw.get("quality_tier_pair")
        quality_tier_pair: tuple[str, str] | None = None
        if isinstance(quality_pair, (list, tuple)) and len(quality_pair) == 2:
            quality_tier_pair = (str(quality_pair[0]), str(quality_pair[1]))

        templates.append(
            Template(
                name=str(raw["name"]),
                description=str(raw["description"]),
                inputs=inputs,
                pipeline_kind=raw["pipeline_kind"],
                default_preset=str(raw["default_preset"]),
                default_aspect_ratio=str(raw.get("default_aspect_ratio", "16:9")),
                default_providers=dict(raw.get("default_providers", {})),
                default_music_mood=str(raw.get("default_music_mood", "")),
                scene_type_hints=list(raw.get("scene_type_hints", [])),
                build_concept=_build_concept(str(raw.get("build_concept_kind", "default"))),
                caveats=list(raw.get("caveats", [])),
                quality_tier_pair=quality_tier_pair,
            )
        )

    return templates


def yaml_template_map(templates_dir: Path | str | None = None) -> dict[str, Template]:
    """Return a name -> Template mapping loaded from YAML manifests."""
    return {t.name: t for t in load_yaml_templates(templates_dir)}


def yaml_template_names(templates_dir: Path | str | None = None) -> list[str]:
    """Return the list of template names from YAML manifests."""
    return [t.name for t in load_yaml_templates(templates_dir)]
