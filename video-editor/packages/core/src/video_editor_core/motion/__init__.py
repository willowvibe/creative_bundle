"""Named motion primitives compiled to deterministic CSS."""

from video_editor_core.motion import primitives  # noqa: F401  (registers)
from video_editor_core.motion import text as motion_text  # noqa: F401  (registers)
from video_editor_core.motion.registry import (
    PRIMITIVES,
    MotionError,
    Primitive,
    build_motion,
    register,
)
from video_editor_core.motion.spec import DEFAULT_EASE, MotionCSS, keyframes_name

__all__ = [
    "DEFAULT_EASE",
    "PRIMITIVES",
    "MotionCSS",
    "MotionError",
    "Primitive",
    "build_motion",
    "keyframes_name",
    "motion_text",
    "register",
]
