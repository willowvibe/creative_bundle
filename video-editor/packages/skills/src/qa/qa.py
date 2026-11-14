"""QA skill for validating rendered videos."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import structlog
from graphics.models import Brand
from video_editor_core.render_graph import RenderGraph
from video_editor_core.safe_area import safe_area_violations
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.regions import parse_ffmpeg_regions
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg
from video_editor_llm import (
    ClassifyClient,
    question_choice,
    question_noul,
    run_sync,
)
from video_editor_state.asset_audit import (
    cost_tracking_degraded_reasons,
    stub_asset_ids,
)
from video_editor_state.job import JobContext
from video_editor_tools.versions import collect_tool_versions

from qa.film_gate import film_gate_checks
from qa.models import QACheck, QAReport

logger = structlog.get_logger(__name__)

_DEFAULT_EXPECTED_ASPECT = "16:9"
_SILENCE_THRESHOLD_DB = -50.0
_SILENCE_MIN_DURATION = 0.5
_BLACK_MIN_DURATION = 0.5
_BLACK_THRESHOLD = 0.02
# Freeze detection: a still frame held at least this long reads as a stall in
# explainers/motion graphics. Noise is freezedetect's per-pixel difference.
_FREEZE_MIN_DURATION = 1.5
_FREEZE_NOISE = 0.003
# Flash detection: a single frame (or two) that is much brighter or darker
# than *both* neighbours is a glitch, typically a broken generation clip.
# ``delta`` is on the 0-255 luma scale; ``agreement`` is how close the two
# neighbours must be before we accept that the spike is isolated rather than
# a real cut. Decoding at 160 px wide keeps the analysis cheap; luma is a
# frame-level statistic, so downscaling does not lose the spike.
_FLASH_DELTA = 40.0
_FLASH_NEIGHBOUR_AGREEMENT = 12.0
_FLASH_DECODE_WIDTH = 160
# Audio dropouts: digital-silence gaps bracketed by sound. -80 dBFS is below
# any real room tone, so a gap this deep is a broken join, not a pause. Gaps
# longer than ``max`` are deliberate silence and belong to ``audio_not_silent``.
_DROPOUT_NOISE_DB = -80.0
_DROPOUT_MIN_DURATION = 0.02
_DROPOUT_MAX_DURATION = 1.0
_DROPOUT_EDGE_GUARD = 0.05
# Jev content-consistency: probability above which an advisory noul question
# is considered satisfied, plus the size cap on the compact textual context.
_JEV_PASS_THRESHOLD = 0.5
_JEV_CONTEXT_MAX_CHARS = 4000
_JEV_SNIPPET_MAX_CHARS = 120
_JEV_MAX_SCENES = 8
# Default integrated-loudness target (LUFS) when no brand is supplied.
# -16 LUFS is the YouTube/Spotify target; -14 LUFS is the podcast target.
_DEFAULT_LOUDNESS_TARGET_LUFS = -16.0
_LOUDNESS_TOLERANCE_LU = 2.0


_ASPECT_RATIOS = {
    "16:9": 16 / 9,
    "9:16": 9 / 16,
    "1:1": 1.0,
    "4:3": 4 / 3,
}


def _parse_ffmpeg_regions(
    stderr: str,
    start_key: str,
    end_key: str,
) -> list[tuple[float, float]]:
    """Parse start/end timestamps from FFmpeg filter output.

    This is a thin wrapper around the shared
    :func:`video_editor_ffmpeg.regions.parse_ffmpeg_regions` so QA and
    rough-cut share one parser while preserving the historical import path.
    """
    return parse_ffmpeg_regions(stderr, start_key, end_key)


def detect_silence_regions(
    path: Path | str,
    threshold_db: float = _SILENCE_THRESHOLD_DB,
    min_duration: float = _SILENCE_MIN_DURATION,
) -> list[tuple[float, float]]:
    """Detect silent audio regions in a video file."""
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={threshold_db}dB:d={min_duration}",
        "-f",
        "null",
        "-",
    ]
    result = run_ffmpeg(cmd, check=False, timeout=RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg silencedetect failed for {path}: exit {result.returncode} - "
            f"{result.stderr[:500]}"
        )
    return _parse_ffmpeg_regions(result.stderr, "silence_start:", "silence_end:")


def detect_black_regions(
    path: Path | str,
    min_duration: float = _BLACK_MIN_DURATION,
    threshold: float = _BLACK_THRESHOLD,
) -> list[tuple[float, float]]:
    """Detect black video regions in a video file."""
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-vf",
        f"blackdetect=d={min_duration}:pix_th={threshold}",
        "-an",
        "-f",
        "null",
        "-",
    ]
    result = run_ffmpeg(cmd, check=False, timeout=RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg blackdetect failed for {path}: exit {result.returncode} - "
            f"{result.stderr[:500]}"
        )
    return _parse_ffmpeg_regions(result.stderr, "black_start:", "black_end:")


def detect_freeze_regions(
    path: Path | str,
    min_duration: float = _FREEZE_MIN_DURATION,
    noise: float = _FREEZE_NOISE,
) -> list[tuple[float, float]]:
    """Detect frozen (motionless) video regions in a video file.

    A freeze is a stretch where consecutive frames differ by less than
    ``noise`` for at least ``min_duration`` seconds — the held end-state of a
    slide or diagram whose entrance animations have finished.
    """
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-vf",
        f"freezedetect=n={noise}:d={min_duration}",
        "-an",
        "-f",
        "null",
        "-",
    ]
    result = run_ffmpeg(cmd, check=False, timeout=RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg freezedetect failed for {path}: exit {result.returncode} - "
            f"{result.stderr[:500]}"
        )
    return _parse_ffmpeg_regions(result.stderr, "freeze_start:", "freeze_end:")


def _luma_series(
    path: Path | str,
    decode_width: int = _FLASH_DECODE_WIDTH,
) -> list[tuple[float, float | None]]:
    """Return ``(pts_time, mean_luma)`` for every decoded frame.

    ``signalstats`` computes ``YAVG`` per frame and ``metadata=print`` writes
    it to stdout. ``nan``/``inf`` (a fully saturated or empty frame) become
    ``None`` so callers can skip them rather than compare against a NaN.
    """
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-vf",
        (f"scale={decode_width}:-2,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-"),
        "-an",
        "-f",
        "null",
        "-",
    ]
    result = run_ffmpeg(cmd, check=False, timeout=RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg signalstats failed for {path}: exit {result.returncode} - "
            f"{result.stderr[:500]}"
        )

    series: list[tuple[float, float | None]] = []
    current_time: float | None = None
    for line in result.stdout.splitlines():
        time_match = re.search(r"pts_time:([\d.]+)", line)
        if time_match:
            current_time = float(time_match.group(1))
            continue
        luma_match = re.search(r"lavfi\.signalstats\.YAVG=(-?[\d.]+|nan|-?inf)", line)
        if luma_match and current_time is not None:
            raw = luma_match.group(1)
            value = None if ("nan" in raw or "inf" in raw) else float(raw)
            series.append((current_time, value))
    return series


def detect_flash_frames(
    path: Path | str,
    delta: float = _FLASH_DELTA,
    agreement: float = _FLASH_NEIGHBOUR_AGREEMENT,
    decode_width: int = _FLASH_DECODE_WIDTH,
    max_frames: int = 2,
) -> list[float]:
    """Return the start time of each isolated flash in ``path``.

    A flash is a large luma jump that is undone by an opposite jump a frame or
    two later, with the frames on either side agreeing within ``agreement``.
    Testing the *jumps* rather than the frames is what makes a two-frame flash
    work: its plateau has no frame that differs from both its neighbours.

    A hard cut makes one jump and never returns, so it cannot match. Scene
    scores cannot be used here — FFmpeg scores the transition *into* a
    single-frame flash at 1.0 and the transition *out* of it at ~0.01, so a
    "two spikes in a row" test misses every single-frame flash.

    The returned times are the first frame of each flash; a run never produces
    more than one entry.
    """
    series = _luma_series(path, decode_width=decode_width)
    values = [value for _, value in series]
    events: list[float] = []
    last_hit: int | None = None

    for start in range(1, len(values)):
        current = values[start]
        before = values[start - 1]
        if current is None or before is None:
            continue
        jump_in = current - before
        if abs(jump_in) < delta:
            continue
        for end in range(start, min(start + max_frames - 1, len(values) - 2) + 1):
            last = values[end]
            after = values[end + 1]
            if last is None or after is None:
                continue
            jump_out = after - last
            if abs(jump_out) < delta or (jump_in > 0) == (jump_out > 0):
                continue
            if abs(after - before) > agreement:
                continue
            if last_hit is None or start > last_hit:
                events.append(series[start][0])
                last_hit = end
            break
    return events


def detect_audio_dropouts(
    path: Path | str,
    total_duration: float,
    noise_db: float = _DROPOUT_NOISE_DB,
    min_duration: float = _DROPOUT_MIN_DURATION,
    max_duration: float = _DROPOUT_MAX_DURATION,
    edge_guard: float = _DROPOUT_EDGE_GUARD,
) -> list[tuple[float, float]]:
    """Return digital-silence gaps that are bracketed by sound.

    Reuses ``silencedetect`` at -80 dBFS. A region only counts as a dropout
    when it starts after ``edge_guard``, ends before the file does, and is no
    longer than ``max_duration`` — otherwise it is leading/trailing silence or
    a deliberate pause, both of which ``audio_not_silent`` already reports.
    """
    regions = detect_silence_regions(
        path,
        threshold_db=noise_db,
        min_duration=min_duration,
    )
    limit = total_duration - edge_guard
    return [
        (start, end)
        for start, end in regions
        if start > edge_guard and end < limit and (end - start) <= max_duration
    ]


def measure_integrated_loudness_lufs(path: Path | str) -> float | None:
    """Return the integrated loudness in LUFS, or None on failure.

    Uses ``ffmpeg -af ebur128=peak=true`` to print a summary to stderr.
    The integrated loudness is reported twice: in the per-frame progress
    line (``... I: -16.4 LUFS ...``) and in the post-run summary block
    (``    I:         -16.4 LUFS``). The summary block's value is the
    authoritative final measurement, but it can sometimes be elided on
    short files. We return the LAST I: value found (which is either the
    summary or, if the summary is missing, the most recent per-frame
    measurement — both are valid). Returns None if no I-line is found.
    """
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-af",
        "ebur128=peak=true",
        "-f",
        "null",
        "-",
    ]
    result = run_ffmpeg(cmd, check=False, timeout=RENDER_TIMEOUT_SECONDS)
    last: float | None = None
    for line in result.stderr.splitlines():
        # The I: token can be at the start of the line, after whitespace
        # in the summary block, or buried in a progress line — match in
        # any position. The lookahead ``(?=\s+LUFS)`` ensures we don't
        # confuse this I: with another field's I: token.
        match = re.search(r"\bI:\s+(-?[\d.]+)\s+LUFS", line)
        if match:
            last = float(match.group(1))
    return last


def _actual_aspect(width: int, height: int) -> float:
    return width / height if height else 0.0


def _aspect_matches(actual: float, expected_label: str, tolerance: float = 0.05) -> bool:
    expected = _ASPECT_RATIOS.get(expected_label)
    if expected is None:
        return True
    return abs(actual - expected) <= tolerance


def _truncate(text: str, limit: int) -> str:
    """Collapse whitespace and clamp ``text`` to ``limit`` characters."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


