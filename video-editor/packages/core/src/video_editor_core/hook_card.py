"""The hook overlay: a headline panel over a scene's first seconds.

A scene may declare ``hook: {headline: ..., sub: ..., seconds: 3.0}``. The
panel sits clear of the captions (bottom) and the tally chip (top left), fades
and slides in just after the cut and is gone again by ``seconds``.

Only the panel itself carries an animation (opacity and transform); its text
children are static. HyperFrames drops a child with its own opacity animation
inside an animated, clipped parent, so the whole card moves as one element.
Telugu text is drawn by Chrome from this markup, never by ffmpeg drawtext.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from dataclasses import dataclass

from video_editor_core.fonts import font_stack

DEFAULT_HOOK_SECONDS = 3.0
MIN_HOOK_SECONDS = 1.0
MAX_HOOK_SECONDS = 8.0

#: The card starts to appear this long after the cut, and takes this long to settle.
HOOK_IN_DELAY_SECONDS = 0.15
HOOK_IN_SECONDS = 0.4
#: It is fully gone ``seconds`` after the scene starts; the fade takes this long.
HOOK_OUT_SECONDS = 0.35

#: The caption highlight yellow.
ACCENT = "#FDB813"

_KEYS = frozenset({"headline", "sub", "seconds"})


@dataclass(frozen=True)
class Hook:
    headline: str
    sub: str = ""
    seconds: float = DEFAULT_HOOK_SECONDS


def parse_hook(raw: object) -> Hook | None:
    """Read a scene's ``hook`` prop; ``None`` when absent, ``ValueError`` when malformed."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError(f"hook must be a mapping with a `headline`, got {raw!r}")
    unknown = sorted(str(key) for key in raw if key not in _KEYS)
    if unknown:
        raise ValueError(
            f"hook has unknown key(s) {unknown}; use `headline`, `sub` and `seconds` "
            "(quote values that contain commas)"
        )
    headline = str(raw.get("headline") or "").strip()
    if not headline:
        raise ValueError("hook needs a non-empty `headline`")
    seconds = raw.get("seconds", DEFAULT_HOOK_SECONDS)
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        raise ValueError(f"hook `seconds` must be a number, got {seconds!r}")
    if not MIN_HOOK_SECONDS <= seconds <= MAX_HOOK_SECONDS:
        raise ValueError(
            f"hook `seconds` must be between {MIN_HOOK_SECONDS:g} and {MAX_HOOK_SECONDS:g}, "
            f"got {seconds:g}"
        )
    return Hook(headline=headline, sub=str(raw.get("sub") or "").strip(), seconds=float(seconds))


def render_hook(hook: Hook, *, index: int, width: int) -> tuple[str, str]:
    """Markup and CSS for one scene's hook card."""
    unit = max(0.5, width / 1920.0)
    seconds = hook.seconds
    fade_in_end = HOOK_IN_DELAY_SECONDS + HOOK_IN_SECONDS

    def percent(t: float) -> str:
        return f"{100.0 * t / seconds:.3f}%"

    keyframes = (
        f"      @keyframes hook_{index} {{\n"
        f"        0%, {percent(HOOK_IN_DELAY_SECONDS)} {{ opacity: 0; transform: translateY({28 * unit:.0f}px); }}\n"
        f"        {percent(fade_in_end)}, {percent(seconds - HOOK_OUT_SECONDS)} "
        "{ opacity: 1; transform: none; }\n"
        "        100% { opacity: 0; transform: none; } }\n"
    )
    sub = f'<div class="hook-sub">{html.escape(hook.sub)}</div>' if hook.sub else ""
    # The wrapper is static and centres the animated panel in the layer rect.
    markup = (
        f'<div class="hook-wrap hook-wrap-{index}">'
        f'<div class="hook hook-{index}" style="animation: hook_{index} {seconds:.2f}s linear 0s both">'
        f'<div class="hook-headline">{html.escape(hook.headline)}</div>{sub}</div></div>'
    )
    css = (
        f"      .hook-wrap-{index} {{ width: 100%; height: 100%; display: flex;\n"
        "        align-items: center; justify-content: center; }\n"
        f"      .hook-{index} {{ box-sizing: border-box; width: 100%; opacity: 0;\n"
        "        display: flex; flex-direction: column; align-items: center;\n"
        f"        justify-content: center; text-align: center; gap: {10 * unit:.0f}px;\n"
        f"        padding: {22 * unit:.0f}px {32 * unit:.0f}px; border-radius: {22 * unit:.0f}px;\n"
        "        background: rgba(12, 14, 18, 0.80); color: #fff;\n"
        f"        font-family: {font_stack(None)};\n"
        f"        box-shadow: 0 {6 * unit:.0f}px {24 * unit:.0f}px rgba(0, 0, 0, 0.40); }}\n"
        f"      .hook-{index} .hook-headline {{ font-size: {72 * unit:.0f}px; font-weight: 800;\n"
        "        line-height: 1.15; color: #fff; }\n"
        f"      .hook-{index} .hook-sub {{ font-size: {40 * unit:.0f}px; font-weight: 700;\n"
        f"        line-height: 1.2; color: {ACCENT}; }}\n"
        f"{keyframes}"
    )
    return markup, css
