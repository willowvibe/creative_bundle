"""Bundled open-licence fonts for HTML (HyperFrames) compositions.

Brand fonts are often not installed on the render machine, so text silently
falls back to whatever sans-serif Chrome finds. The families here ship with
the engine (SIL Open Font License; see ``fonts/OFL-*.txt``) and are embedded
into a composition with ``@font-face`` rules, so renders look the same on any
host. The four Latin/CJK files are variable fonts
(``CormorantGaramond.ttf`` covers weights 300-700, ``NotoSerifKR.ttf``
200-900, the others 100-900); the Devanagari file is a static Bold WOFF2
subset of the whole script block; Noto Sans Telugu is two static files
(Regular, Bold) of the whole Telugu block.

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
    # Telugu explainers: static Regular + Bold (see `_STATIC_FACES`), full block.
    "Noto Sans Telugu": "NotoSansTelugu-Regular.ttf",
}

#: Families that ship as static files, one per weight band, instead of a
#: single variable file: ``(filename, CSS font-weight range)``. Chrome picks
#: the face whose range holds the requested weight, so weight 800 captions
#: land on Bold rather than a synthesised bold.
_STATIC_FACES: dict[str, tuple[tuple[str, str], ...]] = {
    "Noto Sans Telugu": (
        ("NotoSansTelugu-Regular.ttf", "100 599"),
        ("NotoSansTelugu-Bold.ttf", "600 900"),
    ),
}

#: Indic faces every composition embeds. No Latin face has these scripts, and
#: HyperFrames rewrites ``Noto Sans``/``DejaVu Sans`` in a stack to its own
#: Latin-only Inter, so relying on the render host's system fonts for Telugu
#: or Devanagari text is not safe: embed them whenever fonts are embedded.
INDIC_FALLBACK_FAMILIES: tuple[str, ...] = ("Noto Sans Telugu", "Noto Sans Devanagari")

DEFAULT_FAMILY = "Inter"
_INDIC_STACK = ", ".join(f"'{family}'" for family in INDIC_FALLBACK_FAMILIES)
_FALLBACKS = f"'Inter', 'Noto Sans', 'DejaVu Sans', {_INDIC_STACK}, system-ui, sans-serif"
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


def with_indic_fallbacks(families: Iterable[str]) -> list[str]:
    """Return ``families`` plus the bundled Indic faces (no duplicates)."""
    return list(dict.fromkeys([*(f for f in families if f), *INDIC_FALLBACK_FAMILIES]))


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
        faces = _STATIC_FACES.get(family) or ((filename, "100 900"),)
        for face_file, weights in faces:
            shutil.copyfile(FONT_DIR / face_file, fonts_dir / face_file)
            css_format = "woff2" if face_file.endswith(".woff2") else "truetype"
            rules.append(
                f"@font-face {{ font-family: '{family}'; "
                f"src: url('{url_prefix}/{face_file}') format('{css_format}'); "
                f"font-weight: {weights}; font-display: block; }}"
            )
    return "\n".join(rules)
