"""CJK glyph safety: every character a layer draws must be declared.

The bundled Noto Serif KR is a subset, so an undeclared character renders as a
blank box — and a wrong Hanja is the failure mode the series brief calls out.
The content pack declares each character with its reading and meaning; this
gate compares the composition's text against that table.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

GLYPH_DIR = Path(__file__).resolve().parents[4] / "designs" / "glyphs"
DEFAULT_MANIFEST = "noto_serif_kr"

#: Han (CJK unified + extension A), Hangul jamo and Hangul syllables.
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xAC00, 0xD7AF),
)


class GlyphManifestError(ValueError):
    """A glyph manifest that cannot be read or is malformed."""


def is_cjk(character: str) -> bool:
    return any(low <= ord(character) <= high for low, high in _CJK_RANGES)


def cjk_characters(text: str) -> set[str]:
    """The distinct CJK characters in ``text``."""
    return {character for character in text if is_cjk(character)}


def load_glyph_manifest(
    name: str = DEFAULT_MANIFEST, directory: Path | str | None = None
) -> dict[str, dict[str, Any]]:
    """Load ``<directory>/<name>.json`` and return its character table."""
    root = Path(directory) if directory is not None else GLYPH_DIR
    path = root / f"{name}.json"
    if not path.is_file():
        raise GlyphManifestError(f"glyph manifest {name!r} is missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GlyphManifestError(f"glyph manifest {name!r} cannot be read: {exc}") from exc
    characters = data.get("characters") if isinstance(data, dict) else None
    if not isinstance(characters, dict) or not characters:
        raise GlyphManifestError(
            f"glyph manifest {name!r} must hold a non-empty 'characters' object"
        )
    try:
        return {str(key): dict(value) for key, value in characters.items()}
    except (TypeError, ValueError) as exc:
        raise GlyphManifestError(
            f"glyph manifest {name!r} has a malformed character value: {exc}"
        ) from exc


def undeclared_glyphs(
    texts: Iterable[str], manifest: dict[str, dict[str, Any]]
) -> list[tuple[str, str]]:
    """Every (character, text) pair whose character the manifest does not declare."""
    missing: dict[str, str] = {}
    for text in texts:
        for character in cjk_characters(text):
            if character not in manifest:
                missing.setdefault(character, text)
    return [(character, missing[character]) for character in sorted(missing)]
