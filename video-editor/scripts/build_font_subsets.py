"""Build the bundled font subsets from the upstream OFL variable fonts.

Reproducible by construction: the inputs are upstream Google Fonts files and
the character set is the committed glyph manifest, so re-running it on a fresh
checkout produces the same bytes.

    curl -fL -o /tmp/CormorantGaramond.ttf \
      'https://raw.githubusercontent.com/google/fonts/main/ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf'
    curl -fL -o /tmp/NotoSerifKR.ttf \
      'https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifkr/NotoSerifKR%5Bwght%5D.ttf'
    uv run python scripts/build_font_subsets.py \
      --cormorant /tmp/CormorantGaramond.ttf --noto /tmp/NotoSerifKR.ttf
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fontTools import subset

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "packages" / "core" / "src" / "video_editor_core" / "fonts"
GLYPHS = ROOT / "designs" / "glyphs" / "noto_serif_kr.json"

#: Printable ASCII plus the punctuation the series sets in display type.
LATIN = "".join(chr(code) for code in range(0x20, 0x7F)) + "·–—‘’“”…"


def build(source: Path, dest: Path, text: str) -> None:
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    font = subset.load_font(str(source), options)
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    subset.save_font(font, str(dest), options)
    print(f"{dest.name}: {dest.stat().st_size // 1024} KB")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cormorant", type=Path, required=True)
    parser.add_argument("--noto", type=Path, required=True)
    args = parser.parse_args()

    manifest = json.loads(GLYPHS.read_text(encoding="utf-8"))
    characters = set(manifest["characters"]) | set(LATIN)

    build(args.cormorant, FONT_DIR / "CormorantGaramond.ttf", LATIN)
    build(args.noto, FONT_DIR / "NotoSerifKR.ttf", "".join(sorted(characters)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
