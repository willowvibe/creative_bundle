"""Thin wrapper around the HyperFrames renderer CLI.

Skills call this wrapper rather than invoking HyperFrames directly so the
renderer can be swapped or mocked in tests.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import CommandError, run_subprocess

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = 600


class RenderSegmentError(Exception):
    """Raised when a HyperFrames render produces no usable output."""


class HyperFramesLintError(Exception):
    """Raised when a HyperFrames composition plan fails local validation."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("HyperFrames lint failed: " + "; ".join(messages))


class HyperFramesCheckError(Exception):
    """Raised when ``hyperframes check`` reports a problem."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("HyperFrames check failed: " + "; ".join(messages))


class HyperFramesAudioError(Exception):
    """Raised when ``hyperframes audio`` cannot measure a file."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def resolve_hyperframes_cli(cli_path: str = "hyperframes") -> str:
    """Prefer the pinned local bin over a global HyperFrames install."""
    if cli_path == "hyperframes":
        local = _repo_root() / "node_modules" / ".bin" / "hyperframes"
        if local.is_file():
            return str(local)
    return cli_path


def hyperframes_available(cli_path: str = "hyperframes") -> bool:
    return shutil.which(resolve_hyperframes_cli(cli_path)) is not None


def render_segment(
    input_plan: Path | str,
    output_path: Path | str,
    cli_path: str = "hyperframes",
    timeout: int = DEFAULT_TIMEOUT,
    extra_args: dict[str, Any] | None = None,
) -> Path:
    """Render one HyperFrames project directory to ``output_path``.

    Modern HyperFrames (0.7+) renders an HTML project:
    ``hyperframes render <dir> -o output.mp4``. The older JSON-plan
    interface (``render --input plan.json``) was removed in 0.8, so a plan
    file is rejected here rather than passed to a CLI that ignores it.
    """
    plan = Path(input_plan)
    if plan.is_file():
        raise RenderSegmentError(
            f"HyperFrames no longer renders JSON plans ({plan}); pass an HTML "
            "project directory, or render with FFmpeg instead."
        )
    return render_project(
        plan,
        output_path,
        cli_path=cli_path,
        timeout=timeout,
        extra_args=extra_args,
    )


def _validate_rendered(output: Path, stderr: str | None) -> None:
    """Raise ``RenderSegmentError`` unless ``output`` is a usable video."""
    if not output.exists() or output.stat().st_size == 0:
        raise RenderSegmentError(f"HyperFrames render produced empty output: {output}\n{stderr}")
    try:
        info = probe_media(output)
    except Exception as exc:  # noqa: BLE001
        raise RenderSegmentError(
            f"HyperFrames output is not probeable: {output}\n{stderr}"
        ) from exc
    if info.duration is None or info.duration <= 0:
        raise RenderSegmentError(
            f"HyperFrames render produced zero-duration output: {output}\n{stderr}"
        )


