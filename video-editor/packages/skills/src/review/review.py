"""Post-render self-review / automated quality gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from qa.qa import (
    _BLACK_MIN_DURATION,
    _BLACK_THRESHOLD,
    _DEFAULT_EXPECTED_ASPECT,
    _DEFAULT_LOUDNESS_TARGET_LUFS,
    _LOUDNESS_TOLERANCE_LU,
    _SILENCE_MIN_DURATION,
    _SILENCE_THRESHOLD_DB,
    _actual_aspect,
    _aspect_matches,
    detect_black_regions,
    detect_silence_regions,
    measure_integrated_loudness_lufs,
)
from video_editor_ffmpeg.probe import probe_media
from video_editor_state.job import JobContext

from review.models import ReviewCheck, ReviewReport, overall_from_checks

logger = structlog.get_logger(__name__)


class PostRenderReviewError(RuntimeError):
    """Raised when a critical post-render review check fails."""


class PostRenderReviewSkill:
    """Run deterministic post-render quality checks on the exported video."""

    @property
    def name(self) -> str:
        return "post_render_review"

    def __init__(
        self,
        ctx: JobContext,
        expected_aspect_ratio: str = _DEFAULT_EXPECTED_ASPECT,
        loudness_target_lufs: float | None = None,
        loudness_tolerance_lu: float = _LOUDNESS_TOLERANCE_LU,
        silence_threshold_db: float = _SILENCE_THRESHOLD_DB,
        silence_max_duration_seconds: float | None = None,
        black_min_duration: float = _BLACK_MIN_DURATION,
        black_threshold: float = _BLACK_THRESHOLD,
        black_max_duration_seconds: float | None = None,
        duration_tolerance_seconds: float = 1.0,
        expected_duration_seconds: float | None = None,
    ):
        self.ctx = ctx
        self.expected_aspect_ratio = expected_aspect_ratio
        self.loudness_target_lufs = loudness_target_lufs or _DEFAULT_LOUDNESS_TARGET_LUFS
        self.loudness_tolerance_lu = loudness_tolerance_lu
        self.silence_threshold_db = silence_threshold_db
        self.silence_max_duration_seconds = silence_max_duration_seconds
        self.black_min_duration = black_min_duration
        self.black_threshold = black_threshold
        self.black_max_duration_seconds = black_max_duration_seconds
        self.duration_tolerance_seconds = duration_tolerance_seconds
        self.expected_duration_seconds = expected_duration_seconds

    def _default_video_path(self) -> Path:
        """Locate the exported final video for this job."""
        export_outputs = self.ctx.manifest().stages.get("export", {}).get("outputs", {})
        final = export_outputs.get("final_video")
        if final:
            return Path(final)
        return self.ctx.output_dir / "final.mp4"

    def _expected_duration_from_plan(self) -> float | None:
        """Sum scene durations from a scene plan when one exists."""
        plan_path = self.ctx.work_dir / "plan" / "scene_plan.json"
        if not plan_path.exists():
            return None
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
            scenes = data.get("scenes", [])
            if not isinstance(scenes, list):
                return None
            total = 0.0
            for scene in scenes:
                dur = scene.get("duration_seconds")
                if isinstance(dur, (int, float)):
                    total += float(dur)
            return total if total > 0 else None
        except Exception:  # noqa: BLE001 - advisory review check
            return None

    def _run_checks(self, video_path: Path, info: Any) -> list[ReviewCheck]:
        """Run every critical and quality check."""
        checks: list[ReviewCheck] = []
        duration = info.duration
        width = info.width
        height = info.height
        actual_aspect = _actual_aspect(width, height)

        # --- Critical checks -------------------------------------------------
        checks.append(
            ReviewCheck(
                name="final_video_exists",
                category="critical",
                result="PASS" if video_path.exists() and video_path.stat().st_size > 0 else "FAIL",
                actual=str(video_path),
                reason="Final video file exists and is non-empty"
                if video_path.exists()
                else "Final video file missing or empty",
            )
        )

        checks.append(
            ReviewCheck(
                name="has_video_stream",
                category="critical",
                result="PASS" if info.video_stream is not None else "FAIL",
                actual=info.video_stream is not None,
                reason="Video stream detected"
                if info.video_stream is not None
                else "No video stream detected",
            )
        )

        checks.append(
            ReviewCheck(
                name="has_audio_stream",
                category="critical",
                result="PASS" if info.has_audio else "FAIL",
                actual=info.has_audio,
                reason="Audio stream detected" if info.has_audio else "No audio stream detected",
            )
        )

        checks.append(
            ReviewCheck(
                name="valid_duration",
                category="critical",
                result="PASS" if duration and duration > 0 else "FAIL",
                actual=duration,
                reason=f"Duration is {duration:.3f}s"
                if duration and duration > 0
                else "Could not determine duration",
            )
        )

        expected_duration = (
            self.expected_duration_seconds
            if self.expected_duration_seconds is not None
            else self._expected_duration_from_plan()
        )
        if expected_duration is not None and duration is not None:
            within_tolerance = abs(duration - expected_duration) <= self.duration_tolerance_seconds
            checks.append(
                ReviewCheck(
                    name="duration_matches_plan",
                    category="critical",
                    result="PASS" if within_tolerance else "FAIL",
                    actual=round(duration, 3),
                    threshold=f"{expected_duration:.3f}s ± {self.duration_tolerance_seconds:.3f}s",
                    reason=(
                        f"Duration {duration:.3f}s within {self.duration_tolerance_seconds:.3f}s "
                        f"of plan total {expected_duration:.3f}s"
                    )
                    if within_tolerance
                    else (
                        f"Duration {duration:.3f}s differs from plan total "
                        f"{expected_duration:.3f}s by more than {self.duration_tolerance_seconds:.3f}s"
                    ),
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
            else max(1.0, duration * 0.2 if duration else 1.0)
        )
        checks.append(
            ReviewCheck(
                name="audio_not_silent",
                category="critical",
                result="PASS" if total_silence <= silence_threshold else "FAIL",
                actual=round(total_silence, 3),
                threshold=f"<={silence_threshold:.1f}s",
                reason=f"Total silence: {total_silence:.3f}s across {len(silence_regions)} region(s)",
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
            ReviewCheck(
                name="no_black_frames",
                category="critical",
                result="PASS" if total_black <= black_threshold else "FAIL",
                actual=round(total_black, 3),
                threshold=f"<={black_threshold:.1f}s",
                reason=f"Black frame regions: {len(black_regions)} ({total_black:.3f}s)",
            )
        )

        # --- Quality checks --------------------------------------------------
        checks.append(
            ReviewCheck(
                name="aspect_ratio",
                category="quality",
                result="PASS"
                if _aspect_matches(actual_aspect, self.expected_aspect_ratio)
                else "FAIL",
                actual=round(actual_aspect, 4),
                threshold=self.expected_aspect_ratio,
                reason=f"Actual aspect {actual_aspect:.4f} vs expected {self.expected_aspect_ratio}",
            )
        )

        checks.append(
            ReviewCheck(
                name="fps_stable",
                category="quality",
                result="PASS" if info.fps >= 24 else "FAIL",
                actual=round(info.fps, 2),
                threshold=">=24",
                reason=f"Frame rate is {info.fps:.2f} fps",
            )
        )

        if info.has_audio:
            measured_lufs = measure_integrated_loudness_lufs(video_path)
            if measured_lufs is None:
                checks.append(
                    ReviewCheck(
                        name="audio_loudness",
                        category="quality",
                        result="UNSURE",
                        actual=None,
                        threshold=f"{self.loudness_target_lufs}±{self.loudness_tolerance_lu} LUFS",
                        reason="Could not measure integrated loudness (ebur128 failed)",
                    )
                )
            else:
                in_range = (
                    abs(measured_lufs - self.loudness_target_lufs) <= self.loudness_tolerance_lu
                )
                checks.append(
                    ReviewCheck(
                        name="audio_loudness",
                        category="quality",
                        result="PASS" if in_range else "FAIL",
                        actual=round(measured_lufs, 2),
                        threshold=f"{self.loudness_target_lufs}±{self.loudness_tolerance_lu} LUFS",
                        reason=(
                            f"Integrated loudness {measured_lufs:.2f} LUFS "
                            f"(target {self.loudness_target_lufs:.0f}±"
                            f"{self.loudness_tolerance_lu:.0f} LUFS)"
                        ),
                    )
                )

        if video_path.exists():
            size_mb = video_path.stat().st_size / (1024 * 1024)
            checks.append(
                ReviewCheck(
                    name="file_size_mb",
                    category="quality",
                    result="PASS",
                    actual=round(size_mb, 2),
                    reason=f"File size is {size_mb:.2f} MB",
                )
            )

        export_stage = self.ctx.manifest().stages.get("export")
        checks.append(
            ReviewCheck(
                name="export_stage_present",
                category="quality",
                result="PASS" if export_stage is not None else "UNSURE",
                actual=export_stage is not None,
                reason="Export stage recorded in manifest"
                if export_stage is not None
                else "No export stage recorded in manifest",
            )
        )

        return checks

    def run(self, video_path: Path | str | None = None) -> ReviewReport:
        """Run post-render review checks and persist a report.

        Raises:
            PostRenderReviewError: if any critical check fails.
        """
        self.ctx.ensure_dirs()
        target = Path(video_path) if video_path else self._default_video_path()
        if not target.exists():
            raise PostRenderReviewError(f"Final video not found: {target}")

        info = probe_media(target)
        checks = self._run_checks(target, info)

        overall = overall_from_checks(checks)
        report = ReviewReport(
            job_id=self.ctx.job_id,
            video_path=str(target),
            overall=overall,
            checks=checks,
            passed=overall != "FAIL",
        )

        report_path = self.ctx.artifact("review", "review_report.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )

        self.ctx.manifest().stage_done("review", report.model_dump(mode="json"))
        self.ctx.store.save()

        logger.info(
            "post_render_review_complete",
            job_id=self.ctx.job_id,
            overall=report.overall,
            checks=len(report.checks),
            video=str(target),
        )

        if report.overall == "FAIL":
            failed = [
                c.name for c in report.checks if c.category == "critical" and c.result == "FAIL"
            ]
            raise PostRenderReviewError(
                f"Post-render review failed for {self.ctx.job_id}; "
                f"critical checks: {failed}. See {report_path}"
            )

        return report
