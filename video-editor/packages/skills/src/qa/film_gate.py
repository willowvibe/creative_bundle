"""Anti-slide film gate: does the cut feel like a film, or like a deck?

Ported from the ``huashu-art-motion`` skill's ``scripts/film_gate.py`` (MIT,
commit ``1468196``). Trial and measurements:
``../tool-lab/trials/2026-11-11-art-animation-skills/``.

Three measurements, kept in the units the source calibrated them in:

``film_motion``
    Share of frame pairs whose mean absolute grey difference reaches 9, with
    transitions excluded. Calibrated by the source on 13 hand-labelled films:
    red below 0.035, thin below 0.06. Only this metric is calibrated, so only
    this one is allowed to fail a render.
``film_empty_frames``
    Longest run of frames with almost no edges (Sobel magnitude above 24 on
    fewer than 0.2% of pixels). Fact-class, no calibration: an image with no
    edges is empty whoever made it, and a short film does not get a pass.
``film_blue_purple``
    Share of frames whose ground is a blue/violet gradient -- the palette that
    reads as generated slop. Hue 225-300 deg, saturation >= 0.30, value
    0.06-0.90 on 30% of a frame.

**Deviation from the source.** The source derives transitions from
optical-flow camera compensation (OpenCV ``goodFeaturesToTrack`` + pyramidal
LK + RANSAC), which this repo does not depend on. This port uses FFmpeg's
``scdet`` scores for cuts instead: histogram change, so a pan does not read as
one, which is what the camera compensation was there to rule out. Only the pair
that carries each cut is excluded -- see :func:`_transition_pairs`, where that
width was measured against the source. Because the detector is different, the
ported numbers were cross-checked against the source on the same two films
before the thresholds were trusted: 0.0683 vs 0.069 (``day_master_deck``) and
0.0627 vs 0.059 (``saju_five_elements_ink``), i.e. within 0.004 of the source
on both.

Decoding is bounded by :data:`_MAX_ANALYSIS_SECONDS` so a long drama episode
cannot balloon the QA pass; the metric carries ``analysed_seconds`` and
``source_seconds`` so a truncated read is visible, never silent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import structlog
from video_editor_ffmpeg.probe import MediaInfo, probe_media
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg

from qa.models import QACheck

logger = structlog.get_logger(__name__)

# --- decode (mirrors the source's ``decode_gray``/``blue_purple``) ----------
_GATE_FPS = 12
_GATE_WIDTH = 320
_SUB_BAND_LANDSCAPE = 0.15
_SUB_BAND_PORTRAIT = 0.18
_MAX_ANALYSIS_SECONDS = 300.0

# --- film_motion -----------------------------------------------------------
# A pair is "fast" at a mean absolute grey difference of 9 (0-255). The source
# reads 6-11 as all separating its positive and negative films; 9 has the most
# margin either side. Do not tune without a new set of labelled films.
_FAST_DELTA = 9.0
_FAST_MOTION_RED = 0.035
_FAST_MOTION_THIN = 0.06

# --- transitions -----------------------------------------------------------
_SCENE_SCORE_CUT = 10.0

# --- film_empty_frames -----------------------------------------------------
_BLANK_GRAD = 24.0
_BLANK_FRACTION = 0.002
_BLANK_EDGE_SECONDS = 0.5
_BLANK_MIN_SECONDS = 0.3

# --- film_blue_purple ------------------------------------------------------
_BP_FPS = 2
_BP_WIDTH = 160
_BP_HUE = (225.0, 300.0)
_BP_SAT = 0.30
_BP_VALUE = (0.06, 0.90)
_BP_FRAME_SHARE = 0.30
_BP_RED = 0.30
_BP_THIN = 0.12


@dataclass(frozen=True)
class FilmGateMetrics:
    """What the gate measured, and on how much of the file."""

    source_seconds: float
    analysed_seconds: float
    frames: int
    shots: int
    fast_motion_ratio: float
    empty_run_seconds: float
    empty_run_start: float | None
    blue_purple_share: float

    @property
    def truncated(self) -> bool:
        return self.analysed_seconds + 0.5 < self.source_seconds


def _decode_frames(
    path: Any,
    *,
    fps: int,
    width: int,
    pix_fmt: str,
    seconds: float | None = None,
) -> tuple[np.ndarray, MediaInfo]:
    """Decode ``path`` to raw frames, downscaled and resampled by FFmpeg.

    ``gray`` returns ``(n, h, w)``; ``rgb24`` returns ``(n, h, w, 3)``. The
    height keeps the source's aspect ratio and is forced even so the rawvideo
    frame size is unambiguous.
    """
    info = probe_media(path)
    if info.width <= 0 or info.height <= 0:
        raise RuntimeError(f"cannot decode {path}: no video dimensions")
    height = max(2, int(round(info.height * width / info.width / 2)) * 2)
    cmd = ["ffmpeg", "-v", "error", "-i", str(path)]
    if seconds is not None:
        cmd += ["-t", f"{seconds:.3f}"]
    cmd += [
        "-vf",
        f"fps={fps},scale={width}:{height}:flags=area",
        "-pix_fmt",
        pix_fmt,
        "-f",
        "rawvideo",
        "-",
        "-an",
    ]
    result = run_ffmpeg(cmd, text=False, check=False, timeout=RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        stderr = result.stderr if isinstance(result.stderr, bytes) else str(result.stderr)
        detail = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
        raise RuntimeError(
            f"ffmpeg rawvideo decode failed for {path}: exit {result.returncode} - {detail[:500]}"
        )
    channels = 3 if pix_fmt == "rgb24" else 1
    frame_bytes = width * height * channels
    raw: bytes = result.stdout
    count = len(raw) // frame_bytes
    flat = np.frombuffer(raw[: count * frame_bytes], dtype=np.uint8)
    if channels == 1:
        return flat.reshape(count, height, width), info
    return flat.reshape(count, height, width, channels), info


def _scene_scores(path: Any, *, seconds: float | None = None) -> list[tuple[float, float]]:
    """Return ``(pts_time, score)`` for every analysed frame from FFmpeg ``scdet``.

    Scores run 0-100. The source's own hard cuts land above 18 on our films and
    everything else below 10, which is why :data:`_SCENE_SCORE_CUT` sits at 10.
    """
    cmd = ["ffmpeg", "-v", "error", "-i", str(path)]
    if seconds is not None:
        cmd += ["-t", f"{seconds:.3f}"]
    cmd += [
        "-vf",
        f"fps={_GATE_FPS},scale={_GATE_WIDTH}:-2:flags=area,scdet=threshold=8,"
        "metadata=print:file=-",
        "-an",
        "-f",
        "null",
        "-",
    ]
    result = run_ffmpeg(cmd, check=False, timeout=RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg scdet failed for {path}: exit {result.returncode} - {result.stderr[:500]}"
        )
    scores: list[tuple[float, float]] = []
    current: float | None = None
    for line in result.stdout.splitlines():
        time_match = re.search(r"pts_time:([\d.]+)", line)
        if time_match:
            current = float(time_match.group(1))
            continue
        score_match = re.search(r"lavfi\.scd\.score=(-?[\d.]+)", line)
        if score_match and current is not None:
            scores.append((current, float(score_match.group(1))))
    return scores


def _transition_pairs(
    cut_frames: list[int],
    n_frames: int,
) -> np.ndarray:
    """Mark the frame pair that carries each cut.

    Exactly one pair per cut, and nothing around it. The width was measured,
    not guessed: excluding a window either side of a cut removes the energetic
    in-shot frames that follow it and dragged this port's reading of
    ``day_master_deck`` to 0.033 against the source's 0.069. One pair per cut
    reproduces the source to within 0.003 on both calibration films.

    Dissolves need no separate window: they change a frame at a time, well
    under :data:`_FAST_DELTA`, so they never register as fast motion.
    """
    trans = np.zeros(max(n_frames - 1, 0), dtype=bool)
    for cut in cut_frames:
        if 0 <= cut < len(trans):
            trans[cut] = True
    return trans


def _gradient_fraction(frame: np.ndarray, threshold: float) -> float:
    """Share of pixels whose Sobel gradient magnitude exceeds ``threshold``.

    Same 3x3 Sobel kernels as ``cv2.Sobel``, applied with array shifts so the
    port needs numpy only.
    """
    image = frame.astype(np.float32)
    padded = np.pad(image, 1, mode="edge")
    gx = (
        padded[:-2, 2:]
        + 2.0 * padded[1:-1, 2:]
        + padded[2:, 2:]
        - padded[:-2, :-2]
        - 2.0 * padded[1:-1, :-2]
        - padded[2:, :-2]
    )
    gy = (
        padded[2:, :-2]
        + 2.0 * padded[2:, 1:-1]
        + padded[2:, 2:]
        - padded[:-2, :-2]
        - 2.0 * padded[:-2, 1:-1]
        - padded[:-2, 2:]
    )
    return float((np.hypot(gx, gy) > threshold).mean())


def _empty_runs(
    frames: np.ndarray,
    cut_frames: list[int],
) -> tuple[float, float | None]:
    """Return the longest run of empty frames, in seconds, and where it starts."""
    n = len(frames)
    empty = np.array([_gradient_fraction(f, _BLANK_GRAD) < _BLANK_FRACTION for f in frames])
    usable = np.ones(n, dtype=bool)
    edge = int(round(_BLANK_EDGE_SECONDS * _GATE_FPS))
    usable[:edge] = False
    usable[max(0, n - edge) :] = False
    # The source drops the frames either side of a transition rather than
    # splitting the run there, so a dissolve cannot read as an empty stretch.
    for cut in cut_frames:
        for index in (cut, cut + 1, cut - 1):
            if 0 <= index < n:
                usable[index] = False
    longest, longest_start = 0.0, None
    start: int | None = None
    for index, is_empty in enumerate(list(empty & usable) + [False]):
        if is_empty and start is None:
            start = index
        elif not is_empty and start is not None:
            seconds = (index - start) / _GATE_FPS
            if seconds > longest:
                longest, longest_start = seconds, start / _GATE_FPS
            start = None
    return longest, longest_start


def _blue_purple_share(path: Any, *, seconds: float | None) -> float:
    """Share of frames dominated by a blue/violet ground."""
    frames, _ = _decode_frames(path, fps=_BP_FPS, width=_BP_WIDTH, pix_fmt="rgb24", seconds=seconds)
    if len(frames) == 0:
        return 0.0
    rgb = frames.astype(np.float32) / 255.0
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    brightest = rgb.max(axis=-1)
    darkest = rgb.min(axis=-1)
    spread = brightest - darkest + 1e-6
    saturation = spread / (brightest + 1e-6)
    hue = (
        np.where(
            brightest == red,
            ((green - blue) / spread) % 6.0,
            np.where(brightest == green, (blue - red) / spread + 2.0, (red - green) / spread + 4.0),
        )
        * 60.0
    )
    mask = (
        (hue >= _BP_HUE[0])
        & (hue <= _BP_HUE[1])
        & (saturation >= _BP_SAT)
        & (brightest >= _BP_VALUE[0])
        & (brightest <= _BP_VALUE[1])
    )
    per_frame = mask.reshape(len(frames), -1).mean(axis=1)
    return float((per_frame >= _BP_FRAME_SHARE).mean())


def measure_film_gate(path: Any) -> FilmGateMetrics:
    """Run the three gates on ``path`` and return the raw measurements."""
    info = probe_media(path)
    source_seconds = float(info.duration or 0.0)
    seconds = (
        min(source_seconds, _MAX_ANALYSIS_SECONDS) if source_seconds > 0 else _MAX_ANALYSIS_SECONDS
    )

    frames, info = _decode_frames(
        path, fps=_GATE_FPS, width=_GATE_WIDTH, pix_fmt="gray", seconds=seconds
    )
    if len(frames) < 4:
        raise RuntimeError(f"too few frames to analyse in {path}: {len(frames)}")
    sub_band = _SUB_BAND_PORTRAIT if info.height > info.width else _SUB_BAND_LANDSCAPE
    keep = int(round(frames.shape[1] * (1.0 - sub_band)))
    frames = np.ascontiguousarray(frames[:, :keep])
    n = len(frames)

    int16 = frames.astype(np.int16)
    diffs = np.array(
        [np.abs(int16[index + 1] - int16[index]).mean() for index in range(n - 1)],
        dtype=np.float64,
    )

    cut_frames = sorted(
        {
            int(round(time * _GATE_FPS))
            for time, score in _scene_scores(path, seconds=seconds)
            if score >= _SCENE_SCORE_CUT
        }
    )
    transitions = _transition_pairs(cut_frames, n)
    in_shot = diffs[~transitions] if len(diffs) else diffs
    fast_motion_ratio = float((in_shot >= _FAST_DELTA).mean()) if len(in_shot) else 0.0

    empty_seconds, empty_start = _empty_runs(frames, cut_frames)
    blue_purple_share = _blue_purple_share(path, seconds=seconds)

    return FilmGateMetrics(
        source_seconds=source_seconds,
        analysed_seconds=len(frames) / _GATE_FPS,
        frames=n,
        shots=len(cut_frames) + 1,
        fast_motion_ratio=round(fast_motion_ratio, 4),
        empty_run_seconds=round(empty_seconds, 2),
        empty_run_start=None if empty_start is None else round(empty_start, 2),
        blue_purple_share=round(blue_purple_share, 4),
    )


def _truncation_note(metrics: FilmGateMetrics) -> str:
    if not metrics.truncated:
        return ""
    return f" (analysed the first {metrics.analysed_seconds:.0f}s of {metrics.source_seconds:.0f}s)"


def film_gate_checks(path: Any, *, strict: bool = False) -> list[QACheck]:
    """Return the three film-gate checks for ``path``.

    Advisory unless ``strict``: a drama episode of held close-ups is allowed to
    be still, so a render only fails on these gates when the caller opts in
    (explainer and motion-graphics pipelines). The measurements are always
    reported either way.
    """
    metrics = measure_film_gate(path)
    note = _truncation_note(metrics)

    motion_fail = metrics.fast_motion_ratio < _FAST_MOTION_RED
    motion_thin = metrics.fast_motion_ratio < _FAST_MOTION_THIN
    motion_message = f"In-shot motion in {metrics.fast_motion_ratio:.1%} of frames"
    if motion_thin:
        motion_message += (
            "; thin (under 6%) - the cut is close to a slideshow. Give the viewer "
            "something to follow inside the shot: a push, a glide, or an object's own action."
        )
    motion_message += note

    empty_message = f"Longest edge-less stretch {metrics.empty_run_seconds:.2f}s"
    if metrics.empty_run_start is not None:
        empty_message += f" starting at {metrics.empty_run_start:.2f}s"
    empty_message += note

    blue_message = f"Blue/violet ground on {metrics.blue_purple_share:.1%} of frames"
    if metrics.blue_purple_share >= _BP_THIN:
        blue_message += "; this palette reads as generated slop - move to a designed ground"
    blue_message += note

    return [
        QACheck(
            name="film_motion",
            passed=(not strict) or not motion_fail,
            value=metrics.fast_motion_ratio,
            threshold="advisory" if not strict else f">={_FAST_MOTION_RED}",
            message=motion_message,
        ),
        QACheck(
            name="film_empty_frames",
            passed=(not strict) or metrics.empty_run_seconds < _BLANK_MIN_SECONDS,
            value=metrics.empty_run_seconds,
            threshold="advisory" if not strict else f"<{_BLANK_MIN_SECONDS}s",
            message=empty_message,
        ),
        QACheck(
            name="film_blue_purple",
            passed=(not strict) or metrics.blue_purple_share < _BP_RED,
            value=metrics.blue_purple_share,
            threshold="advisory" if not strict else f"<{_BP_RED}",
            message=blue_message,
        ),
    ]
