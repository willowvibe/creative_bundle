"""Per-segment QA hook for cut-boundary self-evaluation.

The graphics skill renders each segment of a composition in a loop. Between
segments, this module runs two ffmpeg-based checks on the join:

1. **Visual jump** — frame-diff the last 0.1s of ``prev_path`` against the
   first 0.1s of ``curr_path`` using ``ffmpeg -filter_complex psnr``. A
   PSNR below ``PSNR_FAILURE_DB`` dB fails the check.
2. **Audio pop** — measure peak RMS on a 0.2s window centered on the
   boundary using ``ffmpeg -af afftfilt,astats=metadata=1:reset=1``. A
   peak RMS at or above ``AUDIO_POP_THRESHOLD`` fails the check.

The check is **opt-in**: callers (``GraphicsSkill.run_qa_per_segment``)
decide whether to invoke it. When ``prev_path`` is ``None`` (the first
segment in a sequence has no previous), the check is skipped.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import structlog
from video_editor_ffmpeg.run import FFmpegError, run_ffmpeg

from qa.models import QASelfEvalError

logger = structlog.get_logger(__name__)

# Visual jump: PSNR below this (in dB) at a cut boundary is a hard fail.
# 20 dB corresponds to ~1% RMS error per pixel — anything lower means the
# cut is perceptually jarring (e.g. scene change to a different color).
PSNR_FAILURE_DB: float = 20.0

# Audio pop: a boundary whose peak RMS is at or above this threshold is
# clipped or has a hard transient; either is a hard fail.
# 0.90 leaves margin below a full-scale (1.0) square wave while still
# catching real clicks after lossy codec compression of synthetic test
# tones (AAC square-wave RMS can drop to ~0.92).
AUDIO_POP_THRESHOLD: float = 0.90

# Window sizes: the last 0.1s of the previous clip and the first 0.1s of
# the current clip are diffed for the visual check. A 0.2s window centered
# on the boundary is analyzed for the audio check.
_VISUAL_WINDOW_S: float = 0.1
_AUDIO_WINDOW_S: float = 0.2

# FFmpeg progress noise. astats prints one line per audio frame; we
# take the maximum RMS level across the window.
_ASTATS_RMS_DB_RE = re.compile(r"RMS\s+level\s+dB:\s*(-?[\d.]+|-\s*inf|inf|nan)")


def _measure_visual_jump(prev_path: Path, curr_path: Path) -> float | None:
    """Run ``ffmpeg -filter_complex psnr`` on the boundary window.

    Returns the average PSNR in dB, or ``None`` if the host's ffmpeg
    cannot run the psnr filter (returns 0 frames). The two streams are
    trimmed to ``_VISUAL_WINDOW_S`` from the tail of the previous and
    the head of the current clip so the comparison is bounded.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-sseof",
        f"-{_VISUAL_WINDOW_S}",
        "-i",
        str(prev_path),
        "-i",
        str(curr_path),
        "-t",
        f"{_VISUAL_WINDOW_S}",
        "-filter_complex",
        "[0:v][1:v]psnr",
        "-f",
        "null",
        "-",
    ]
    try:
        result = run_ffmpeg(cmd, check=False)
    except FFmpegError as exc:
        logger.warning(
            "psnr_filter_failed",
            stderr_tail=exc.stderr[-400:] if exc.stderr else "",
        )
        return None

    # ffmpeg prints the summary line in this shape (single stream, libx264):
    #   [Parsed_psnr_0 @ 0x...] PSNR y:29.487493 u:29.546700 v:28.397649
    #       average:29.295153 min:25.452289 max:inf
    # We grab the "average:" value; if both inputs are 0 frames long the
    # line is absent and we return None.
    match = re.search(
        r"average:([-\d.inf]+)",
        result.stderr,
    )
    if match is None:
        return None
    value = match.group(1)
    if value in ("inf", "-inf", "nan"):
        return None
    return float(value)


def _measure_peak_rms_at_boundary(prev_path: Path, curr_path: Path) -> float | None:
    """Run ``ffmpeg -af afftfilt,astats=metadata=1:reset=1`` on a 0.2s window
    centered on the boundary.

    We take 0.1s of audio from the tail of ``prev_path`` and 0.1s from
    the head of ``curr_path`` (concatenated) and run the audio through
    astats with ``reset=1`` so each frame gets its own measurement. We
    return the maximum ``RMS level`` across the window — that is the
    peak loudness the boundary presents to the listener.
    """
    boundary_window = _AUDIO_WINDOW_S / 2
    # Bug #72: the previous command applied `-t` as an *output* option,
    # limiting the whole concat output to 0.1s. That meant only the tail
    # of `prev` was measured; the head of `curr` (where the transient
    # actually lives) was never analyzed. The corrected command trims
    # the last 0.1s of `prev` and the first 0.1s of `curr` inside
    # filter_complex, concatenates both halves, and measures the full
    # 0.2s boundary window.
    cmd = [
        "ffmpeg",
        "-y",
        "-sseof",
        f"-{boundary_window}",
        "-i",
        str(prev_path),
        "-i",
        str(curr_path),
        "-filter_complex",
        (
            f"[0:a]atrim=duration={boundary_window}[a0];"
            f"[1:a]atrim=start=0:end={boundary_window}[a1];"
            "[a0][a1]concat=n=2:v=0:a=1[a];"
            "[a]astats=metadata=1:reset=1"
        ),
        "-f",
        "null",
        "-",
    ]
    try:
        result = run_ffmpeg(cmd, check=False)
    except FFmpegError as exc:
        logger.warning(
            "astats_filter_failed",
            stderr_tail=exc.stderr[-400:] if exc.stderr else "",
        )
        return None

    peak_linear: float | None = None
    for line in result.stderr.splitlines():
        match = _ASTATS_RMS_DB_RE.search(line)
        if match is None:
            continue
        raw = match.group(1).replace(" ", "")
        if raw in ("-inf", "inf", "nan"):
            continue
        try:
            db_value = float(raw)
        except ValueError:
            continue
        # Convert dBFS to linear: linear = 10^(dB/20). The threshold
        # comparison is in linear (0.95 = ~-0.45 dBFS) per the QA spec.
        linear = 10.0 ** (db_value / 20.0)
        if peak_linear is None or linear > peak_linear:
            peak_linear = linear
    return peak_linear


