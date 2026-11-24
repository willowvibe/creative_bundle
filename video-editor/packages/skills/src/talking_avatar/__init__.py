"""Talking-avatar skill package."""

from talking_avatar.avatar import AvatarRenderer, iter_avatar_frames
from talking_avatar.talking_avatar import (
    TalkingAvatarError,
    TalkingAvatarSkill,
    render_talking_avatar,
)
from talking_avatar.timeline import (
    BlinkScheduler,
    VisemeFrame,
    WordTiming,
    blink_frames,
    build_viseme_timeline,
    grapheme_to_shape,
    rms_to_openness,
    word_to_shapes,
)

__all__ = [
    "AvatarRenderer",
    "BlinkScheduler",
    "TalkingAvatarError",
    "TalkingAvatarSkill",
    "VisemeFrame",
    "WordTiming",
    "blink_frames",
    "build_viseme_timeline",
    "grapheme_to_shape",
    "iter_avatar_frames",
    "render_talking_avatar",
    "rms_to_openness",
    "word_to_shapes",
]
