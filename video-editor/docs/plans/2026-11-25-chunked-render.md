# Chunked scene render with a per-scene cache — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to carry this out task by task. In this repo, code changes go to the
> cheapag `delegate` tool: `delegate(plan: "docs/plans/2026-11-25-chunked-render.md", steps: "1")`.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fixing one scene of a `ve explain` video re-renders only that scene's chunk, not the whole video.

**Architecture:** Build the full `Composition` once, so state that carries across scenes, such as the
tally chip's "from" value and scene-transition exits, is already resolved. Then cut it at scene boundaries
into chunks of about 45 s, rebase each chunk to t=0 and compile it to its own HyperFrames project. Hash
every file in that project plus the render settings, reuse a cached MP4 when the hash matches, and render
only the misses. Join the chunks with an ffmpeg stream copy, mix the composition's audio cues once,
and mux. Design and research: `docs/plans/2026-11-25-chunked-render-concept.md`.

**Tech stack:** Python 3.12, pydantic v2 models in `video_editor_core.composition`, the HyperFrames
CLI 0.8.216 through `video_editor_core.hyperframes_cli.render_project`, ffmpeg/ffprobe, pytest.

**Global constraints**

- Cut only at scene boundaries, and never where any layer is on screen on both sides of the cut.
- Chunks are video only (`audio_mode="external"`, `audio=[]`). Audio is mixed once over the joined video
  and never chunked.
- Every chunk renders with the same settings: `render_project` and its automatic NVIDIA profile
  (`--gpu --browser-gpu --workers 6`), one chunk at a time.
- A chunk's frame count is `round(quantise(duration, fps) * fps)`, the formula `overlay_burn` already
  checks HyperFrames output with. The joined video must have exactly `frame_count(composition.duration, fps)` frames.
- The cache lives at `<job work dir>/render_cache/` (the sibling of `composition/`), with
  `manifest.json` and `<key>.mp4` files.
- Switch: environment variable `VE_RENDER_CHUNKS` (`1/on/true/yes` or `0/off/false/no`). It defaults to off
  until T6's measured run passes, and T6 flips it on.
- Processes: never import `subprocess` in package code (ruff TID251). Use `video_editor_ffmpeg.run.run_subprocess(cmd, timeout=..., check=...)`, which raises `CommandError` (with `.stderr`) on failure or timeout. Tests may use `subprocess`.
- Run single test files only: `.venv/bin/pytest tests/<file> -q`. Lint with `.venv/bin/ruff check <files>`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

**Out of scope (later plans):** caching caption-overlay chunks (`overlay_burn`), a `ve cache` CLI, and
parallel footage search.

**File map**

| File | Responsibility |
|---|---|
| `packages/core/src/video_editor_core/scene_chunks.py` (new) | Where a cut is allowed, grouping scenes into chunks, slicing and rebasing a chunk |
| `packages/core/src/video_editor_core/chunk_cache.py` (new) | Project hash, settings key, frame probe, the cache store with its manifest and prune |
| `packages/core/src/video_editor_core/chunk_media.py` (new) | ffmpeg concat, audio-cue mix, mux |
| `packages/core/src/video_editor_core/chunked_render.py` (new) | Runs the pipeline, the env switch and the report |
| `packages/core/src/video_editor_core/scene_plan_composition.py` (modify) | `render_scene_plan` takes the chunked path when switched on |
| `tests/test_scene_chunks.py`, `tests/test_chunk_cache.py`, `tests/test_chunk_media.py`, `tests/test_chunked_render.py` (new); `tests/test_scene_plan_composition.py` (modify) | Tests |
| `docs/RENDERING.md`, `docs/plans/2026-11-25-chunked-render-concept.md` (modify in T6) | Measured numbers, status |

---

## 1 Scene chunk planner and slicer
files: packages/core/src/video_editor_core/scene_chunks.py, tests/test_scene_chunks.py
do: Add scene_chunks.py with ChunkSpan, frame_count, cut_points, plan_scene_chunks and slice_composition exactly as written in this task's steps, test-first.
done-when: `PYTHONPATH=packages/core/src /mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/pytest tests/test_scene_chunks.py -q` and `/mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/ruff check packages/core/src/video_editor_core/scene_chunks.py tests/test_scene_chunks.py`
difficulty: medium
kind: change

**Interfaces**
- Consumes: `Composition`, `CompositionScene`, `CompositionLayer`, `quantise` from `video_editor_core.composition`.
- Produces:
  - `frame_count(seconds: float, fps: int) -> int`
  - `ChunkSpan` (frozen dataclass: `index: int`, `scene_ids: tuple[str, ...]`, `start_frame: int`,
    `end_frame: int`, `fps: int`; properties `frames`, `start`, `end`, `duration`)
  - `cut_points(composition: Composition) -> set[int]`
  - `plan_scene_chunks(composition: Composition, target_seconds: float = 45.0, min_seconds: float = 12.0) -> list[ChunkSpan]`
  - `slice_composition(composition: Composition, span: ChunkSpan) -> Composition`

- [ ] **Step 1: Write the failing tests** in `tests/test_scene_chunks.py`

