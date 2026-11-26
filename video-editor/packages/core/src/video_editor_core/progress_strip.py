"""The cost checklist strip: where the running tally's story has got to.

The script's front matter lists the ordered ``progress`` steps; a scene names
the one it covers. The strip is a compact vertical checklist under the tally
chip: finished steps read ``✓`` and dimmed, the current step is bold in the
accent yellow with a ``▸`` marker, later steps are muted grey.

Static text except the marker, whose own ``transform``/``visibility`` animate
(never an opacity animation on a child of an animated element, which
HyperFrames drops). Telugu is drawn by Chrome from this markup.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from video_editor_core.fonts import font_stack

#: The current step's marker appears this long after the cut.
MARKER_DELAY_SECONDS = 0.3
MARKER_SECONDS = 0.3

ACCENT = "#FDB813"


@dataclass(frozen=True)
class Progress:
    items: tuple[str, ...]
    current: str

    @property
    def current_index(self) -> int:
        return self.items.index(self.current)


def parse_progress_labels(raw: object) -> tuple[str, ...]:
    """Read the front-matter ``progress`` list: non-empty, unique, ordered labels."""
    if not isinstance(raw, Sequence) or isinstance(raw, str) or not raw:
        raise ValueError("`progress` must be a non-empty list of step labels")
    labels = [str(item).strip() if item is not None else "" for item in raw]
    if any(not label for label in labels):
        raise ValueError("`progress` labels must be non-empty")
    if len(set(labels)) != len(labels):
        raise ValueError("`progress` labels must be unique")
    return tuple(labels)


def parse_progress(raw: object) -> Progress | None:
    """Read a scene's resolved ``progress`` prop: ``{items: [...], current: label}``."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError(f"progress must be {{items, current}}, got {raw!r}")
    items = parse_progress_labels(raw.get("items"))
    current = str(raw.get("current") or "").strip()
    if current not in items:
        raise ValueError(f"progress step {current!r} is not one of {list(items)}")
    return Progress(items=items, current=current)


def render_progress(progress: Progress, *, index: int, width: int) -> tuple[str, str]:
    """Markup and CSS for one scene's checklist strip."""
    unit = max(0.5, width / 1920.0)
    current = progress.current_index
    rows = []
    for position, label in enumerate(progress.items):
        if position < current:
            state, mark = "done", "✓"
        elif position == current:
            state, mark = "now", "▸"
        else:
            state, mark = "todo", ""
        rows.append(
            f'<div class="progress-item progress-{state}">'
            f'<span class="progress-mark">{mark}</span>'
            f'<span class="progress-label">{html.escape(label)}</span></div>'
        )
    markup = f'<div class="progress progress-{index}">{"".join(rows)}</div>'
    selector = f".progress-{index}"
    css = (
        f"      {selector} {{ box-sizing: border-box; display: inline-flex; flex-direction: column;\n"
        f"        gap: {6 * unit:.0f}px; width: 100%;\n"
        f"        padding: {12 * unit:.0f}px {20 * unit:.0f}px; border-radius: {14 * unit:.0f}px;\n"
        "        background: rgba(12, 14, 18, 0.78); color: #fff;\n"
        f"        font-family: {font_stack(None)};\n"
        f"        box-shadow: 0 {4 * unit:.0f}px {18 * unit:.0f}px rgba(0, 0, 0, 0.35); }}\n"
        f"      {selector} .progress-item {{ display: flex; align-items: baseline;\n"
        f"        gap: {10 * unit:.0f}px; font-size: {26 * unit:.0f}px; line-height: 1.25; }}\n"
        f"      {selector} .progress-mark {{ display: inline-block; width: {26 * unit:.0f}px;\n"
        "        flex: none; text-align: center; }\n"
        f"      {selector} .progress-done {{ color: rgba(255, 255, 255, 0.45); }}\n"
        f"      {selector} .progress-now {{ color: {ACCENT}; font-weight: 800; }}\n"
        f"      {selector} .progress-todo {{ color: rgba(255, 255, 255, 0.35); }}\n"
        # Transform and visibility only: no opacity on a child of the strip.
        f"      {selector} .progress-now .progress-mark {{ animation: progress_mark_{index} "
        f"{MARKER_SECONDS:.2f}s ease-out {MARKER_DELAY_SECONDS:.2f}s both; }}\n"
        f"      @keyframes progress_mark_{index} {{ from {{ visibility: hidden;\n"
        f"        transform: translateX({-10 * unit:.0f}px); }}\n"
        "        to { visibility: visible; transform: none; } }\n"
    )
    return markup, css