def _write_artifact(
    work_dir: Path,
    segment_id: str,
    payload: dict[str, Any],
) -> Path:
    """Write the per-segment QA artifact to ``work_dir/.cache/qa/{segment_id}.json``."""
    artifact_dir = work_dir / ".cache" / "qa"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{segment_id}.json"
    with open(artifact_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return artifact_path


# Public entry point is ``check_segment_boundary``.


def check_segment_boundary(
    prev_path: Path | str | None,
    curr_path: Path | str,
    prev_segment_id: str | None,
    curr_segment_id: str,
    work_dir: Path | str,
) -> dict[str, Any]:
    """Run the per-segment QA check at the ``prev → curr`` cut boundary.

    The check is a no-op when ``prev_path`` is None (the first segment
    in a sequence has no previous); the artifact is written with
    ``skipped=True`` so downstream tooling can distinguish "no check
    performed" from "check passed".

    On a check failure, the artifact is written with ``passed=False``
    and a ``QASelfEvalError`` is raised carrying the segment ID, the
    reason (``"visual_jump"`` or ``"audio_pop"``), and the raw
    measurements.
    """
    work_dir = Path(work_dir)
    curr_path = Path(curr_path)

    if prev_path is None:
        payload: dict[str, Any] = {
            "segment_id": curr_segment_id,
            "prev_segment_id": prev_segment_id,
            "skipped": True,
            "passed": True,
            "reason": None,
            "psnr_db": None,
            "peak_rms": None,
            # Bug #42: include a creation timestamp so a re-render
            # # of the same job doesn't silently overwrite the prior
            # # artifact. Downstream tools (or human reviewers) can
            # # tell which run the file belongs to.
            "created_at": round(time.time(), 3),
        }
        _write_artifact(work_dir, curr_segment_id, payload)
        logger.info(
            "qa_segment_check_skipped",
            segment_id=curr_segment_id,
            reason="no_prev_segment",
        )
        return payload

    prev_path = Path(prev_path)
    psnr_db = _measure_visual_jump(prev_path, curr_path)
    peak_rms = _measure_peak_rms_at_boundary(prev_path, curr_path)

    visual_failed = psnr_db is not None and psnr_db < PSNR_FAILURE_DB
    audio_failed = peak_rms is not None and peak_rms >= AUDIO_POP_THRESHOLD
    # Bug #41: a None on BOTH measurements means the check didn't
    # actually run (e.g. the host's ffmpeg is missing the psnr/astats
    # filters, or the boundary window produced 0 frames). A silent
    # pass is misleading — a downstream tool reading the artifact
    # would think the cut is clean when no measurement was taken.
    # We surface this as a third failure reason so the artifact
    # carries the truth.
    measurements_incomplete = psnr_db is None and peak_rms is None

    if visual_failed:
        reason = "visual_jump"
    elif audio_failed:
        reason = "audio_pop"
    elif measurements_incomplete:
        reason = "measurements_incomplete"
    else:
        reason = None
    passed = reason is None

    payload = {
        "segment_id": curr_segment_id,
        "prev_segment_id": prev_segment_id,
        "skipped": False,
        "passed": passed,
        "reason": reason,
        "psnr_db": round(psnr_db, 3) if psnr_db is not None else None,
        "peak_rms": round(peak_rms, 4) if peak_rms is not None else None,
        "created_at": round(time.time(), 3),
    }
    _write_artifact(work_dir, curr_segment_id, payload)

    if not passed:
        logger.warning(
            "qa_segment_check_failed",
            segment_id=curr_segment_id,
            reason=reason,
            psnr_db=psnr_db,
            peak_rms=peak_rms,
        )
        raise QASelfEvalError(
            segment_id=curr_segment_id,
            reason=reason or "unknown",
            psnr_db=psnr_db,
            peak_rms=peak_rms,
        )

    logger.info(
        "qa_segment_check_passed",
        segment_id=curr_segment_id,
        psnr_db=psnr_db,
        peak_rms=peak_rms,
    )
    return payload