```python
"""Tests for cutting a composition into scene-aligned chunks."""

from __future__ import annotations

import pytest
from video_editor_core.composition import (
    AudioCue,
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
)
from video_editor_core.render_graph import Rect
from video_editor_core.scene_chunks import (
    ChunkSpan,
    cut_points,
    frame_count,
    plan_scene_chunks,
    slice_composition,
)


def _layer(layer_id: str, start: float, end: float | None) -> CompositionLayer:
    return CompositionLayer(
        id=layer_id,
        kind="text",
        rect=Rect(x=0, y=0, width=100, height=100),
        time=LayerTime(start=start, end=end),
        content=LayerContent(text=layer_id),
    )


def _comp(
    durations: list[float],
    extra: dict[int, list[CompositionLayer]] | None = None,
    fps: int = 30,
) -> Composition:
    scenes: list[CompositionScene] = []
    t = 0.0
    for i, d in enumerate(durations):
        end = round(t + d, 6)
        layers = [_layer(f"s{i}_text", t, end), *((extra or {}).get(i, []))]
        scenes.append(CompositionScene(id=f"s{i}", start=t, end=end, layers=layers))
        t = end
    return Composition(
        job_id="job",
        canvas=Canvas(width=1920, height=1080, fps=fps),
        duration=t,
        scenes=scenes,
        audio=[AudioCue(id="narration", source="/n.mp3", start=0.0)],
    )


def test_frame_count_uses_the_frame_grid() -> None:
    assert frame_count(3.0, 30) == 90
    assert frame_count(1.01, 30) == 30


def test_every_scene_boundary_is_a_cut_when_layers_stay_inside() -> None:
    assert cut_points(_comp([10, 10, 10])) == {1, 2}


def test_a_layer_across_a_boundary_blocks_that_cut() -> None:
    comp = _comp([10, 10, 10], extra={1: [_layer("wide", 10.0, 25.0)]})
    assert cut_points(comp) == {1}


def test_an_open_ended_layer_blocks_every_later_cut() -> None:
    comp = _comp([10, 10, 10], extra={0: [_layer("forever", 0.0, None)]})
    assert cut_points(comp) == set()


def test_scenes_group_to_the_target_and_a_short_tail_merges() -> None:
    spans = plan_scene_chunks(_comp([10, 10, 10, 10, 10]), target_seconds=20, min_seconds=12)
    assert [span.scene_ids for span in spans] == [("s0", "s1"), ("s2", "s3", "s4")]
    assert [span.index for span in spans] == [0, 1]
    assert spans[1].start_frame == 600 and spans[1].end_frame == 1500


def test_chunk_frames_add_up_to_the_whole_video_off_grid() -> None:
    comp = _comp([1.01, 1.02, 0.97])
    spans = plan_scene_chunks(comp, target_seconds=1.0, min_seconds=0.5)
    assert len(spans) == 3
    assert sum(span.frames for span in spans) == frame_count(comp.duration, 30)
    assert spans[0].end_frame == spans[1].start_frame


def test_slice_rebases_to_zero_and_drops_audio() -> None:
    comp = _comp([10, 10, 10])
    span = ChunkSpan(index=1, scene_ids=("s1", "s2"), start_frame=300, end_frame=900, fps=30)
    sliced = slice_composition(comp, span)
    assert sliced.duration == pytest.approx(20.0)
    assert [(s.id, s.start, s.end) for s in sliced.scenes] == [("s1", 0.0, 10.0), ("s2", 10.0, 20.0)]
    layer = sliced.scenes[1].layers[0]
    assert (layer.time.start, layer.time.end) == (10.0, 20.0)
    assert sliced.audio == [] and sliced.audio_mode == "external"


def test_the_same_scenes_slice_identically_at_any_offset() -> None:
    early = _comp([10, 10])
    late = _comp([5, 10, 10])
    # s0 of ``early`` and s2 of ``late`` (its own chunk) are both a 10 s scene with one
    # text layer; the layer ids differ by index, so compare times only.
    a = slice_composition(early, plan_scene_chunks(early, target_seconds=10, min_seconds=1)[0])
    b_spans = plan_scene_chunks(late, target_seconds=10, min_seconds=1)
    b = slice_composition(late, b_spans[1])
    assert [(s.start, s.end) for s in a.scenes] == [(s.start, s.end) for s in b.scenes]
    assert a.scenes[0].layers[0].time == b.scenes[0].layers[0].time
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/pytest tests/test_scene_chunks.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'video_editor_core.scene_chunks'`.

- [ ] **Step 3: Implement** `packages/core/src/video_editor_core/scene_chunks.py`

