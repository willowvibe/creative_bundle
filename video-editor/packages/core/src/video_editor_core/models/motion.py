"""Motion-clip generation policy shared by every stage that requests video.

The planners (``storyteller_planner``) and the asset stage (``ve assets`` via
``video_editor_asset_ingestion.scene_assets``) both turn a scene into a VIDEO
request. They must agree on the length, or one path asks a model for a clip it
cannot produce while the other clamps (#257). Keep the rule here.
"""

from __future__ import annotations

import math

# docs/POYO_ROUTING.md: Seedance accepts 4-15 s (5-10 s recommended) and Sora
# has fixed 4/8/12/16/20 s tiers, so [4, 10] is accepted by every model in
# ``VIDEO_TIER_MODELS``. The renderer loops any remaining shortfall.
MOTION_DURATION_MIN = 4
MOTION_DURATION_MAX = 10


def motion_duration_for(seconds: float) -> int:
    """Return the whole-second generation length for a scene of ``seconds``.

    Rounded up (providers accept whole seconds, and rounding down would end
    the clip early and hold the last frame) and clamped to the supported
    window.
    """
    return min(MOTION_DURATION_MAX, max(MOTION_DURATION_MIN, math.ceil(seconds)))