def render_project(
    project_dir: Path | str,
    output_path: Path | str,
    *,
    cli_path: str = "hyperframes",
    timeout: int = DEFAULT_TIMEOUT,
    fps: int | None = None,
    resolution: str | None = None,
    extra_args: dict[str, Any] | None = None,
) -> Path:
    """Render a HyperFrames **HTML project** (``hyperframes render <dir>``).

    This targets the modern CLI (v0.7+), which renders HTML compositions rather
    than a JSON plan. See ``render_segment`` for the legacy JSON-plan interface.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    cli_path = resolve_hyperframes_cli(cli_path)
    cmd = [cli_path, "render", str(Path(project_dir).resolve()), "-o", str(output.resolve())]
    if fps is not None:
        cmd.extend(["--fps", str(fps)])
    if resolution:
        cmd.extend(["--resolution", resolution])
    if extra_args:
        for key, value in extra_args.items():
            cmd.extend([f"--{key}", str(value)])

    logger.info("hyperframes_render_project", cmd=" ".join(cmd))
    try:
        result = run_subprocess(cmd, check=True, timeout=timeout)
    except CommandError as exc:
        if "timed out" in str(exc).lower():
            raise RenderSegmentError(f"HyperFrames render timed out after {timeout}s") from exc
        raise RenderSegmentError(
            f"HyperFrames render failed\nstderr: {(exc.stderr or '')[-2000:]}"
        ) from exc

    _validate_rendered(output, result.stderr)
    return output


def lint_plan(plan: dict[str, Any]) -> list[str]:
    """Run a lightweight local lint over a HyperFrames composition plan.

    Returns a list of human-readable problems. An empty list means the plan
    passed the local checks. This does not guarantee HyperFrames will accept
    the plan, but it catches the most common mistakes before invoking the
    external CLI.
    """
    problems: list[str] = []

    width = plan.get("width")
    height = plan.get("height")
    fps = plan.get("fps")
    duration = plan.get("duration")
    layers = plan.get("layers", [])

    if not isinstance(width, int) or width <= 0:
        problems.append(f"width must be a positive integer, got {width!r}")
    if not isinstance(height, int) or height <= 0:
        problems.append(f"height must be a positive integer, got {height!r}")
    if not isinstance(fps, (int, float)) or fps <= 0:
        problems.append(f"fps must be positive, got {fps!r}")
    if not isinstance(duration, (int, float)) or duration <= 0:
        problems.append(f"duration must be positive, got {duration!r}")

    if not isinstance(layers, list):
        problems.append("layers must be a list")
        return problems

    seen_names: set[str] = set()
    for i, layer in enumerate(layers):
        if not isinstance(layer, dict):
            problems.append(f"layer {i} is not an object")
            continue

        name = layer.get("name", f"layer_{i}")
        if name in seen_names:
            problems.append(f"duplicate layer name {name!r}")
        seen_names.add(name)

        layer_type = layer.get("type", "")
        x = layer.get("x", 0)
        y = layer.get("y", 0)
        w = layer.get("width", 0)
        h = layer.get("height", 0)
        source = layer.get("source", "")
        text = layer.get("text", "")

        if layer_type in {"image", "logo"}:
            if not source:
                problems.append(f"layer {name!r} ({layer_type}) is missing source")
            elif not Path(source).exists():
                problems.append(f"layer {name!r} source does not exist: {source}")
            elif not Path(source).is_file():
                problems.append(f"layer {name!r} source is not a file: {source}")

        if layer_type == "video" and source:
            if not Path(source).exists():
                problems.append(f"layer {name!r} source does not exist: {source}")
            elif not Path(source).is_file():
                problems.append(f"layer {name!r} source is not a file: {source}")

        if layer_type == "text" and name != "captions" and not text.strip():
            problems.append(f"layer {name!r} (text) has empty text")

        if isinstance(width, int) and isinstance(height, int):
            if (
                not isinstance(x, (int, float))
                or not isinstance(y, (int, float))
                or not isinstance(w, (int, float))
                or not isinstance(h, (int, float))
            ):
                problems.append(f"layer {name!r} rect values must be numeric")
                continue
            if x < 0 or y < 0 or w < 0 or h < 0:
                problems.append(f"layer {name!r} rect has negative value")
            elif x + w > width or y + h > height:
                problems.append(
                    f"layer {name!r} rect ({x},{y},{w},{h}) extends outside "
                    f"canvas ({width}x{height})"
                )

    return problems


def _cli_messages(stdout: str | None, stderr: str | None) -> list[str]:
    text = (stderr or "").strip() or (stdout or "").strip()
    messages = [line.strip() for line in text.splitlines() if line.strip()]
    return messages or ["the CLI reported no detail"]


def analyze_audio(
    path: Path | str,
    *,
    cli_path: str = "hyperframes",
    only: Sequence[str] = ("onsets", "loudness", "envelope"),
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run ``hyperframes audio --json`` and return its measurement payload.

    ``--range`` only applies to film queries; a single rendered file is
    measured whole and the caller slices the envelope/onsets by time.
    """
    cli_path = resolve_hyperframes_cli(cli_path)
    cmd = [
        cli_path,
        "audio",
        str(Path(path).resolve()),
        "--json",
        f"--only={','.join(only)}",
    ]
    logger.info("hyperframes_audio", cmd=" ".join(cmd))
    try:
        result = run_subprocess(cmd, check=True, timeout=timeout)
    except CommandError as exc:
        if "timed out" in str(exc).lower():
            raise HyperFramesAudioError(f"audio analysis timed out after {timeout}s") from exc
        raise HyperFramesAudioError(
            f"audio analysis failed\nstderr: {(exc.stderr or '')[-2000:]}"
        ) from exc
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HyperFramesAudioError(
            f"audio analysis returned non-JSON: {(result.stdout or '')[:200]}"
        ) from exc
    if not isinstance(payload, dict):
        raise HyperFramesAudioError("audio analysis did not return an object")
    return payload


def check_project(
    project_dir: Path | str,
    *,
    cli_path: str = "hyperframes",
    timeout: int = DEFAULT_TIMEOUT,
    strict: bool = False,
) -> str:
    """Run ``hyperframes check``: lint, runtime, layout and contrast.

    Returns the CLI's report text; raises ``HyperFramesCheckError`` on a
    non-zero exit so a build cannot ignore a composition problem.
    """
    cli_path = resolve_hyperframes_cli(cli_path)
    cmd = [cli_path, "check", str(Path(project_dir).resolve())]
    if strict:
        cmd.append("--strict")
    logger.info("hyperframes_check", cmd=" ".join(cmd))
    try:
        result = run_subprocess(cmd, check=True, timeout=timeout)
    except CommandError as exc:
        if "timed out" in str(exc).lower():
            raise HyperFramesCheckError([f"check timed out after {timeout}s"]) from exc
        raise HyperFramesCheckError(_cli_messages(exc.stdout, exc.stderr)) from exc
    return str(result.stdout or "")


def snapshot_project(
    project_dir: Path | str,
    output_dir: Path | str,
    *,
    at: Sequence[float] | None = None,
    frames: int = 5,
    cli_path: str = "hyperframes",
    timeout: int = DEFAULT_TIMEOUT,
) -> list[Path]:
    """Capture PNG frames; returns the files, sorted by name.

    Frames land in a fresh child directory of ``output_dir`` on every call. A
    reused directory would otherwise let a caller read a previous run's PNGs as
    this run's output — the visual QA gate compares those frames against a
    blankness floor, so stale frames turn it into a false pass.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="snapshot-", dir=output))
    cli_path = resolve_hyperframes_cli(cli_path)
    cmd = [cli_path, "snapshot", str(Path(project_dir).resolve()), "-o", str(run_dir.resolve())]
    if at:
        cmd.extend(["--at", ",".join(f"{float(t):g}" for t in at), "--no-end"])
    else:
        cmd.extend(["--frames", str(frames)])
    logger.info("hyperframes_snapshot", cmd=" ".join(cmd))
    try:
        run_subprocess(cmd, check=True, timeout=timeout)
    except CommandError as exc:
        if "timed out" in str(exc).lower():
            raise RenderSegmentError(f"HyperFrames snapshot timed out after {timeout}s") from exc
        raise RenderSegmentError(
            f"HyperFrames snapshot failed\nstderr: {(exc.stderr or '')[-2000:]}"
        ) from exc

    shots = sorted(run_dir.glob("*.png"))
    if not shots:
        raise RenderSegmentError(f"HyperFrames snapshot wrote no PNGs to {run_dir}")
    return shots