```python
"""Cut a compiled composition into scene-aligned chunks and slice each one out.

A chunk is a run of whole scenes, cut only where no layer is on screen on both
sides. ``slice_composition`` shifts a chunk to start at 0 and drops the audio,
so the same scenes give the same chunk project wherever they sit in the
timeline (the render-cache key relies on that). Apply
``scene_transitions.apply_scene_transitions`` before planning: a transition's
exit motion lives on the previous scene's layers and must already be baked in.
"""

from __future__ import annotations

from dataclasses import dataclass

from video_editor_core.composition import Composition, CompositionLayer, quantise

DEFAULT_TARGET_SECONDS = 45.0
DEFAULT_MIN_SECONDS = 12.0
_EPS = 1e-6


def frame_count(seconds: float, fps: int) -> int:
    """Frames HyperFrames renders for ``seconds`` (the overlay_burn formula)."""
    return round(quantise(seconds, fps) * fps)


@dataclass(frozen=True)
class ChunkSpan:
    index: int
    scene_ids: tuple[str, ...]
    start_frame: int
    end_frame: int
    fps: int

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def start(self) -> float:
        return self.start_frame / self.fps

    @property
    def end(self) -> float:
        return self.end_frame / self.fps

    @property
    def duration(self) -> float:
        return self.frames / self.fps


def _straddles(layer: CompositionLayer, t: float, duration: float) -> bool:
    end = duration if layer.time.end is None else layer.time.end
    return layer.time.start < t - _EPS and end > t + _EPS


def cut_points(composition: Composition) -> set[int]:
    """Scene indexes ``i`` where cutting between scene ``i-1`` and ``i`` splits no layer."""
    scenes = composition.scenes
    layers = [layer for scene in scenes for layer in scene.layers]
    allowed: set[int] = set()
    for i in range(1, len(scenes)):
        t = scenes[i].start
        if not any(_straddles(layer, t, composition.duration) for layer in layers):
            allowed.add(i)
    return allowed


def plan_scene_chunks(
    composition: Composition,
    target_seconds: float = DEFAULT_TARGET_SECONDS,
    min_seconds: float = DEFAULT_MIN_SECONDS,
) -> list[ChunkSpan]:
    """Group scenes into chunks of about ``target_seconds``; merge a short tail."""
    scenes = composition.scenes
    if not scenes:
        raise ValueError("composition has no scenes")
    fps = composition.canvas.fps
    cuts = cut_points(composition)
    groups: list[list[int]] = [[0]]
    for i in range(1, len(scenes)):
        current = groups[-1]
        length = scenes[current[-1]].end - scenes[current[0]].start
        if i in cuts and length >= target_seconds - _EPS:
            groups.append([i])
        else:
            current.append(i)
    if len(groups) > 1:
        tail = groups[-1]
        if scenes[tail[-1]].end - scenes[tail[0]].start < min_seconds - _EPS:
            groups.pop()
            groups[-1].extend(tail)
    total = frame_count(composition.duration, fps)
    spans: list[ChunkSpan] = []
    for n, group in enumerate(groups):
        start_frame = 0 if n == 0 else round(scenes[group[0]].start * fps)
        end_frame = total if n == len(groups) - 1 else round(scenes[group[-1]].end * fps)
        spans.append(
            ChunkSpan(
                index=n,
                scene_ids=tuple(scenes[i].id for i in group),
                start_frame=start_frame,
                end_frame=end_frame,
                fps=fps,
            )
        )
    return spans


def _clamp(t: float, length: float) -> float:
    return min(max(round(t, 6), 0.0), length)


def _shift_layer(layer: CompositionLayer, offset: float, length: float, fps: int) -> CompositionLayer:
    start = _clamp(layer.time.start - offset, length)
    end = None if layer.time.end is None else _clamp(layer.time.end - offset, length)
    if end is not None and end <= start + _EPS:
        end = min(length, start + 1.0 / fps)
    time = layer.time.model_copy(update={"start": start, "end": end})
    return layer.model_copy(update={"time": time})


def slice_composition(composition: Composition, span: ChunkSpan) -> Composition:
    """The chunk's scenes as their own composition: rebased to 0, frame-exact, video only."""
    wanted = set(span.scene_ids)
    picked = [scene for scene in composition.scenes if scene.id in wanted]
    if [scene.id for scene in picked] != list(span.scene_ids):
        raise ValueError(f"chunk {span.index}: scenes {span.scene_ids} not found in order")
    offset = span.start
    length = span.duration
    fps = composition.canvas.fps
    scenes = []
    for n, scene in enumerate(picked):
        start = 0.0 if n == 0 else _clamp(scene.start - offset, length)
        end = length if n == len(picked) - 1 else _clamp(scene.end - offset, length)
        layers = [_shift_layer(layer, offset, length, fps) for layer in scene.layers]
        scenes.append(scene.model_copy(update={"start": start, "end": end, "layers": layers}))
    sliced = composition.model_copy(
        update={"scenes": scenes, "duration": length, "audio": [], "audio_mode": "external"}
    )
    # model_copy skips validation; re-validate so a bad slice fails here, not in Chrome.
    return Composition.model_validate(sliced.model_dump())
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/bin/pytest tests/test_scene_chunks.py -q && .venv/bin/ruff check packages/core/src/video_editor_core/scene_chunks.py tests/test_scene_chunks.py`
Expected: `8 passed`, ruff `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/video_editor_core/scene_chunks.py tests/test_scene_chunks.py
git commit -m "render: scene chunk planner and rebasing slicer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## 2 Render cache (project hash, settings key, manifest store)
files: packages/core/src/video_editor_core/chunk_cache.py, tests/test_chunk_cache.py
do: Add chunk_cache.py with file_sha256, project_hash, render_settings_key, count_video_frames and RenderCache (lookup, store, prune) exactly as written in this task's steps, test-first.
done-when: `PYTHONPATH=packages/core/src /mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/pytest tests/test_chunk_cache.py -q` and `/mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/ruff check packages/core/src/video_editor_core/chunk_cache.py tests/test_chunk_cache.py`
difficulty: medium
kind: change

**Interfaces**
- Consumes: `gpu_render_profile`, `resolve_hyperframes_cli` from `video_editor_core.hyperframes_cli`.
- Produces:
  - `project_hash(project_dir: Path, settings: str) -> str` (20 hex chars)
  - `render_settings_key(fps: int, cli_path: str = "hyperframes") -> str`
  - `count_video_frames(path: Path) -> int`
  - `RenderCache(root: Path)` with `.lookup(key: str, frames: int) -> Path | None`,
    `.store(key: str, rendered: Path, *, frames: int, scene_ids: Sequence[str]) -> Path` (moves the file in),
    `.prune(keep: set[str]) -> list[str]`
  - `RENDER_SCHEMA_VERSION = 1`. Bump it whenever the compiler's output meaning changes without the HTML
    changing.

- [ ] **Step 1: Write the failing tests** in `tests/test_chunk_cache.py`

```python
"""Tests for the content-addressed chunk render cache."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from video_editor_core import chunk_cache
from video_editor_core.chunk_cache import (
    RenderCache,
    count_video_frames,
    project_hash,
    render_settings_key,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _clip(path: Path, frames: int) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30",
         "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def _project(root: Path, html: str = "<html>a</html>") -> Path:
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(html, encoding="utf-8")
    (root / "assets" / "still.png").write_bytes(b"png-bytes")
    return root


def test_hash_is_stable_and_ignores_location(tmp_path: Path) -> None:
    a = project_hash(_project(tmp_path / "a"), "s")
    b = project_hash(_project(tmp_path / "b"), "s")
    assert a == b and len(a) == 20


def test_hash_changes_with_content_asset_or_settings(tmp_path: Path) -> None:
    base = project_hash(_project(tmp_path / "a"), "s")
    assert project_hash(_project(tmp_path / "b", "<html>b</html>"), "s") != base
    c = _project(tmp_path / "c")
    (c / "assets" / "still.png").write_bytes(b"other")
    assert project_hash(c, "s") != base
    assert project_hash(_project(tmp_path / "d"), "t") != base


def test_hash_skips_render_outputs(tmp_path: Path) -> None:
    project = _project(tmp_path / "a")
    before = project_hash(project, "s")
    (project / "rendered.mp4").write_bytes(b"x")
    (project / "renders").mkdir()
    (project / "renders" / "seg.mp4").write_bytes(b"y")
    assert project_hash(project, "s") == before


def test_settings_key_names_version_fps_and_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VE_RENDER_GPU", "0")
    monkeypatch.setattr(chunk_cache, "_hyperframes_version", lambda cli: "0.8.216")
    key = render_settings_key(30)
    assert "hyperframes=0.8.216" in key and "fps=30" in key and "schema=1" in key


@needs_ffmpeg
def test_store_then_lookup_validates_frames(tmp_path: Path) -> None:
    cache = RenderCache(tmp_path / "cache")
    stored = cache.store("k1", _clip(tmp_path / "r.mp4", 15), frames=15, scene_ids=["s0"])
    assert stored == tmp_path / "cache" / "k1.mp4"
    assert count_video_frames(stored) == 15
    assert cache.lookup("k1", 15) == stored
    assert cache.lookup("k1", 16) is None
    assert cache.lookup("missing", 15) is None


@needs_ffmpeg
def test_lookup_rejects_a_changed_file(tmp_path: Path) -> None:
    cache = RenderCache(tmp_path / "cache")
    stored = cache.store("k1", _clip(tmp_path / "r.mp4", 15), frames=15, scene_ids=["s0"])
    stored.write_bytes(b"truncated")
    assert cache.lookup("k1", 15) is None


@needs_ffmpeg
def test_prune_keeps_only_named_keys(tmp_path: Path) -> None:
    cache = RenderCache(tmp_path / "cache")
    cache.store("keep", _clip(tmp_path / "a.mp4", 5), frames=5, scene_ids=["s0"])
    cache.store("drop", _clip(tmp_path / "b.mp4", 5), frames=5, scene_ids=["s1"])
    (tmp_path / "cache" / "stray.mp4").write_bytes(b"z")
    removed = cache.prune({"keep"})
    assert sorted(removed) == ["drop", "stray"]
    assert sorted(p.name for p in (tmp_path / "cache").glob("*.mp4")) == ["keep.mp4"]
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/pytest tests/test_chunk_cache.py -q`
Expected: `ModuleNotFoundError: No module named 'video_editor_core.chunk_cache'`.

- [ ] **Step 3: Implement** `packages/core/src/video_editor_core/chunk_cache.py`

```python
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
import subprocess
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    result = subprocess.run(
        [resolve_hyperframes_cli(cli_path), "--version"],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return result.stdout.strip()


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


def count_video_frames(path: Path) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=300,
    )
    text = result.stdout.strip().split(",")[0] if result.returncode == 0 else ""
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
            "stored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/bin/pytest tests/test_chunk_cache.py -q && .venv/bin/ruff check packages/core/src/video_editor_core/chunk_cache.py tests/test_chunk_cache.py`
Expected: `7 passed`, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/video_editor_core/chunk_cache.py tests/test_chunk_cache.py
git commit -m "render: content-addressed chunk cache with manifest validation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## 3 Chunk media: concat, audio-cue mix, mux
files: packages/core/src/video_editor_core/chunk_media.py, tests/test_chunk_media.py
do: Add chunk_media.py with ChunkMediaError, concat_videos, mix_audio_cues and mux_audio exactly as written in this task's steps, test-first, using real ffmpeg on lavfi test clips.
done-when: `PYTHONPATH=packages/core/src /mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/pytest tests/test_chunk_media.py -q` and `/mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/ruff check packages/core/src/video_editor_core/chunk_media.py tests/test_chunk_media.py`
difficulty: medium
kind: change

**Interfaces**
- Consumes: `AudioCue` from `video_editor_core.composition`; `count_video_frames` from `video_editor_core.chunk_cache` (tests only).
- Produces:
  - `concat_videos(parts: Sequence[Path], output: Path) -> Path` (video stream only, stream copy)
  - `mix_audio_cues(cues: Sequence[AudioCue], duration: float, output: Path) -> Path` (48 kHz stereo
    PCM WAV, exactly `duration` long)
  - `mux_audio(video: Path, audio: Path, output: Path) -> Path` (copies video, AAC 192k audio)
  - `ChunkMediaError(RuntimeError)`

Cue semantics match `compile_composition._audio_html`: a cue plays from `start` for `duration` seconds
(the rest of the video if `None`), scaled by `volume`, and cues are summed without normalising.

- [ ] **Step 1: Write the failing tests** in `tests/test_chunk_media.py`

```python
"""Tests for joining chunk videos and mixing audio cues with ffmpeg."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from video_editor_core.chunk_media import concat_videos, mix_audio_cues, mux_audio
from video_editor_core.composition import AudioCue
from video_editor_core.chunk_cache import count_video_frames

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _clip(path: Path, frames: int) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30",
         "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def _tone(path: Path, seconds: float) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         str(path)],
        check=True,
    )
    return path


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(out.strip())