class QASkill:
    """Run automated quality checks on a rendered video."""

    def __init__(
        self,
        ctx: JobContext,
        expected_aspect_ratio: str = _DEFAULT_EXPECTED_ASPECT,
        brand: Brand | None = None,
        loudness_target_lufs: float | None = None,
        loudness_tolerance_lu: float = _LOUDNESS_TOLERANCE_LU,
        silence_threshold_db: float = _SILENCE_THRESHOLD_DB,
        silence_max_duration_seconds: float | None = None,
        black_min_duration: float = _BLACK_MIN_DURATION,
        black_threshold: float = _BLACK_THRESHOLD,
        black_max_duration_seconds: float | None = None,
        freeze_max_seconds: float | None = None,
        max_flashes: int | None = None,
        max_audio_dropouts: int | None = None,
        safe_area_strict: bool = False,
        film_gate_strict: bool = False,
        classify_client: ClassifyClient | None = None,
    ):
        self.ctx = ctx
        self.classify_client = classify_client
        self.expected_aspect_ratio = expected_aspect_ratio
        # Brand supplies the loudness target. Explicit loudness_target_lufs
        # argument wins over the brand. Falls back to -16 LUFS if neither
        # is set.
        if loudness_target_lufs is not None:
            self.loudness_target_lufs = loudness_target_lufs
        elif brand is not None and "final_loudness" in brand.defaults:
            self.loudness_target_lufs = float(brand.defaults["final_loudness"])
        else:
            self.loudness_target_lufs = _DEFAULT_LOUDNESS_TARGET_LUFS
        self.loudness_tolerance_lu = loudness_tolerance_lu
        self.silence_threshold_db = silence_threshold_db
        self.silence_max_duration_seconds = silence_max_duration_seconds
        self.black_min_duration = black_min_duration
        self.black_threshold = black_threshold
        self.black_max_duration_seconds = black_max_duration_seconds
        # Longest allowed freeze. ``None`` keeps the check advisory (reported,
        # never failing) for footage where holds are legitimate, e.g. screen
        # recordings; explainer/motion-graphics pipelines should set ~1.5s.
        self.freeze_max_seconds = freeze_max_seconds
        # Flash frames and digital-silence dropouts are measured always and
        # fail only when a limit is set. Strobes, camera flashes and staccato
        # music are legitimate in some footage, so the default is advisory;
        # explainer and drama pipelines pass 0 for both.
        self.max_flashes = max_flashes
        self.max_audio_dropouts = max_audio_dropouts
        # Text outside the platform safe area (``video_editor_core.safe_area``)
        # is reported; ``safe_area_strict=True`` makes it fail the build.
        self.safe_area_strict = safe_area_strict
        # Film-gate checks (``qa.film_gate``) are always measured but advisory
        # by default: a drama episode of held close-ups is allowed to be still.
        # Explainer and motion-graphics pipelines pass ``film_gate_strict=True``
        # so a cut that reads like a slideshow fails the build.
        self.film_gate_strict = film_gate_strict

    def _default_video_path(self) -> Path:
        # Prefer the b-roll output, fall back to the captioned output.
        broll = self.ctx.artifact("broll", "with_broll.mp4")
        if broll.exists():
            return broll
        # The captions skill writes "with_captions.mp4" (not "captioned.mp4").
        return self.ctx.artifact("captions", "with_captions.mp4")

    def _run_checks(self, video_path: Path, info: Any) -> list[QACheck]:
        checks: list[QACheck] = []
        duration = info.duration
        width = info.width
        height = info.height
        actual_aspect = _actual_aspect(width, height)

        checks.append(
            QACheck(
                name="file_present",
                passed=video_path.exists() and video_path.stat().st_size > 0,
                value=str(video_path),
                message="Video file exists and is non-empty",
            )
        )
        checks.append(
            QACheck(
                name="valid_duration",
                passed=duration > 0,
                value=duration,
                threshold=">0",
                message=f"Duration is {duration:.3f}s",
            )
        )
        checks.append(
            QACheck(
                name="valid_dimensions",
                passed=width > 0 and height > 0,
                value={"width": width, "height": height},
                message=f"Dimensions are {width}x{height}",
            )
        )
        checks.append(
            QACheck(
                name="has_audio",
                passed=info.has_audio,
                value=info.has_audio,
                message="Video has an audio stream"
                if info.has_audio
                else "No audio stream detected",
            )
        )
        checks.append(
            QACheck(
                name="aspect_ratio",
                passed=_aspect_matches(actual_aspect, self.expected_aspect_ratio),
                value=round(actual_aspect, 4),
                threshold=self.expected_aspect_ratio,
                message=f"Actual aspect {actual_aspect:.4f} vs expected {self.expected_aspect_ratio}",
            )
        )
        checks.append(
            QACheck(
                name="fps_stable",
                passed=info.fps >= 24,
                value=round(info.fps, 2),
                threshold=">=24",
                message=f"Frame rate is {info.fps:.2f} fps",
            )
        )

        silence_regions = detect_silence_regions(
            video_path,
            threshold_db=self.silence_threshold_db,
            min_duration=_SILENCE_MIN_DURATION,
        )
        total_silence = sum(end - start for start, end in silence_regions)
        silence_threshold = (
            self.silence_max_duration_seconds
            if self.silence_max_duration_seconds is not None
            else max(1.0, duration * 0.2)
        )
        checks.append(
            QACheck(
                name="audio_not_silent",
                passed=total_silence <= silence_threshold,
                value=round(total_silence, 3),
                threshold=f"<={silence_threshold:.1f}s",
                message=f"Total silence: {total_silence:.3f}s across {len(silence_regions)} region(s)",
            )
        )

        black_regions = detect_black_regions(
            video_path,
            min_duration=self.black_min_duration,
            threshold=self.black_threshold,
        )
        total_black = sum(end - start for start, end in black_regions)
        black_threshold = self.black_max_duration_seconds or 0.0
        checks.append(
            QACheck(
                name="no_black_frames",
                passed=total_black <= black_threshold,
                value=round(total_black, 3),
                threshold=f"<={black_threshold:.1f}s",
                message=f"Black frame regions: {len(black_regions)} ({total_black:.3f}s)",
            )
        )

        freeze_regions = detect_freeze_regions(video_path)
        longest_freeze = max((end - start for start, end in freeze_regions), default=0.0)
        checks.append(
            QACheck(
                name="no_frozen_frames",
                passed=(
                    self.freeze_max_seconds is None or longest_freeze <= self.freeze_max_seconds
                ),
                value=round(longest_freeze, 3),
                threshold=(
                    "advisory"
                    if self.freeze_max_seconds is None
                    else f"<={self.freeze_max_seconds:.1f}s"
                ),
                message=(
                    f"Frozen regions: {len(freeze_regions)} "
                    f"(longest {longest_freeze:.2f}s)"
                    + (
                        " at " + ", ".join(f"{s:.1f}s" for s, _ in freeze_regions[:6])
                        if freeze_regions
                        else ""
                    )
                ),
            )
        )

        checks.extend(film_gate_checks(video_path, strict=self.film_gate_strict))

        flash_times = detect_flash_frames(video_path)
        checks.append(
            QACheck(
                name="no_flash_frames",
                passed=self.max_flashes is None or len(flash_times) <= self.max_flashes,
                value=len(flash_times),
                threshold=("advisory" if self.max_flashes is None else f"<={self.max_flashes}"),
                message=(
                    f"Isolated flash frames: {len(flash_times)}"
                    + (
                        " at " + ", ".join(f"{t:.2f}s" for t in flash_times[:6])
                        if flash_times
                        else ""
                    )
                ),
            )
        )

        if info.has_audio:
            dropouts = detect_audio_dropouts(video_path, total_duration=duration)
            checks.append(
                QACheck(
                    name="no_audio_dropouts",
                    passed=(
                        self.max_audio_dropouts is None or len(dropouts) <= self.max_audio_dropouts
                    ),
                    value=len(dropouts),
                    threshold=(
                        "advisory"
                        if self.max_audio_dropouts is None
                        else f"<={self.max_audio_dropouts}"
                    ),
                    message=(
                        f"Audio dropouts: {len(dropouts)}"
                        + (
                            " at " + ", ".join(f"{s:.2f}-{e:.2f}s" for s, e in dropouts[:6])
                            if dropouts
                            else ""
                        )
                    ),
                )
            )

        safe_check = self._safe_area_check()
        if safe_check is not None:
            checks.append(safe_check)

        if info.has_audio:
            measured_lufs = measure_integrated_loudness_lufs(video_path)
            if measured_lufs is None:
                checks.append(
                    QACheck(
                        name="audio_loudness",
                        passed=False,
                        value=None,
                        threshold=self.loudness_target_lufs,
                        message="Could not measure integrated loudness (ebur128 failed)",
                    )
                )
            else:
                in_range = (
                    abs(measured_lufs - self.loudness_target_lufs) <= self.loudness_tolerance_lu
                )
                checks.append(
                    QACheck(
                        name="audio_loudness",
                        passed=in_range,
                        value=round(measured_lufs, 2),
                        threshold=f"{self.loudness_target_lufs}±{self.loudness_tolerance_lu} LUFS",
                        message=(
                            f"Integrated loudness {measured_lufs:.2f} LUFS "
                            f"(target {self.loudness_target_lufs:.0f}±"
                            f"{self.loudness_tolerance_lu:.0f} LUFS)"
                        ),
                    )
                )

        stub_assets = stub_asset_ids(self.ctx.work_dir)
        checks.append(
            QACheck(
                name="no_stub_assets",
                passed=not stub_assets,
                value=stub_assets,
                threshold="none",
                message=(
                    "every asset came from a real provider"
                    if not stub_assets
                    else "placeholder provider asset(s): " + ", ".join(stub_assets)
                ),
            )
        )
        degraded = cost_tracking_degraded_reasons(self.ctx.work_dir)
        checks.append(
            QACheck(
                name="cost_tracking",
                passed=True,
                value=degraded,
                threshold="none",
                message=(
                    "complete" if not degraded else "WARNING: degraded - " + "; ".join(degraded)
                ),
            )
        )

        return checks

    def _safe_area_check(self) -> QACheck | None:
        """Check the job's render graph for text outside the safe area.

        Returns ``None`` when the job has no ``plan/render_graph.json`` (the
        rendered pixels alone cannot say where text layers were placed).
        """
        graph_path = self.ctx.artifact("plan", "render_graph.json")
        if not graph_path.exists():
            return None
        try:
            graph = RenderGraph.model_validate_json(graph_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a malformed graph must not break QA
            return None
        unsafe = safe_area_violations(graph.overlays, graph.width, graph.height)
        names = sorted({str(v["name"]) for v in unsafe})
        return QACheck(
            name="text_in_safe_area",
            passed=not (self.safe_area_strict and unsafe),
            value=len(unsafe),
            threshold="0" if self.safe_area_strict else "advisory",
            message=(
                f"{len(unsafe)} text layer(s) outside the safe area"
                + (f": {', '.join(names[:6])}" if names else "")
            ),
        )

    # ------------------------------------------------------------------
    # Jev content-consistency (advisory)
    # ------------------------------------------------------------------
    def _load_scene_plan(self) -> dict[str, Any] | None:
        """Load the project scene plan if one was produced by the plan stage."""
        plan_path = self.ctx.artifact("plan", "scene_plan.json")
        if not plan_path.exists():
            return None
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a malformed plan must not break QA
            return None
        return data if isinstance(data, dict) else None

    def _build_jev_context(self, info: Any, checks: list[QACheck]) -> str:
        """Compose a compact textual summary of the measured QA facts.

        Includes duration/dimensions/fps/audio, the deterministic check
        verdicts, and (when present) an abbreviated scene plan. Truncated to
        ``_JEV_CONTEXT_MAX_CHARS`` so the classifier state stays small.
        """
        lines = [
            f"Duration: {info.duration:.2f}s",
            f"Dimensions: {info.width}x{info.height} at {info.fps:.2f} fps",
            f"Has audio: {bool(info.has_audio)}",
        ]
        if checks:
            verdicts = ", ".join(f"{c.name}={'pass' if c.passed else 'fail'}" for c in checks)
            lines.append(f"Deterministic checks: {verdicts}")

        plan = self._load_scene_plan()
        if plan is not None:
            scenes = plan.get("scenes")
            scenes = [s for s in scenes if isinstance(s, dict)] if isinstance(scenes, list) else []
            durations = [
                float(s["duration_seconds"])
                for s in scenes
                if isinstance(s.get("duration_seconds"), (int, float))
            ]
            planned_total = sum(durations)
            lines.append(f"Scene plan: {len(scenes)} scene(s), planned total {planned_total:.2f}s")
            for i, scene in enumerate(scenes[:_JEV_MAX_SCENES]):
                narration = _truncate(str(scene.get("narration") or ""), _JEV_SNIPPET_MAX_CHARS)
                visual = _truncate(
                    str(scene.get("visual_description") or ""), _JEV_SNIPPET_MAX_CHARS
                )
                lines.append(
                    f"  scene {i}: {scene.get('duration_seconds', '?')}s; "
                    f"narration={narration!r}; visual={visual!r}"
                )

        context = "\n".join(lines)
        return context[:_JEV_CONTEXT_MAX_CHARS]

    async def _jev_content_checks(
        self,
        info: Any,
        checks: list[QACheck],
    ) -> list[QACheck]:
        """Ask Jev a few batched, content-consistency questions about the render.

        Advisory only: returns ``[]`` when no classifier is available, when the
        call fails, or when a question has no answer. All questions are sent in
        a single ``classify`` call.
        """
        client = self.classify_client
        owns_client = False
        if client is None:
            try:
                client = ClassifyClient.from_config()
            except Exception:  # noqa: BLE001 - config probing must not raise
                client = ClassifyClient()
            owns_client = True

        try:
            if not client.available():
                return []

            context = self._build_jev_context(info, checks)
            plan = self._load_scene_plan()
            questions: dict[str, Any] = {
                "content_complete": question_noul(
                    "Given `context`, does this render look complete and free of "
                    "obvious placeholder or stub content?",
                    data={"context": context},
                    true_label="Complete",
                    false_label="Placeholder or stub",
                ),
                "render_state": question_choice(
                    "Which best describes the render state shown in `context`?",
                    {
                        "complete": "A finished render with real content throughout.",
                        "partial": "Some scenes are finished but others are thin or missing.",
                        "placeholder": "Mostly placeholder, stub, or default content.",
                    },
                    data={"context": context},
                ),
            }
            if plan is not None:
                questions["duration_matches"] = question_noul(
                    "Does the rendered video's total duration roughly match the "
                    "total planned scene duration in `context`?",
                    data={"context": context},
                    true_label="Matches",
                    false_label="Mismatched",
                )

            body = await client.classify(
                {"state": "QA review of a rendered video's measured facts."},
                questions,
            )
            answers = body.get("answers") or {}
            results: list[QACheck] = []

            content = answers.get("content_complete")
            if isinstance(content, dict):
                prob = float(content.get("noul", 0.0) or 0.0)
                results.append(
                    QACheck(
                        name="jev_content_complete",
                        passed=prob >= _JEV_PASS_THRESHOLD,
                        value=round(prob, 3),
                        threshold=_JEV_PASS_THRESHOLD,
                        message=(f"Jev content-completeness probability {prob:.2f} (advisory)"),
                    )
                )

            if "duration_matches" in questions:
                duration = answers.get("duration_matches")
                if isinstance(duration, dict):
                    prob = float(duration.get("noul", 0.0) or 0.0)
                    results.append(
                        QACheck(
                            name="jev_duration_matches",
                            passed=prob >= _JEV_PASS_THRESHOLD,
                            value=round(prob, 3),
                            threshold=_JEV_PASS_THRESHOLD,
                            message=(
                                f"Jev duration-matches-plan probability {prob:.2f} (advisory)"
                            ),
                        )
                    )

            state_answer = answers.get("render_state")
            choice = state_answer.get("choice") if isinstance(state_answer, dict) else None
            if choice:
                results.append(
                    QACheck(
                        name="jev_render_state",
                        passed=str(choice) == "complete",
                        value=str(choice),
                        threshold="complete",
                        message=f"Jev render state: {choice} (advisory)",
                    )
                )

            logger.info("jev_qa_content_checks", added=len(results))
            return results
        except Exception as exc:  # noqa: BLE001 - advisory, never raise
            logger.warning("jev_qa_content_failed", error=str(exc))
            return []
        finally:
            if owns_client:
                await client.close()

    def run(self, video_path: Path | str | None = None) -> QAReport:
        """Run QA checks on ``video_path`` and persist a report."""
        self.ctx.ensure_dirs()
        target = Path(video_path) if video_path else self._default_video_path()
        if not target.exists():
            raise FileNotFoundError(f"QA target not found: {target}")

        info = probe_media(target)
        if info.duration is None:
            raise RuntimeError(f"Could not determine duration for {target}")
        checks = self._run_checks(target, info)
        # Overall pass reflects the deterministic gate; the Jev content checks
        # below are advisory and must not flip the verdict.
        passed = all(c.passed for c in checks)
        try:
            checks.extend(run_sync(self._jev_content_checks(info, checks)))
        except Exception as exc:  # noqa: BLE001 - advisory; QA must not fail here
            logger.warning("jev_qa_content_checks_failed", error=str(exc))

        stub_assets = stub_asset_ids(self.ctx.work_dir)
        warnings = (
            ["placeholder provider asset(s): " + ", ".join(stub_assets)] if stub_assets else []
        ) + cost_tracking_degraded_reasons(self.ctx.work_dir)
        report = QAReport(
            job_id=self.ctx.job_id,
            video_path=str(target),
            duration_seconds=info.duration,
            width=info.width,
            height=info.height,
            fps=info.fps,
            has_audio=info.has_audio,
            checks=checks,
            passed=passed,
            warnings=warnings,
            tool_versions=collect_tool_versions(),
        )

        report_path = self.ctx.artifact("qa", "qa_report.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report.model_dump(mode="json"), f, indent=2)

        self.ctx.manifest().stage_done("qa", report.model_dump(mode="json"))
        self.ctx.store.save()

        logger.info(
            "qa_complete",
            job_id=self.ctx.job_id,
            passed=passed,
            checks=len(checks),
            video=str(target),
        )
        return report
