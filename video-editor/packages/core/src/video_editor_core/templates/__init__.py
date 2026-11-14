"""Template layer: map user inputs to supported pipeline recipes."""

from __future__ import annotations

from video_editor_core.templates.base import InputSpec, Template, TemplateResult
from video_editor_core.templates.eleven_templates import TEMPLATE_NAMES, TEMPLATES
from video_editor_core.templates.router import TemplateRouter
from video_editor_core.templates.yaml_templates import (
    CONCEPTS_PATH,
    TEMPLATES_DIR,
    load_yaml_templates,
    yaml_template_map,
    yaml_template_names,
)

__all__ = [
    "CONCEPTS_PATH",
    "InputSpec",
    "Template",
    "TemplateResult",
    "TemplateRouter",
    "TEMPLATES",
    "TEMPLATES_DIR",
    "TEMPLATE_NAMES",
    "load_yaml_templates",
    "yaml_template_map",
    "yaml_template_names",
]
