"""Shared caption-token rules for line grouping and the caption QA gate.

The builder that groups words into burnt caption lines and ``caption_tokens``
must agree on what counts as a stranded short word, otherwise the builder can
emit lines the gate rejects.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

#: A caption frame of one word shorter than this many letters is an orphan.
ORPHAN_LETTERS = 4


def letters(raw: str) -> str:
    """Letters, combining marks and digits of any script (Telugu vowel signs are marks)."""
    return "".join(ch for ch in raw.lower() if unicodedata.category(ch)[0] in {"L", "M", "N"})


def tokens(text: str) -> list[str]:
    return [token for token in (letters(raw) for raw in text.split()) if token]


def ends_sentence(text: str) -> bool:
    """True when ``text`` closes a sentence, ignoring trailing quotes/brackets."""
    return text.rstrip().rstrip("\"'”’)]}").endswith((".", "!", "?", "…"))


def whole_sentence(lines: Sequence[str], index: int) -> bool:
    """True when frame ``index`` is a complete sentence ("No."), not a stranded word."""
    starts = index == 0 or ends_sentence(lines[index - 1])
    return starts and ends_sentence(lines[index])


def is_orphan(lines: Sequence[str], index: int) -> bool:
    """True when frame ``index`` is one short token that is not a whole sentence."""
    parts = tokens(lines[index])
    return (
        len(parts) == 1
        and len(parts[0]) < ORPHAN_LETTERS
        and not whole_sentence(lines, index)
    )
