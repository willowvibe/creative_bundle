"""Content-addressed store of rendered scene chunks (Manim's partial-movie model).

A chunk's key hashes every file in its compiled HyperFrames project (index.html,
copied assets, fonts) plus the render settings, so the key changes exactly when
what is drawn changes, and not when the chunk moves in the timeline. Entries are
re-validated before reuse, as HyperFrames does for its own segments: the file
exists, its size matches the manifest and its probed frame count matches.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_editor_ffmpeg.run import CommandError, run_subprocess

from video_editor_core.hyperframes_cli import gpu_render_profile, resolve_hyperframes_cli

RENDER_SCHEMA_VERSION = 1
MANIFEST = "manifest.json"
_BLOCK = 1 << 20
_SKIP_NAMES = {"rendered.mp4"}
_SKIP_DIRS = {"renders"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def project_hash(project_dir: Path, settings: str) -> str:
    """Hash of every input file in a compiled project, plus the render settings."""
    root = Path(project_dir)
    digest = hashlib.sha256(settings.encode("utf-8"))
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if rel.as_posix() in _SKIP_NAMES or rel.parts[0] in _SKIP_DIRS:
            continue
        digest.update(f"\n{rel.as_posix()}\n{file_sha256(path)}".encode())
    return digest.hexdigest()[:20]


@functools.cache
def _hyperframes_version(cli_path: str) -> str:
    result = run_subprocess(
        [resolve_hyperframes_cli(cli_path), "--version"],
        check=True,
        timeout=60,
    )
    return (result.stdout or "").strip()


def render_settings_key(fps: int, cli_path: str = "hyperframes") -> str:
    """Everything outside the project that changes the rendered pixels."""
    args, _ = gpu_render_profile()
    return ";".join(
        [
            f"schema={RENDER_SCHEMA_VERSION}",
            f"hyperframes={_hyperframes_version(cli_path)}",
            f"fps={fps}",
            "profile=" + " ".join(args),
        ]
    )


#: Tag for transparent PNG sequences, kept separate from the video chunk schema.
OVERLAY_SCHEMA_VERSION = "overlay-png-v1"


def overlay_render_settings_key(
    width: int, height: int, fps: int, cli_path: str = "hyperframes"
) -> str:
    """Key inputs outside a compiled overlay chunk that change its PNG frames."""
    return ";".join(
        [
            render_settings_key(fps, cli_path),
            f"overlay_schema={OVERLAY_SCHEMA_VERSION}",
            f"width={width}",
            f"height={height}",
        ]
    )


def link_or_copy(src: Path, dest: Path) -> None:
    """Hard-link ``src`` to ``dest``, copying when the filesystem refuses."""
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def count_video_frames(path: Path) -> int:
    try:
        result = run_subprocess(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_packets",
                "-show_entries",
                "stream=nb_read_packets",
                "-of",
                "csv=p=0",
                str(path),
            ],
            check=True,
            timeout=300,
        )
    except CommandError:
        return -1
    text = (result.stdout or "").strip().split(",")[0]
    return int(text) if text.isdigit() else -1


class RenderCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}.mp4"

    def _entries(self) -> dict[str, dict[str, Any]]:
        path = self.root / MANIFEST
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        entries = data.get("entries") if isinstance(data, dict) else None
        return entries if isinstance(entries, dict) else {}

    def _write(self, entries: dict[str, dict[str, Any]]) -> None:
        tmp = self.root / f"{MANIFEST}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({"version": 1, "entries": entries}, indent=2), encoding="utf-8")
        tmp.replace(self.root / MANIFEST)

    def lookup(self, key: str, frames: int) -> Path | None:
        entry = self._entries().get(key)
        path = self.path_for(key)
        if entry is None or not path.exists():
            return None
        if path.stat().st_size != entry.get("bytes") or entry.get("frames") != frames:
            return None
        if count_video_frames(path) != frames:
            return None
        return path

    def store(self, key: str, rendered: Path, *, frames: int, scene_ids: Sequence[str]) -> Path:
        dest = self.path_for(key)
        shutil.move(str(rendered), dest)
        entries = self._entries()
        entries[key] = {
            "bytes": dest.stat().st_size,
            "frames": frames,
            "scene_ids": list(scene_ids),
            "stored_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self._write(entries)
        return dest

    def prune(self, keep: set[str]) -> list[str]:
        entries = self._entries()
        removed: list[str] = []
        for key in sorted(set(entries) - keep):
            self.path_for(key).unlink(missing_ok=True)
            entries.pop(key)
            removed.append(key)
        for stray in sorted(self.root.glob("*.mp4")):
            if stray.stem not in entries:
                stray.unlink()
                removed.append(stray.stem)
        self._write(entries)
        return removed


class OverlayCache:
    """Content-addressed store of transparent PNG frame directories."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        return self.root / key

    def _entries(self) -> dict[str, dict[str, Any]]:
        path = self.root / MANIFEST
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        entries = data.get("entries") if isinstance(data, dict) else None
        return entries if isinstance(entries, dict) else {}

    def _write(self, entries: dict[str, dict[str, Any]]) -> None:
        tmp = self.root / f"{MANIFEST}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({"version": 1, "entries": entries}, indent=2), encoding="utf-8")
        tmp.replace(self.root / MANIFEST)

    def lookup(self, key: str, frames: int) -> Path | None:
        entry = self._entries().get(key)
        path = self.path_for(key)
        if entry is None or not path.is_dir() or entry.get("frames") != frames:
            return None
        if len(list(path.glob("*.png"))) != frames:
            return None
        return path

    def store(
        self,
        key: str,
        frames_dir: Path,
        *,
        frames: int,
        start: int,
        end: int,
        fps: int | None = None,
    ) -> Path:
        produced = sorted(Path(frames_dir).glob("*.png"))[:frames]
        if len(produced) != frames:
            raise ValueError(
                f"overlay cache entry {key} has {len(produced)} frames, expected {frames}"
            )
        dest = self.path_for(key)
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        for offset, png in enumerate(produced):
            link_or_copy(png, dest / f"{offset + 1:06d}.png")
        entry: dict[str, Any] = {
            "key": key,
            "frames": frames,
            "start_frame": start,
            "end_frame": end,
            "start": start,
            "end": end,
            "time_range": [start, end],
            "stored_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if fps:
            entry["start_seconds"] = round(start / fps, 6)
            entry["end_seconds"] = round(end / fps, 6)
        entries = self._entries()
        entries[key] = entry
        self._write(entries)
        return dest

    def prune(self, keep: set[str]) -> list[str]:
        entries = self._entries()
        removed: list[str] = []
        for key in sorted(set(entries) - keep):
            shutil.rmtree(self.path_for(key), ignore_errors=True)
            entries.pop(key)
            removed.append(key)
        for stray in sorted(path for path in self.root.iterdir() if path.is_dir()):
            if stray.name not in entries:
                shutil.rmtree(stray, ignore_errors=True)
                removed.append(stray.name)
        self._write(entries)
        return removed
