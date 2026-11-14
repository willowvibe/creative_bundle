"""Bundled open-licence fonts for HTML (HyperFrames) compositions.

Brand fonts are often not installed on the render machine, so text silently
falls back to whatever sans-serif Chrome finds. The families here ship with
the engine (SIL Open Font License; see ``fonts/OFL-*.txt``) and are embedded
into a composition with ``@font-face`` rules, so renders look the same on any
host. The four Latin/CJK files are variable fonts
(``CormorantGaramond.ttf`` covers weights 300-700, ``NotoSerifKR.ttf``
200-900, the others 100-900); the Devanagari file is a static Bold WOFF2
subset of the whole script block.

The committed files are subsets, not the full upstream fonts: ``Inter`` and
``Montserrat`` keep their wide Latin coverage, while the ``Noto Serif KR``
subset covers only the characters declared in
``designs/glyphs/noto_serif_kr.json`` (that is why :func:`font_stack` keeps a
system CJK face behind it).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

FONT_DIR = Path(__file__).parent / "fonts"

BUNDLED_FONTS: dict[str, str] = {
    "Inter": "Inter.ttf",
    "Montserrat": "Montserrat.ttf",
    "Cormorant Garamond": "CormorantGaramond.ttf",
    "Noto Serif KR": "NotoSerifKR.ttf",
    # The musical-video captions are Devanagari. This is a WOFF2 subset of the
    # whole U+0900–U+097F block (not a per-video glyph set), so no glyph
    # manifest is needed: every Devanagari character the lyrics can use is in
    # the file. `font_face_css` already emits `format('woff2')` for it.
    "Noto Sans Devanagari": "NotoSansDevanagari.woff2",
}

DEFAULT_FAMILY = "Inter"
_FALLBACKS = "'Inter', 'Noto Sans', 'DejaVu Sans', system-ui, sans-serif"
_CJK_FAMILIES = frozenset({"Noto Serif KR"})
#: The bundled subset covers only the declared glyphs, so a system CJK face
#: stays in the stack for anything the series has not declared yet.
_CJK_FALLBACKS = "'Noto Serif CJK KR', 'Noto Sans KR', system-ui, sans-serif"


def font_stack(family: str | None) -> str:
    """Return a CSS ``font-family`` list led by ``family`` with safe fallbacks."""
    family = (family or DEFAULT_FAMILY).strip().replace("'", "")
    if family in _CJK_FAMILIES:
        return f"'{family}', {_CJK_FALLBACKS}"
    if family == DEFAULT_FAMILY:
        return _FALLBACKS
    return f"'{family}', {_FALLBACKS}"


def font_face_css(
    families: Iterable[str],
    fonts_dir: Path,
    *,
    url_prefix: str = "assets/fonts",
) -> str:
    """Copy the bundled files for ``families`` into ``fonts_dir``; return ``@font-face`` rules.

    Families that are not bundled are skipped (they resolve via
    :func:`font_stack` fallbacks), so callers can pass whatever a brand or
    style names.
    """
    rules: list[str] = []
    for family in dict.fromkeys(f.strip() for f in families if f):
        filename = BUNDLED_FONTS.get(family)
        if filename is None:
            continue
        fonts_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FONT_DIR / filename, fonts_dir / filename)
        css_format = "woff2" if filename.endswith(".woff2") else "truetype"
        rules.append(
            f"@font-face {{ font-family: '{family}'; "
            f"src: url('{url_prefix}/{filename}') format('{css_format}'); "
            f"font-weight: 100 900; font-display: block; }}"
        )
    return "\n".join(rules)
