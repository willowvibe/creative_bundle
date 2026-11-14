"""`ve musical`: compose a musical video through the Composition IR.

Stage order is fixed: probe → plan → compile → render → mux the music →
normalise loudness → QA. There are no paid providers on this path; every input
is a local file.

The legacy ``scripts/compose_musical_video.py`` remains the FFmpeg reference for
one release. This module owns the IR path and the artifacts under the job's
``work/`` directory.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import cast

import structlog
from beat_sync.beat_sync import BeatSyncConfig, BeatSyncError, BeatSyncSkill
from video_editor_core.adapters.from_musical import from_musical
from video_editor_core.beat_grid import (
    QuantizeMode,
    build_grid,
    grid_markdown,
    quantize_boundaries,
)
from video_editor_core.compile_composition import compile_composition
from video_editor_core.composition import Canvas, Composition
from video_editor_core.hyperframes_cli import (
    HyperFramesCheckError,
    check_project,
    render_project,
    snapshot_project,
)
from video_editor_core.lyrics import parse_lyrics
from video_editor_core.musical import (
    MusicalSource,
    discover_media,
    plan_segments,
)
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg
from video_editor_ffmpeg.voiceover import mux_voiceover
from video_editor_state.job import JobContext

from video_editor_pipelines.composition_qa import MIN_FRAME_STDDEV, frame_stddev
from video_editor_pipelines.explainer_pipeline import measure_loudness, normalize_loudness
from video_editor_pipelines.explainer_qa import (
    LOUDNESS_TARGET_LUFS,
    TRUE_PEAK_CEILING_DB,
    QaCheck,
)

logger = structlog.get_logger(__name__)

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30
DEFAULT_SEGMENT_SECONDS = 8.0
DEFAULT_TRANSITION_SECONDS = 1.5
DURATION_TOLERANCE_SECONDS = 0.1
LOUDNESS_TOLERANCE_LU = 1.0


class MusicalPipelineError(RuntimeError):
    """A musical build that cannot proceed."""


@dataclass(frozen=True)
class MusicalResult:
    final_path: Path
    composition_path: Path
    qa_path: Path | None
    checks: tuple[QaCheck, ...]
    passed: bool


def swell_filter(swells: Sequence[tuple[float, float, float]]) -> str:
    """FFmpeg volume expression for gain points (at seconds, dB, duration)."""
    parts: list[str] = []
    for at, gain_db, duration in swells:
        factor = 10 ** (gain_db / 20.0)
        parts.append(f"volume='if(between(t,{at:g},{at + duration:g}),{factor:.6g},1)':eval=frame")
    return ",".join(parts)


def apply_swells(
    source: Path | str,
    swells: Sequence[tuple[float, float, float]],
    output: Path,
) -> Path:
    """Render the music bed with the requested gain points."""
    expression = swell_filter(swells)
    if not expression:
        return Path(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-af",
            expression,
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise MusicalPipelineError(f"music swell failed: {completed.stderr[-400:]}")
    return output


def run_musical_job(
    ctx: JobContext,
    *,
    music_path: Path | str,
    media_dir: Path | str,
    lyrics_path: Path | str,
    output_path: Path | str,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
    segment_seconds: float = DEFAULT_SEGMENT_SECONDS,
    transition_seconds: float = DEFAULT_TRANSITION_SECONDS,
    cli_path: str = "hyperframes",
    run_qa: bool = True,
    quantize: str = "off",
    beat_tolerance: float = 0.12,
    swells: Sequence[tuple[float, float, float]] = (),
) -> MusicalResult:
    """Build, render and normalise one musical video from local files."""
    ctx.ensure_dirs()
    music = _require_file(music_path, "music")
    lyrics_file = _require_file(lyrics_path, "lyrics")
    output = Path(output_path)
    _require_positive("width", width)
    _require_positive("height", height)
    _require_positive("fps", fps)
    _require_positive("segment_seconds", segment_seconds)
    _require_positive("transition_seconds", transition_seconds)
    if transition_seconds >= segment_seconds:
        raise MusicalPipelineError(
            "transition_seconds must be less than segment_seconds "
            f"(got {transition_seconds:g} and {segment_seconds:g})"
        )

    music_duration = _duration(music, "music")
    sources = _probe_sources(
        discover_media(media_dir, exclude={music, output}),
    )
    boundaries: list[float] | None = None
    if quantize != "off":
        if quantize not in ("beat", "downbeat"):
            raise MusicalPipelineError("quantize must be 'off', 'beat' or 'downbeat'")
        try:
            detector = BeatSyncSkill(config=BeatSyncConfig(generate_cuts=False))
            beat_report = detector.run(ctx, audio_path=music)
            beat_times = [
                float(item["timestamp"])
                for item in json.loads(Path(beat_report["beats_path"]).read_text(encoding="utf-8"))
            ]
        except BeatSyncError as exc:
            raise MusicalPipelineError(f"beat detection failed: {exc}") from exc
        grid = build_grid(beat_times, bpm=beat_report.get("avg_bpm") or None)
        count = max(1, math.ceil(music_duration / segment_seconds))
        planned = [index * segment_seconds for index in range(1, count)]
        mode = cast(QuantizeMode, quantize)
        boundaries, moved = quantize_boundaries(planned, grid, mode=mode, tolerance=beat_tolerance)
        beat_grid_path = ctx.work_dir / "plan" / "beat_grid.md"
        beat_grid_path.parent.mkdir(parents=True, exist_ok=True)
        beat_grid_path.write_text(
            grid_markdown(
                grid,
                planned=planned,
                quantized=boundaries,
                mode=mode,
                tolerance=beat_tolerance,
            ),
            encoding="utf-8",
        )
        logger.info(
            "musical_beat_grid",
            beats=len(grid.beats),
            moved=moved,
            path=str(beat_grid_path),
        )
    segments = plan_segments(
        sources,
        music_duration,
        segment_seconds=segment_seconds,
        transition_seconds=transition_seconds,
        boundaries=boundaries,
    )
    lyrics = parse_lyrics(lyrics_file.read_text(encoding="utf-8"))
    if not lyrics:
        raise MusicalPipelineError(f"no captions parsed from {lyrics_file}")

    composition = from_musical(
        segments,
        lyrics,
        job_id=ctx.job_id,
        canvas=Canvas(width=width, height=height, fps=fps),
    )
    composition_path = ctx.work_dir / "plan" / "composition.json"
    composition_path.parent.mkdir(parents=True, exist_ok=True)
    composition_path.write_text(
        json.dumps(composition.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    project_dir = ctx.work_dir / "composition"
    compile_composition(composition, project_dir)
    rendered = render_project(
        project_dir,
        ctx.work_dir / "rendered.mp4",
        cli_path=cli_path,
        fps=fps,
    )

    # The composer uses the music as the sole audio track and truncates to the
    # shorter side; the planner already clamps the video to the music, so this
    # only trims a frame of encoder padding.
    music_track = (
        apply_swells(music, swells, ctx.work_dir / "music_swelled.wav") if swells else music
    )
    with_music = ctx.work_dir / "with_music.mp4"
    mux_voiceover(rendered, music_track, with_music, shortest=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    final = normalize_loudness(with_music, output)

    checks: tuple[QaCheck, ...] = ()
    qa_path: Path | None = None
    if run_qa:
        checks = tuple(
            _qa_checks(
                project_dir=project_dir,
                composition=composition,
                final=final,
                frame_dir=ctx.work_dir / "qa" / "frames",
                cli_path=cli_path,
            )
        )
        qa_path = ctx.work_dir / "qa.json"
        qa_path.write_text(
            json.dumps(
                {
                    "job_id": ctx.job_id,
                    "passed": all(check.ok for check in checks),
                    "checks": [asdict(check) for check in checks],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    passed = all(check.ok for check in checks)
    logger.info(
        "musical_complete",
        job_id=ctx.job_id,
        final=str(final),
        segments=len(segments),
        captions=len(lyrics),
        passed=passed,
    )
    return MusicalResult(
        final_path=final,
        composition_path=composition_path,
        qa_path=qa_path,
        checks=checks,
        passed=passed,
    )


def _require_file(path: Path | str, label: str) -> Path:
    resolved = Path(path)
    if not resolved.is_file():
        raise MusicalPipelineError(f"{label} file not found: {resolved}")
    return resolved


def _require_positive(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise MusicalPipelineError(f"{name} must be a positive finite number, got {value!r}")


def _duration(path: Path, label: str) -> float:
    try:
        duration = probe_media(path).duration
    except Exception as exc:  # noqa: BLE001 - a probe failure must name the file
        raise MusicalPipelineError(f"could not probe {label} {path}: {exc}") from exc
    if duration is None or not math.isfinite(duration) or duration <= 0:
        raise MusicalPipelineError(f"{label} has no usable duration: {path}")
    return duration


def _probe_sources(sources: Sequence[MusicalSource]) -> list[MusicalSource]:
    """Record each video's source length so the adapter can document a hold."""
    probed: list[MusicalSource] = []
    for source in sources:
        if source.kind != "video" or source.duration is not None:
            probed.append(source)
            continue
        try:
            duration = probe_media(source.path).duration
        except Exception as exc:  # noqa: BLE001 - a source probe is advisory
            logger.warning("musical_source_probe_failed", source=str(source.path), error=str(exc))
            duration = None
        probed.append(replace(source, duration=duration))
    return probed


