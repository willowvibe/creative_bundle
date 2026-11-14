"""Caption QA gates measured on what was burned in (#338-#341).

``caption_tokens`` compares every burned caption word with the script, which
catches glued words ("kilometresa") and lost ones, and fails a caption frame
that is one short word on its own. ``caption_contrast`` checks each line's text
colour against the measured luminance of the backdrop behind it.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from captions.theme import MIN_CAPTION_CONTRAST, contrast_ratio, relative_luminance

from video_editor_pipelines.explainer_qa import QaCheck

#: A caption frame of one word shorter than this many letters is an orphan.
ORPHAN_LETTERS = 4


def _letters(raw: str) -> str:
    """Letters, combining marks and digits of any script (Telugu vowel signs are marks)."""
    return "".join(ch for ch in raw.lower() if unicodedata.category(ch)[0] in {"L", "M", "N"})


def _tokens(text: str) -> list[str]:
    return [token for token in (_letters(raw) for raw in text.split()) if token]


def _ends_sentence(text: str) -> bool:
    return text.rstrip().rstrip("\"'”’)]}").endswith((".", "!", "?", "…"))


def _whole_sentence(lines: Sequence[str], index: int) -> bool:
    """True when frame ``index`` is a complete sentence ("No."), not a stranded word."""
    starts = index == 0 or _ends_sentence(lines[index - 1])
    return starts and _ends_sentence(lines[index])


def caption_tokens_check(lines: Sequence[str], script: str) -> QaCheck:
    """Every caption word is a script word; no frame is a lone short word."""
    known = set(_tokens(script))
    unknown = [token for line in lines for token in _tokens(line) if token not in known]
    orphans = [
        line
        for index, line in enumerate(lines)
        if len(_tokens(line)) == 1
        and len(_tokens(line)[0]) < ORPHAN_LETTERS
        and not _whole_sentence(lines, index)
    ]
    problems: list[str] = []
    if unknown:
        problems.append("not in script: " + ", ".join(sorted(set(unknown))[:6]))
    if orphans:
        problems.append("lone short frames: " + ", ".join(repr(line) for line in orphans[:6]))
    return QaCheck(
        name="caption_tokens",
        ok=not problems,
        detail="; ".join(problems) or f"{len(lines)} caption frames match the script",
    )


def caption_contrast_check(records: Iterable[Mapping[str, Any]]) -> QaCheck:
    """Each line's text (and active word) colour clears the large-text floor.

    When a row carries a ``highlight_color`` (the active word's colour) it is
    checked over the backdrop too; a line fails if either colour is under the
    floor. Rows without the key are checked on ``font_color`` alone.
    """
    rows = list(records)
    if not rows:
        return QaCheck("caption_contrast", False, "no caption backdrop measurements")
    failures: list[str] = []
    worst = 21.0
    for row in rows:
        backdrop = float(row["backdrop"])
        ratio = contrast_ratio(relative_luminance(str(row["font_color"])), backdrop)
        worst = min(worst, ratio)
        under = [f"{ratio:.1f}:1"] if ratio < MIN_CAPTION_CONTRAST else []
        if "highlight_color" in row:
            active = contrast_ratio(relative_luminance(str(row["highlight_color"])), backdrop)
            worst = min(worst, active)
            if active < MIN_CAPTION_CONTRAST:
                under.append(f"active {active:.1f}:1")
        if under:
            failures.append(
                f"{float(row['start']):.1f}s {str(row['text'])[:24]!r} {', '.join(under)}"
            )
    detail = (
        f"{len(failures)} of {len(rows)} lines under {MIN_CAPTION_CONTRAST:.0f}:1: "
        + "; ".join(failures[:4])
        if failures
        else f"{len(rows)} lines, worst {worst:.1f}:1"
    )
    return QaCheck(name="caption_contrast", ok=not failures, detail=detail)