def _max_volume(path: Path, start: float, length: float) -> float:
    err = subprocess.run(
        ["ffmpeg", "-v", "info", "-ss", str(start), "-t", str(length), "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    match = re.search(r"max_volume: (-?[\d.]+|-inf) dB", err)
    assert match, err
    return float("-inf") if match.group(1) == "-inf" else float(match.group(1))


def test_concat_joins_frames_exactly(tmp_path: Path) -> None:
    out = concat_videos([_clip(tmp_path / "a.mp4", 45), _clip(tmp_path / "b.mp4", 30)], tmp_path / "j.mp4")
    assert count_video_frames(out) == 75


def test_mix_places_cues_and_pads_to_duration(tmp_path: Path) -> None:
    a = AudioCue(id="a", source=str(_tone(tmp_path / "a.wav", 1.0)), start=0.0, duration=1.0)
    b = AudioCue(id="b", source=str(_tone(tmp_path / "b.wav", 1.0)), start=1.5, duration=1.0)
    out = mix_audio_cues([a, b], 3.0, tmp_path / "mix.wav")
    assert _duration(out) == pytest.approx(3.0, abs=0.01)
    assert _max_volume(out, 1.1, 0.3) < -60
    assert _max_volume(out, 1.6, 0.3) > -30  # lavfi sine peaks at -18 dBFS; mono→stereo costs 3 dB


def test_mux_keeps_video_frames_and_adds_audio(tmp_path: Path) -> None:
    video = _clip(tmp_path / "v.mp4", 60)
    audio = mix_audio_cues(
        [AudioCue(id="a", source=str(_tone(tmp_path / "t.wav", 2.0)), start=0.0)], 2.0, tmp_path / "m.wav"
    )
    out = mux_audio(video, audio, tmp_path / "out.mp4")
    assert count_video_frames(out) == 60
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(streams) == ["audio", "video"]
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/pytest tests/test_chunk_media.py -q`
Expected: `ModuleNotFoundError: No module named 'video_editor_core.chunk_media'`.

- [ ] **Step 3: Implement** `packages/core/src/video_editor_core/chunk_media.py`

```python
"""ffmpeg steps of the chunked render: join chunk videos, mix audio cues, mux.

Audio is never chunked (AAC frames only join cleanly at 1,024-sample
boundaries; see the concept doc), so the composition's cues are mixed once
over the whole timeline and muxed onto the joined video.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from video_editor_core.composition import AudioCue


class ChunkMediaError(RuntimeError):
    pass


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ChunkMediaError(f"{' '.join(cmd[:4])}... failed\nstderr: {result.stderr[-2000:]}")


def concat_videos(parts: Sequence[Path], output: Path) -> Path:
    """Join same-encoded chunk videos by stream copy (the video stream only)."""
    if not parts:
        raise ChunkMediaError("no chunks to join")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    listing = output.with_suffix(".concat.txt")
    lines = []
    for part in parts:
        escaped = str(Path(part).resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
          "-map", "0:v:0", "-c", "copy", "-movflags", "+faststart", str(output)])
    return output


def mix_audio_cues(cues: Sequence[AudioCue], duration: float, output: Path) -> Path:
    """Mix cues as the HyperFrames page plays them: placed, trimmed, scaled, summed."""
    if not cues:
        raise ChunkMediaError("no audio cues to mix")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error"]
    filters: list[str] = []
    labels: list[str] = []
    for i, cue in enumerate(cues):
        cmd += ["-i", cue.source]
        length = max(0.0, duration - cue.start) if cue.duration is None else cue.duration
        delay = round(cue.start * 1000)
        filters.append(
            f"[{i}:a]atrim=0:{length:.3f},asetpts=PTS-STARTPTS,"
            f"volume={cue.volume:.4f},adelay={delay}:all=1[a{i}]"
        )
        labels.append(f"[a{i}]")
    filters.append(
        f"{''.join(labels)}amix=inputs={len(cues)}:normalize=0:dropout_transition=0,"
        f"apad,atrim=0:{duration:.3f}[out]"
    )
    cmd += ["-filter_complex", ";".join(filters), "-map", "[out]",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output)]
    _run(cmd)
    return output


def mux_audio(video: Path, audio: Path, output: Path) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(audio),
          "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
          "-shortest", "-movflags", "+faststart", str(output)])
    return output
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/bin/pytest tests/test_chunk_media.py -q && .venv/bin/ruff check packages/core/src/video_editor_core/chunk_media.py tests/test_chunk_media.py`
Expected: `3 passed`, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/video_editor_core/chunk_media.py tests/test_chunk_media.py
git commit -m "render: ffmpeg concat, audio-cue mix and mux for chunked renders

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## 4 Chunked render orchestrator
files: packages/core/src/video_editor_core/chunked_render.py, tests/test_chunked_render.py
needs: 1, 2, 3
do: Add chunked_render.py with chunked_render_enabled, ChunkResult, ChunkedRenderReport, ChunkedRenderError, _render_chunk and render_composition_chunked exactly as written in this task's steps, test-first; tests replace _render_chunk with a fake that writes a lavfi clip of the requested frame count.
done-when: `PYTHONPATH=packages/core/src /mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/pytest tests/test_chunked_render.py -q` and `/mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/ruff check packages/core/src/video_editor_core/chunked_render.py tests/test_chunked_render.py`
difficulty: medium
kind: change

**Interfaces**
- Consumes:
  - from T1: `plan_scene_chunks`, `slice_composition`, `frame_count`, `DEFAULT_TARGET_SECONDS`, `DEFAULT_MIN_SECONDS`
  - from T2: `RenderCache`, `project_hash`, `render_settings_key`, `count_video_frames`
  - from T3: `concat_videos`, `mix_audio_cues`, `mux_audio`
  - `compile_composition`, `apply_scene_transitions`, `hyperframes_cli.render_project`
- Produces:
  - `chunked_render_enabled() -> bool` (reads `VE_RENDER_CHUNKS`; default `CHUNKED_DEFAULT = False`)
  - `render_composition_chunked(composition: Composition, output_path: Path, work_dir: Path, *, cache_dir: Path, cli_path: str = "hyperframes", timeout: int = 1800, target_seconds: float = DEFAULT_TARGET_SECONDS, min_seconds: float = DEFAULT_MIN_SECONDS) -> ChunkedRenderReport`
  - `ChunkedRenderReport` (`output: Path`, `total_frames: int`, `chunks: list[ChunkResult]`,
    properties `rendered`, `reused`, method `to_json() -> dict`); `ChunkResult` (`index`, `scene_ids`,
    `key`, `cached`, `frames`, `seconds`)
  - It writes `work_dir/chunked_render.json` (the report).

- [ ] **Step 1: Write the failing tests** in `tests/test_chunked_render.py`

```python
"""Tests for the chunked scene render with a per-chunk cache."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from video_editor_core import chunk_cache, chunked_render
from video_editor_core.chunked_render import chunked_render_enabled, render_composition_chunked
from video_editor_core.composition import (
    AudioCue,
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
)
from video_editor_core.chunk_cache import count_video_frames
from video_editor_core.render_graph import Rect

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _clip(path: Path, frames: int) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30",
         "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


@pytest.fixture
def renders(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Replace the HyperFrames call with a clip of the requested length; record each call."""
    calls: list[Path] = []
    monkeypatch.setenv("VE_RENDER_GPU", "0")
    monkeypatch.setattr(chunk_cache, "_hyperframes_version", lambda cli: "test")

    def fake(project: Path, output: Path, frames: int, cli_path: str, timeout: int) -> None:
        calls.append(project)
        _clip(output, frames)

    monkeypatch.setattr(chunked_render, "_render_chunk", fake)
    return calls


def _tone(path: Path, seconds: float) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         str(path)],
        check=True,
    )
    return path


def _comp(tmp_path: Path, texts: list[str]) -> Composition:
    scenes = []
    for i, text in enumerate(texts):
        layer = CompositionLayer(
            id=f"{text}_layer",
            kind="text",
            rect=Rect(x=0, y=0, width=200, height=100),
            time=LayerTime(start=float(i), end=float(i + 1)),
            content=LayerContent(text=text),
        )
        scenes.append(CompositionScene(id=text, start=float(i), end=float(i + 1), layers=[layer]))
    tone = _tone(tmp_path / "narration.wav", float(len(texts)))
    return Composition(
        job_id="job",
        canvas=Canvas(width=320, height=180, fps=30),
        duration=float(len(texts)),
        scenes=scenes,
        audio=[AudioCue(id="narration", source=str(tone), start=0.0)],
    )


def _run(tmp_path: Path, comp: Composition):
    return render_composition_chunked(
        comp, tmp_path / "out.mp4", tmp_path / "work", cache_dir=tmp_path / "cache",
        target_seconds=1.0, min_seconds=0.5,
    )


def test_first_render_renders_every_chunk_and_muxes_audio(tmp_path: Path, renders: list[Path]) -> None:
    report = _run(tmp_path, _comp(tmp_path, ["a", "b", "c"]))
    assert len(renders) == 3 and report.rendered == 3 and report.reused == 0
    assert count_video_frames(tmp_path / "out.mp4") == 90
    data = json.loads((tmp_path / "work" / "chunked_render.json").read_text())
    assert [c["scene_ids"] for c in data["chunks"]] == [["a"], ["b"], ["c"]]


def test_unchanged_rerun_renders_nothing(tmp_path: Path, renders: list[Path]) -> None:
    _run(tmp_path, _comp(tmp_path, ["a", "b", "c"]))
    renders.clear()
    report = _run(tmp_path, _comp(tmp_path, ["a", "b", "c"]))
    assert renders == [] and report.reused == 3


def test_one_changed_scene_renders_one_chunk(tmp_path: Path, renders: list[Path]) -> None:
    _run(tmp_path, _comp(tmp_path, ["a", "b", "c"]))
    renders.clear()
    report = _run(tmp_path, _comp(tmp_path, ["a", "B", "c"]))
    assert len(renders) == 1 and report.rendered == 1 and report.reused == 2


def test_inserting_a_scene_keeps_later_chunks_cached(tmp_path: Path, renders: list[Path]) -> None:
    _run(tmp_path, _comp(tmp_path, ["a", "b", "c"]))
    renders.clear()
    report = _run(tmp_path, _comp(tmp_path, ["intro", "a", "b", "c"]))
    assert len(renders) == 1 and report.reused == 3
    assert count_video_frames(tmp_path / "out.mp4") == 120


def test_switch_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VE_RENDER_CHUNKS", "1")
    assert chunked_render_enabled() is True
    monkeypatch.setenv("VE_RENDER_CHUNKS", "off")
    assert chunked_render_enabled() is False
    monkeypatch.delenv("VE_RENDER_CHUNKS")
    assert chunked_render_enabled() is chunked_render.CHUNKED_DEFAULT
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/pytest tests/test_chunked_render.py -q`
Expected: `ModuleNotFoundError: No module named 'video_editor_core.chunked_render'`.

- [ ] **Step 3: Implement** `packages/core/src/video_editor_core/chunked_render.py`

```python
"""Render a composition as cached scene chunks, then join and add audio once.

Pipeline: apply scene transitions → plan chunks at scene cuts → for each chunk,
slice (rebased to 0, video only) → compile its own HyperFrames project → hash
→ reuse the cached MP4 or render it → concat by stream copy → mix the audio
cues once → mux. Chunks render one at a time; HyperFrames already spreads each
one over its own Chrome workers. See docs/plans/2026-11-25-chunked-render-concept.md.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

from video_editor_core.chunk_media import concat_videos, mix_audio_cues, mux_audio
from video_editor_core.compile_composition import compile_composition
from video_editor_core.composition import Composition
from video_editor_core.hyperframes_cli import render_project
from video_editor_core.chunk_cache import (
    RenderCache,
    count_video_frames,
    project_hash,
    render_settings_key,
)
from video_editor_core.scene_chunks import (
    DEFAULT_MIN_SECONDS,
    DEFAULT_TARGET_SECONDS,
    frame_count,
    plan_scene_chunks,
    slice_composition,
)
from video_editor_core.scene_transitions import apply_scene_transitions

logger = structlog.get_logger(__name__)

#: Flipped to True by T6 once a measured run matches the single-pass render.
CHUNKED_DEFAULT = False
_ON = {"1", "on", "true", "yes"}
_OFF = {"0", "off", "false", "no"}


def chunked_render_enabled() -> bool:
    raw = os.environ.get("VE_RENDER_CHUNKS", "").strip().lower()
    if raw in _ON:
        return True
    if raw in _OFF:
        return False
    return CHUNKED_DEFAULT


class ChunkedRenderError(RuntimeError):
    pass


@dataclass
class ChunkResult:
    index: int
    scene_ids: tuple[str, ...]
    key: str
    cached: bool
    frames: int
    seconds: float


@dataclass
class ChunkedRenderReport:
    output: Path
    total_frames: int
    chunks: list[ChunkResult] = field(default_factory=list)

    @property
    def rendered(self) -> int:
        return sum(1 for chunk in self.chunks if not chunk.cached)

    @property
    def reused(self) -> int:
        return sum(1 for chunk in self.chunks if chunk.cached)

    def to_json(self) -> dict[str, Any]:
        return {
            "output": str(self.output),
            "total_frames": self.total_frames,
            "rendered": self.rendered,
            "reused": self.reused,
            "chunks": [{**asdict(chunk), "scene_ids": list(chunk.scene_ids)} for chunk in self.chunks],
        }


def _render_chunk(project: Path, output: Path, frames: int, cli_path: str, timeout: int) -> None:
    """One HyperFrames render (a seam for tests; ``frames`` is what the caller checks)."""
    del frames
    render_project(project, output, cli_path=cli_path, timeout=timeout)


def render_composition_chunked(
    composition: Composition,
    output_path: Path,
    work_dir: Path,
    *,
    cache_dir: Path,
    cli_path: str = "hyperframes",
    timeout: int = 1800,
    target_seconds: float = DEFAULT_TARGET_SECONDS,
    min_seconds: float = DEFAULT_MIN_SECONDS,
) -> ChunkedRenderReport:
    output_path = Path(output_path)
    work_dir = Path(work_dir)
    composition = apply_scene_transitions(composition)
    fps = composition.canvas.fps
    spans = plan_scene_chunks(composition, target_seconds=target_seconds, min_seconds=min_seconds)
    cache = RenderCache(cache_dir)
    settings = render_settings_key(fps, cli_path)
    chunk_root = work_dir / "chunks"
    shutil.rmtree(chunk_root, ignore_errors=True)
    report = ChunkedRenderReport(output=output_path, total_frames=frame_count(composition.duration, fps))
    parts: list[Path] = []
    for span in spans:
        started = time.monotonic()
        project = chunk_root / f"chunk_{span.index:03d}"
        compile_composition(slice_composition(composition, span), project)
        key = project_hash(project, settings)
        path = cache.lookup(key, span.frames)
        cached = path is not None
        if path is None:
            rendered = project / "rendered.mp4"
            _render_chunk(project, rendered, span.frames, cli_path, timeout)
            got = count_video_frames(rendered)
            if got != span.frames:
                raise ChunkedRenderError(
                    f"chunk {span.index} ({', '.join(span.scene_ids)}) rendered {got} frames, "
                    f"expected {span.frames}"
                )
            path = cache.store(key, rendered, frames=span.frames, scene_ids=span.scene_ids)
        seconds = round(time.monotonic() - started, 2)
        report.chunks.append(
            ChunkResult(span.index, span.scene_ids, key, cached, span.frames, seconds)
        )
        logger.info(
            "chunked_render_chunk", index=span.index, scenes=list(span.scene_ids),
            cached=cached, frames=span.frames, seconds=seconds,
        )
        parts.append(path)

    joined = concat_videos(parts, work_dir / "chunks_joined.mp4")
    got = count_video_frames(joined)
    if got != report.total_frames:
        raise ChunkedRenderError(f"joined video has {got} frames, expected {report.total_frames}")
    if composition.audio and composition.audio_mode == "embed":
        audio = mix_audio_cues(composition.audio, composition.duration, work_dir / "chunks_audio.wav")
        mux_audio(joined, audio, output_path)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(joined, output_path)
    cache.prune({chunk.key for chunk in report.chunks})
    (work_dir / "chunked_render.json").write_text(json.dumps(report.to_json(), indent=2), encoding="utf-8")
    logger.info("chunked_render_done", rendered=report.rendered, reused=report.reused,
                frames=report.total_frames)
    return report
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/bin/pytest tests/test_chunked_render.py -q && .venv/bin/ruff check packages/core/src/video_editor_core/chunked_render.py tests/test_chunked_render.py`
Expected: `5 passed`, ruff clean. If `test_unchanged_rerun_renders_nothing` fails, `compile_composition`
output is not deterministic. Diff two compiles of the same slice
(`diff -r work/chunks/chunk_000 <copy>`) and report the varying field. Do not paper over it in the hash.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/video_editor_core/chunked_render.py tests/test_chunked_render.py
git commit -m "render: chunked scene render with per-chunk cache (off by default)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## 5 Use the chunked path in render_scene_plan
files: packages/core/src/video_editor_core/scene_plan_composition.py, tests/test_scene_plan_composition.py
needs: 4
do: In render_scene_plan, still compile the full project (QA gates read it), then, when chunked_render_enabled(), call render_composition_chunked with cache_dir = project_dir.parent / "render_cache" instead of render_project; add the two tests from this task's steps.
done-when: `PYTHONPATH=packages/core/src /mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/pytest tests/test_scene_plan_composition.py -q` and `/mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/ruff check packages/core/src/video_editor_core/scene_plan_composition.py tests/test_scene_plan_composition.py`
difficulty: easy
kind: change

**Interfaces**
- Consumes: `chunked_render_enabled`, `render_composition_chunked` (T4); `from_scene_plan` (already imported in the module).
- Produces: no new names. `render_scene_plan(plan_path, output_path, project_dir, *, cli_path, timeout, fps)`
  keeps its signature and return value.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_scene_plan_composition.py`)

```python
def test_render_scene_plan_takes_the_chunked_path_when_switched_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from video_editor_core import chunked_render, hyperframes_cli
    from video_editor_core.scene_plan_composition import render_scene_plan

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(tmp_path)), encoding="utf-8")
    seen: dict[str, Path] = {}

    def fake_chunked(composition, output_path, work_dir, *, cache_dir, cli_path, timeout):
        seen["cache_dir"] = cache_dir
        Path(output_path).write_bytes(b"mp4")

    monkeypatch.setenv("VE_RENDER_CHUNKS", "1")
    monkeypatch.setattr(hyperframes_cli, "hyperframes_available", lambda cli="hyperframes": True)
    monkeypatch.setattr(chunked_render, "render_composition_chunked", fake_chunked)
    project = tmp_path / "job" / "composition"
    out = render_scene_plan(plan_path, tmp_path / "final.mp4", project)
    assert out.read_bytes() == b"mp4"
    assert seen["cache_dir"] == tmp_path / "job" / "render_cache"
    assert (project / "index.html").exists()  # the full project is still compiled for QA


def test_render_scene_plan_keeps_the_single_pass_when_switched_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from video_editor_core import hyperframes_cli
    from video_editor_core.scene_plan_composition import render_scene_plan

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(tmp_path)), encoding="utf-8")

    def fake_render(project_dir, output_path, **kwargs):
        Path(output_path).write_bytes(b"single")
        return Path(output_path)

    monkeypatch.setenv("VE_RENDER_CHUNKS", "0")
    monkeypatch.setattr(hyperframes_cli, "hyperframes_available", lambda cli="hyperframes": True)
    monkeypatch.setattr(hyperframes_cli, "render_project", fake_render)
    out = render_scene_plan(plan_path, tmp_path / "final.mp4", tmp_path / "job" / "composition")
    assert out.read_bytes() == b"single"
```

Add `import pytest` at the top of the test file if it is not already there.

- [ ] **Step 2: Run the tests and confirm the first one fails**

Run: `.venv/bin/pytest tests/test_scene_plan_composition.py -q`
Expected: `test_render_scene_plan_takes_the_chunked_path_when_switched_on` fails (`KeyError: 'cache_dir'`,
because the single-pass path ran). The switched-off test may already pass.

- [ ] **Step 3: Implement.** In `render_scene_plan` in
`packages/core/src/video_editor_core/scene_plan_composition.py`, replace these lines:

```python
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    compile_scene_plan_project(plan, project_dir, fps=fps)
    rendered = project_dir / "rendered.mp4"
    render_project(project_dir, rendered, cli_path=cli_path, timeout=timeout, fps=fps)
```

with:

```python
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    # The full project is compiled either way: the QA gates (hyperframes check,
    # frame snapshots) read it.
    compile_scene_plan_project(plan, project_dir, fps=fps)
    rendered = project_dir / "rendered.mp4"
    from video_editor_core import chunked_render

    if chunked_render.chunked_render_enabled():
        chunked_render.render_composition_chunked(
            from_scene_plan(plan, fps=fps),
            rendered,
            project_dir.parent / "chunked",
            cache_dir=project_dir.parent / "render_cache",
            cli_path=cli_path,
            timeout=timeout,
        )
    else:
        render_project(project_dir, rendered, cli_path=cli_path, timeout=timeout, fps=fps)
```

Leave the rest of the function (copy to `output_path`, log) unchanged.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/bin/pytest tests/test_scene_plan_composition.py -q && .venv/bin/ruff check packages/core/src/video_editor_core/scene_plan_composition.py tests/test_scene_plan_composition.py`
Expected: all pass, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/video_editor_core/scene_plan_composition.py tests/test_scene_plan_composition.py
git commit -m "render: render_scene_plan uses the chunked cache when VE_RENDER_CHUNKS is on

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## 6 Measured run on the tiffin video, then default on
files: packages/core/src/video_editor_core/chunked_render.py, docs/RENDERING.md, docs/plans/2026-11-25-chunked-render-concept.md
needs: 5
do: Run the real chunked render of projects/tiffin-teardown-1 against its single-pass rendered.mp4, check frames, loudness and picture match as in this task's steps, time an unchanged re-run and a one-scene edit, record the numbers in docs/RENDERING.md, then set CHUNKED_DEFAULT = True.
done-when: `PYTHONPATH=packages/core/src /mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/pytest tests/test_chunked_render.py -q`
difficulty: hard
kind: change

This task runs real HyperFrames renders for about 10 minutes and uses the GPU. It is a human-in-the-loop
task: **stop and report** if any check below fails. Do not flip the default on a failed check.

- [ ] **Step 1: First chunked render (cold cache), timed**

```bash
cd /mnt/data2/git_repos/creative_repos/video-editor
T=projects/tiffin-teardown-1/chunk_trial && mkdir -p $T
time VE_RENDER_CHUNKS=1 .venv/bin/python -c "
from video_editor_core.scene_plan_composition import render_scene_plan
render_scene_plan('projects/tiffin-teardown-1/plan/render_plan.json', '$T/chunked.mp4', '$T/composition')
"
cat $T/chunked/chunked_render.json | head -20
```

Expected: it exits 0, `rendered` equals the chunk count (about 12 for 9:20), and `reused` is 0. Note the wall time.

- [ ] **Step 2: Compare with the single-pass render** (`projects/tiffin-teardown-1/rendered.mp4`)

```bash
B=projects/tiffin-teardown-1/rendered.mp4; C=$T/chunked.mp4
for f in $B $C; do ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 $f; done
for f in $B $C; do ffmpeg -nostats -i $f -af ebur128 -f null - 2>&1 | grep -A1 "Integrated loudness" | tail -1; done
ffmpeg -nostats -i $C -i $B -lavfi "[0:v][1:v]psnr=stats_file=$T/psnr.log" -f null - 2>&1 | grep PSNR
awk '{for(i=1;i<=NF;i++) if($i ~ /^psnr_avg:/){split($i,a,":"); if(min==""||a[2]<min)min=a[2]}} END{print "min frame PSNR", min}' $T/psnr.log
```

Pass when all of these hold:
- the frame counts are equal;
- the integrated loudness differs by ≤ 0.5 LU;
- the average PSNR is ≥ 35 dB;
- the minimum frame PSNR is ≥ 28 dB.

If the loudness differs, HyperFrames mixes cues differently from `mix_audio_cues`. Report both numbers,
since `normalize=0` or the volume handling may need to change. If a low-PSNR frame sits on a chunk
boundary (frame numbers = the `start_frame`s in the report), look at that seam.

- [ ] **Step 3: Warm re-run, unchanged.** Run Step 1's command again.
Expected: `rendered` is 0 and every chunk is reused. The wall time is about join plus mux, under 1 min plus the full-project compile.

- [ ] **Step 4: One-scene edit.** Copy the plan, change one visible text prop of one mid-video scene
(for example a chart label in `scenes[8].props`), and render the copy:

```bash
.venv/bin/python - <<'EOF'
import json
p = json.load(open("projects/tiffin-teardown-1/plan/render_plan.json"))
print(json.dumps(p["scenes"][8]["props"], ensure_ascii=False)[:400])
EOF
```

Edit one visible string from that output into `$T/render_plan_edit.json`, then run Step 1's command
with `render_plan_edit.json` in place of `render_plan.json`. Expected: `rendered` is 1 (2 if the edit
changes a tally that the next scene's chip counts from). Note the wall time.

- [ ] **Step 5: Record the results and flip the default**
  - In `docs/RENDERING.md`, add a "Chunked render cache" section with the three wall times (cold, warm,
    one-scene edit), the chunk count, the PSNR and loudness results, the `VE_RENDER_CHUNKS=0` escape hatch
    and where the cache lives.
  - In `docs/plans/2026-11-25-chunked-render-concept.md`, set **Status** to "built (see
    2026-11-25-chunked-render.md), default on" with the date.
  - In `packages/core/src/video_editor_core/chunked_render.py`, set `CHUNKED_DEFAULT = True`.

Run: `.venv/bin/pytest tests/test_chunked_render.py -q` (expected: pass).

- [ ] **Step 6: Clean up and commit**

```bash
rm -rf projects/tiffin-teardown-1/chunk_trial
git add packages/core/src/video_editor_core/chunked_render.py docs/RENDERING.md docs/plans/2026-11-25-chunked-render-concept.md
git commit -m "render: chunked scene cache on by default after the tiffin measurement

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