def _qa_checks(
    *,
    project_dir: Path,
    composition: Composition,
    final: Path,
    frame_dir: Path,
    cli_path: str,
) -> list[QaCheck]:
    checks: list[QaCheck] = []

    try:
        report = check_project(project_dir, cli_path=cli_path)
        checks.append(QaCheck("composition", True, (report.strip() or "check passed")[:200]))
    except HyperFramesCheckError as exc:
        checks.append(QaCheck("composition", False, "FAILED: " + "; ".join(exc.messages[:5])))

    try:
        shots = snapshot_project(
            project_dir,
            frame_dir,
            at=[(scene.start + scene.end) / 2 for scene in composition.scenes],
            cli_path=cli_path,
        )
        deviations = tuple(frame_stddev(shot) for shot in shots)
        blank = [value for value in deviations if value < MIN_FRAME_STDDEV]
        checks.append(
            QaCheck(
                "visual",
                bool(deviations) and not blank,
                (
                    f"{len(deviations)} frames, min luma stddev {min(deviations):.1f}"
                    if deviations
                    else "no frames captured"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001 - a gate that cannot run must fail
        logger.warning("musical_gate_visual_failed", error=str(exc))
        checks.append(QaCheck("visual", False, str(exc)))

    try:
        duration = probe_media(final).duration
    except Exception as exc:  # noqa: BLE001 - unmeasured duration fails
        duration = None
        checks.append(QaCheck("duration", False, f"could not probe {final}: {exc}"))
    else:
        delta = None if duration is None else abs(duration - composition.duration)
        checks.append(
            QaCheck(
                "duration",
                delta is not None and delta <= DURATION_TOLERANCE_SECONDS,
                (
                    f"{duration:.3f}s vs plan {composition.duration:.3f}s"
                    if duration is not None
                    else "no duration reported"
                ),
            )
        )

    try:
        loudness, peak = measure_loudness(final)
        checks.append(
            QaCheck(
                "loudness",
                abs(loudness - LOUDNESS_TARGET_LUFS) <= LOUDNESS_TOLERANCE_LU
                and peak <= TRUE_PEAK_CEILING_DB,
                f"{loudness:.1f} LUFS, true peak {peak:.1f} dBTP",
            )
        )
    except Exception as exc:  # noqa: BLE001 - unmeasured loudness fails
        logger.warning("musical_gate_loudness_failed", error=str(exc))
        checks.append(QaCheck("loudness", False, str(exc)))

    return checks


def default_job_id() -> str:
    """A unique job id for a CLI invocation that did not name one."""
    return f"musical-{uuid.uuid4().hex[:8]}"
