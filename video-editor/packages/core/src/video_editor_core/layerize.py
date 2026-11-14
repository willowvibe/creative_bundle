"""Turn one still into depth, cut-out and filled plate in an isolated env.

The engine process never imports torch. A configured command (see
``LayerizeConfig``) runs the model stack in its own environment and writes
three files; this module caches the result by source bytes and parameters,
and surfaces the subprocess' stderr instead of a traceback.

The command must understand::

    <command> --images <source> --out <dir> --process-res <N>
              --depth-model <model> --cutout <birefnet|ben2>

and write ``<stem>-depth.png``, ``<stem>-cutout.png`` and ``<stem>-plate.png``
(``scripts/spikes/layerize_spike.py`` does, and also writes a 2.5D preview).
Canonical names ``depth.png`` / ``fg.png`` / ``bg.png`` are accepted too.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from video_editor_ffmpeg.run import CommandError, run_subprocess

#: Role → the two accepted file names, the stem-suffixed one first.
_OUTPUTS = {
    "depth": ("-depth.png", "depth.png"),
    "fg": ("-cutout.png", "fg.png"),
    "bg": ("-plate.png", "bg.png"),
    "near": ("-mask-near.png", "near.png"),
}
#: Required outputs; ``near`` is the depth mask and may be absent on an older
#: CLI, so a composition falls back to one background layer.
_REQUIRED = ("depth", "fg", "bg")
#: Bumped when the output set changes, so an old cache entry cannot silently
#: satisfy a new pipeline (the near mask was added at version 2).
OUTPUT_VERSION = 2
#: Written last; a key directory without it is a killed build, not a hit (#306).
DONE_MARKER = ".done"
#: Temp dirs older than this are leftovers from a killed process.
STALE_TEMP_SECONDS = 6 * 60 * 60


def _sweep_stale_temp_dirs(parent: Path) -> None:
    """Remove ``.<key>.*`` build dirs left behind by a killed process (#306)."""
    cutoff = time.time() - STALE_TEMP_SECONDS
    try:
        candidates = list(parent.glob(".*"))
    except OSError:
        return
    for candidate in candidates:
        try:
            if candidate.is_dir() and candidate.stat().st_mtime < cutoff:
                shutil.rmtree(candidate, ignore_errors=True)
        except OSError:
            continue


class LayerizeError(RuntimeError):
    """The layerize subprocess is missing, failed, or wrote nothing usable."""


@dataclass(frozen=True)
class LayerizeResult:
    """The cached layers for one still."""

    depth: Path
    fg: Path
    bg: Path
    cached: bool
    #: The depth-derived near-background alpha mask, when the CLI wrote one.
    near: Path | None = None


def cache_key(source: Path | str, *, model: str, process_res: int, cutout: str) -> str:
    """Hash the source bytes plus the parameters that change the output."""
    data = Path(source).read_bytes()
    params = json.dumps(
        {
            "model": model,
            "process_res": process_res,
            "cutout": cutout,
            "version": OUTPUT_VERSION,
        },
        sort_keys=True,
    )
    return hashlib.sha256(data + params.encode()).hexdigest()[:16]


def _find_output(work: Path, stem: str, role: str) -> Path | None:
    for name in _OUTPUTS[role]:
        candidate = work / (f"{stem}{name}" if name.startswith("-") else name)
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def layerize_still(
    source: Path | str,
    cache_dir: Path | str,
    *,
    command: Sequence[str],
    model: str = "depth-anything/DA3MONO-LARGE",
    process_res: int = 1280,
    cutout: str = "birefnet",
    timeout: float = 600.0,
) -> LayerizeResult:
    """Return the cached (or freshly built) depth/fg/bg for ``source``."""
    if not command:
        raise LayerizeError(
            "layers: auto needs layerize.command in config.yaml (the model "
            "environment's Python); see config.example.yaml"
        )
    origin = Path(source)
    if not origin.is_file():
        raise LayerizeError(f"layerize source does not exist: {origin}")
    target = Path(cache_dir) / cache_key(
        origin, model=model, process_res=process_res, cutout=cutout
    )
    _sweep_stale_temp_dirs(target.parent)
    existing = {role: _find_output(target, origin.stem, role) for role in _REQUIRED}
    if all(existing.values()) and (target / DONE_MARKER).is_file():
        return LayerizeResult(
            depth=cast(Path, existing["depth"]),
            fg=cast(Path, existing["fg"]),
            bg=cast(Path, existing["bg"]),
            cached=True,
            near=_find_output(target, origin.stem, "near"),
        )

    # A unique temp dir per call: two jobs layerizing the same still must not
    # write over each other; the last one to finish wins the cache entry.
    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    invocation = [
        *command,
        "--images",
        str(origin),
        "--out",
        str(work),
        "--process-res",
        str(process_res),
        "--depth-model",
        model,
        "--cutout",
        cutout,
    ]
    try:
        completed = run_subprocess(invocation, timeout=timeout, check=False)
    except CommandError as exc:
        shutil.rmtree(work, ignore_errors=True)
        raise LayerizeError(f"layerize failed on {origin}: {exc}") from exc
    except OSError as exc:
        shutil.rmtree(work, ignore_errors=True)
        raise LayerizeError(f"layerize command cannot run: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-6:]
        shutil.rmtree(work, ignore_errors=True)
        raise LayerizeError(
            f"layerize failed ({completed.returncode}) on {origin}:\n" + "\n".join(detail)
        )

    found = {role: _find_output(work, origin.stem, role) for role in _REQUIRED}
    near = _find_output(work, origin.stem, "near")
    missing = [role for role, path in found.items() if path is None]
    if missing:
        shutil.rmtree(work, ignore_errors=True)
        raise LayerizeError(
            f"layerize wrote no {', '.join(missing)} for {origin}; "
            f"expected {_OUTPUTS['depth'][0]}, {_OUTPUTS['fg'][0]} and {_OUTPUTS['bg'][0]}"
        )

    target.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    for role, path in found.items():
        destination = target / {"depth": "depth.png", "fg": "fg.png", "bg": "bg.png"}[role]
        cast(Path, path).replace(destination)
        result[role] = destination
    near_path: Path | None = None
    if near is not None:
        near_path = target / "near.png"
        near.replace(near_path)
    shutil.rmtree(work, ignore_errors=True)
    # Written last: a killed build leaves outputs but no marker, and the next
    # call rebuilds instead of serving a half-written entry (#306).
    (target / DONE_MARKER).write_text(f"v{OUTPUT_VERSION}\n", encoding="utf-8")
    return LayerizeResult(
        depth=result["depth"],
        fg=result["fg"],
        bg=result["bg"],
        cached=False,
        near=near_path,
    )
