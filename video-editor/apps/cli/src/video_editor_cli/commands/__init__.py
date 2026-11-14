"""CLI command modules."""

from video_editor_cli.commands.estimate import register_estimate
from video_editor_cli.commands.finalize import register_finalize
from video_editor_cli.commands.gen_models import register_gen_models
from video_editor_cli.commands.preview import register_preview
from video_editor_cli.commands.update_pricing import register_update_pricing

__all__ = [
    "register_estimate",
    "register_finalize",
    "register_gen_models",
    "register_preview",
    "register_update_pricing",
]
