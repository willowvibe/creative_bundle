"""Copy media into a HyperFrames project's ``assets/`` directory."""

from __future__ import annotations

import shutil
from pathlib import Path


class AssetCopier:
    """Copy media into the project's assets dir, de-duplicating by source."""

    def __init__(self, assets_dir: Path) -> None:
        self._assets_dir = assets_dir
        self._copied: dict[str, str] = {}
        self._used_names: set[str] = set()

    def copy(self, source: str) -> str:
        """Copy ``source`` into assets and return its relative path."""
        src = Path(source)
        key = str(src.resolve()) if src.exists() else source
        if key in self._copied:
            return self._copied[key]

        name = src.name or "asset.bin"
        if name in self._used_names:
            stem, suffix = src.stem or "asset", src.suffix
            name = f"{stem}_{len(self._used_names)}{suffix}"
        self._used_names.add(name)
        dest = self._assets_dir / name
        if src.exists():
            shutil.copyfile(src, dest)
        rel = f"assets/{name}"
        self._copied[key] = rel
        return rel
