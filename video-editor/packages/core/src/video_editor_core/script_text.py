"""Script-aware text helpers for letter-spaced, upper-cased labels.

Scene labels (eyebrow, subtitle, place names, badges, cards, marks) are styled
with wide ``letter-spacing`` and ``text-transform: uppercase``. That suits
Latin capitals but breaks caseless scripts: tracking pulls Indic conjuncts
apart ("ఉదయం" renders as "ఉ ద యం") and cuts Arabic joins, and uppercase is
meaningless there. Labels whose text holds such a script get the
:data:`NON_LATIN_CLASS` class, and :data:`NON_LATIN_CSS` turns both off.
"""

from __future__ import annotations

import html
import re
import unicodedata

NON_LATIN_CLASS = "ve-nl"
#: ``!important`` because scene rules are scoped (``.map-0 .map-eyebrow``) and
#: out-rank a bare class selector.
NON_LATIN_CSS = (
    f"      .{NON_LATIN_CLASS} {{ letter-spacing:0 !important; text-transform:none !important; }}\n"
)

# A leaf element: an opening tag, text with no child tags, the matching close.
_LEAF = re.compile(r"<(div|span|p)\b([^>]*)>([^<]*)</\1>")
_CLASS_ATTR = re.compile(r'\bclass="([^"]*)"')


def has_non_latin_script(text: str) -> bool:
    """True if ``text`` has a letter or mark from a script without letter case.

    Latin, Greek and Cyrillic are cased, so tracking and uppercase are safe
    there. Indic, CJK, Hangul, Arabic, Hebrew and Thai letters and combining
    marks are caseless and are the ones tracking damages.
    """
    for char in text:
        if unicodedata.category(char)[0] in "LM" and char.upper() == char.lower() == char:
            return True
    return False


def tag_non_latin_text(markup: str) -> str:
    """Add :data:`NON_LATIN_CLASS` to every leaf element whose text needs it."""

    def tag(match: re.Match[str]) -> str:
        if not has_non_latin_script(html.unescape(match.group(3))):
            return match.group(0)
        attrs = match.group(2)
        class_match = _CLASS_ATTR.search(attrs)
        if class_match is None:
            return match.group(0)
        if NON_LATIN_CLASS in class_match.group(1).split():
            return match.group(0)
        attrs = (
            attrs[: class_match.start()]
            + f'class="{class_match.group(1)} {NON_LATIN_CLASS}"'
            + attrs[class_match.end() :]
        )
        return f"<{match.group(1)}{attrs}>{match.group(3)}</{match.group(1)}>"

    return _LEAF.sub(tag, markup)
