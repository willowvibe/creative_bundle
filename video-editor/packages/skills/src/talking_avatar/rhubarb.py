"""Rhubarb Lip Sync integration for the talking-avatar skill.

Rhubarb turns a 16 kHz mono WAV into timed Preston Blair mouth cues (A-H, plus
X for rest).  This module finds and runs the CLI, parses its JSON, and converts
the cues into the per-frame :class:`~talking_avatar.timeline.VisemeFrame` list.
The cue-to-frame rules are pure functions so they can be tested without the
binary or FFmpeg.
"""

from __future__ import annotations

import json
import math
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_subprocess

from talking_avatar.timeline import VisemeFrame

DEFAULT_FPS = 30.0
DEFAULT_LEAD = 0.02
DEFAULT_RMS_FPS = 30.0
DEFAULT_CLOSURE_MIN_FRAMES = 2
DEFAULT_STRESS_PERCENTILE = 80.0
CLOSURE_SHAPE = "A"
REST_SHAPE = "X"
STRESS_SHAPES = frozenset({"C", "E"})
STRESS_TARGET = "D"
VISEMES = frozenset("ABCDEFGHX")
INSTALL_HINT = (
    "Rhubarb Lip Sync is not installed. Download it from "
    "https://github.com/DanielSWolf/rhubarb-lip-sync and put the `rhubarb` "
    "binary on PATH, or set RHUBARB_BIN to its location."
)


class RhubarbError(RuntimeError):
    """Raised when Rhubarb cannot be found or its output cannot be used."""


@dataclass(frozen=True)
class MouthCue:
    """One Rhubarb mouth cue: a shape shown from ``start`` to ``end`` seconds."""

    start: float
    end: float
    value: str

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "value": self.value}


def find_rhubarb_binary() -> str:
    """Return the ``rhubarb`` executable from ``RHUBARB_BIN`` or ``PATH``."""
    override = os.environ.get("RHUBARB_BIN")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return str(candidate)
        resolved = shutil.which(override)
        if resolved:
            return resolved
        raise RhubarbError(
            f"RHUBARB_BIN is set to {override!r}, but no executable is there. {INSTALL_HINT}"
        )
    executable = shutil.which("rhubarb")
    if executable:
        return executable
    raise RhubarbError(INSTALL_HINT)


def _normalise_value(value: Any) -> str:
    shape = str(value).strip().upper()
    if shape not in VISEMES:
        raise RhubarbError(f"Rhubarb returned an unknown mouth shape {value!r}")
    return shape


def _coerce_cue(item: MouthCue | Mapping[str, Any] | Sequence[Any]) -> MouthCue:
    if isinstance(item, MouthCue):
        return item
    if isinstance(item, Mapping):
        start = float(item.get("start", 0.0))
        end = float(item.get("end", start))
        value = item.get("value", REST_SHAPE)
    else:
        start, end, value = item
        start = float(start)
        end = float(end)
    return MouthCue(start=start, end=end, value=_normalise_value(value))


def parse_mouth_cues(payload: Mapping[str, Any] | Sequence[Any]) -> list[MouthCue]:
    """Parse Rhubarb's ``mouthCues`` JSON (or a bare cue list)."""
    raw = payload.get("mouthCues", []) if isinstance(payload, Mapping) else payload
    cues = [_coerce_cue(item) for item in raw or ()]
    cues.sort(key=lambda cue: (cue.start, cue.end))
    return cues


def shift_cues(
    cues: Sequence[MouthCue | Mapping[str, Any] | Sequence[Any]],
    lead: float,
) -> list[MouthCue]:
    """Return ``cues`` moved ``lead`` seconds earlier, clamped at zero."""
    shift = max(0.0, float(lead))
    shifted: list[MouthCue] = []
    for cue in cues:
        item = _coerce_cue(cue)
        shifted.append(
            MouthCue(
                start=max(0.0, item.start - shift),
                end=max(0.0, item.end - shift),
                value=item.value,
            )
        )
    return shifted


def sample_shape(cues: Sequence[MouthCue], time: float) -> str:
    """Return the shape covering ``time``, or ``X`` outside every cue.

    Cues are half-open (``start <= time < end``); at the exact boundary between
    two cues the later one wins, and the final cue includes its end instant.
    """
    if not cues:
        return REST_SHAPE
    index = 0
    while index + 1 < len(cues) and cues[index].end <= time:
        index += 1
    cue = cues[index]
    if cue.start <= time < cue.end + 1e-9:
        return cue.value
    return REST_SHAPE


def _run_lengths(shapes: Sequence[str]) -> list[tuple[int, int, str]]:
    runs: list[tuple[int, int, str]] = []
    index = 0
    total = len(shapes)
    while index < total:
        end = index + 1
        while end < total and shapes[end] == shapes[index]:
            end += 1
        runs.append((index, end, shapes[index]))
        index = end
    return runs


def enforce_closure_hold(
    shapes: Sequence[str],
    min_frames: int = DEFAULT_CLOSURE_MIN_FRAMES,
) -> list[str]:
    """Extend every ``A`` (bilabial closure) run to at least ``min_frames``."""
    result = list(shapes)
    if min_frames <= 1:
        return result
    for start, end, shape in _run_lengths(result):
        if shape != CLOSURE_SHAPE:
            continue
        needed = min_frames - (end - start)
        forward = end
        while needed > 0 and forward < len(result):
            result[forward] = CLOSURE_SHAPE
            forward += 1
            needed -= 1
        backward = start - 1
        while needed > 0 and backward >= 0:
            result[backward] = CLOSURE_SHAPE
            backward -= 1
            needed -= 1
    return result


def merge_single_frame_runs(shapes: Sequence[str]) -> list[str]:
    """Replace a one-frame non-closure run with the previous frame's shape."""
    result = list(shapes)
    for index in range(1, len(result)):
        if result[index] == result[index - 1]:
            continue
        is_single = index + 1 >= len(result) or result[index + 1] != result[index]
        if is_single:
            result[index] = result[index - 1]
    return result


def _sample_envelope(
    envelope: Sequence[float] | None,
    *,
    time: float,
    rms_fps: float,
) -> float:
    if not envelope:
        return 0.0
    index = max(0, min(len(envelope) - 1, int(time * rms_fps)))
    return float(envelope[index])


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Linear-interpolated percentile of an unsorted sequence."""
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * max(0.0, min(100.0, float(percentile))) / 100.0
    low = int(math.floor(position))
    high = min(len(ordered) - 1, low + 1)
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def apply_stress_open(
    shapes: Sequence[str],
    envelope: Sequence[float] | None,
    *,
    percentile: float = DEFAULT_STRESS_PERCENTILE,
    fps: float = DEFAULT_FPS,
    rms_fps: float = DEFAULT_RMS_FPS,
) -> tuple[list[str], int]:
    """Widen loud ``C``/``E`` frames to ``D``.

    The peak-normalised RMS envelope is converted to dB.  Frames whose shape is
    not ``X`` define the 80th percentile (by default); every ``C`` or ``E`` at
    or above that level becomes ``D``.  All other shapes are left untouched.
    Returns the new shapes and the number of upgraded frames.
    """
    result = list(shapes)
    if not envelope or not result:
        return result, 0
    epsilon = 1e-8
    decibels = [
        20.0
        * math.log10(max(_sample_envelope(envelope, time=index / fps, rms_fps=rms_fps), epsilon))
        for index in range(len(result))
    ]
    speech_levels = [
        decibel for decibel, shape in zip(decibels, result, strict=True) if shape != REST_SHAPE
    ]
    if not speech_levels:
        return result, 0
    threshold = _percentile(speech_levels, percentile)
    upgraded = 0
    for index, shape in enumerate(result):
        if shape in STRESS_SHAPES and decibels[index] >= threshold:
            result[index] = STRESS_TARGET
            upgraded += 1
    return result, upgraded


def build_rhubarb_timeline(
    cues: Sequence[MouthCue | Mapping[str, Any] | Sequence[Any]],
    *,
    duration: float,
    fps: float = DEFAULT_FPS,
    lead: float = DEFAULT_LEAD,
    closure_min_frames: int = DEFAULT_CLOSURE_MIN_FRAMES,
    envelope: Sequence[float] | None = None,
    rms_fps: float = DEFAULT_RMS_FPS,
    bounce: bool = False,
    stress_open: bool = True,
    stress_percentile: float = DEFAULT_STRESS_PERCENTILE,
    stats: dict[str, Any] | None = None,
) -> list[VisemeFrame]:
    """Convert Rhubarb cues into one viseme frame per output frame.

    Cues are first shifted ``lead`` seconds earlier, then each frame samples the
    cue covering its timestamp (``X`` outside cues).  Loud ``C``/``E`` frames are
    widened to ``D`` when ``stress_open`` is on.  After that every ``A`` run is
    held for at least ``closure_min_frames`` frames, and any other one-frame run
    takes the previous frame's shape, so no shape (including ``D``) can appear
    for a single isolated frame.
    """
    if fps <= 0:
        raise ValueError("fps must be positive")
    duration = max(0.0, float(duration))
    total_frames = max(0, int(round(duration * fps)))
    if total_frames == 0:
        if stats is not None:
            stats["stress_upgraded"] = 0
        return []

    shifted = shift_cues(cues, lead)
    shapes = [sample_shape(shifted, index / fps) for index in range(total_frames)]
    upgraded = 0
    if stress_open:
        shapes, upgraded = apply_stress_open(
            shapes,
            envelope,
            percentile=stress_percentile,
            fps=fps,
            rms_fps=rms_fps,
        )
    shapes = enforce_closure_hold(shapes, closure_min_frames)
    shapes = merge_single_frame_runs(shapes)
    if stats is not None:
        stats["stress_upgraded"] = upgraded

    frames: list[VisemeFrame] = []
    for index, shape in enumerate(shapes):
        time = index / fps
        rms = _sample_envelope(envelope, time=time, rms_fps=rms_fps) if bounce else 0.0
        frames.append(VisemeFrame(frame=index, time=index / fps, shape=shape, rms=rms))
    return frames


def convert_to_wav(audio_path: Path | str, wav_path: Path | str) -> Path:
    """Decode ``audio_path`` to a 16 kHz mono 16-bit PCM WAV at ``wav_path``."""
    output = Path(wav_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(audio_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    result = run_subprocess(cmd, check=False, timeout=RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        detail = result.stderr or ""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise RhubarbError(f"Could not convert audio for Rhubarb: {str(detail)[:500]}")
    return output


def run_rhubarb(
    wav_path: Path | str,
    output_path: Path | str,
    *,
    script_path: Path | str | None = None,
    binary: str | None = None,
) -> dict[str, Any]:
    """Run ``rhubarb`` on ``wav_path`` and return its parsed JSON payload."""
    executable = binary or find_rhubarb_binary()
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [executable, "-q", "-f", "json", "-r", "pocketSphinx"]
    if script_path is not None:
        cmd.extend(["-d", str(script_path)])
    cmd.extend(["-o", str(output), str(wav_path)])
    result = run_subprocess(cmd, check=False, timeout=RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        detail = result.stderr or ""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise RhubarbError(f"Rhubarb failed ({result.returncode}): {str(detail)[:500]}")
    if not output.exists():
        raise RhubarbError(f"Rhubarb produced no JSON at {output}")
    data: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
    return data


def extract_mouth_cues(
    audio_path: Path | str,
    work_dir: Path | str,
    *,
    script_path: Path | str | None = None,
    binary: str | None = None,
) -> tuple[list[MouthCue], dict[str, Any]]:
    """Convert the audio, run Rhubarb, and return cues plus the raw payload."""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    wav = convert_to_wav(audio_path, work / "rhubarb.wav")
    payload = run_rhubarb(
        wav,
        work / "rhubarb.json",
        script_path=script_path,
        binary=binary,
    )
    return parse_mouth_cues(payload), payload


__all__ = [
    "CLOSURE_SHAPE",
    "DEFAULT_CLOSURE_MIN_FRAMES",
    "DEFAULT_FPS",
    "DEFAULT_LEAD",
    "DEFAULT_RMS_FPS",
    "DEFAULT_STRESS_PERCENTILE",
    "INSTALL_HINT",
    "MouthCue",
    "REST_SHAPE",
    "STRESS_SHAPES",
    "STRESS_TARGET",
    "RhubarbError",
    "VISEMES",
    "apply_stress_open",
    "build_rhubarb_timeline",
    "convert_to_wav",
    "enforce_closure_hold",
    "extract_mouth_cues",
    "find_rhubarb_binary",
    "merge_single_frame_runs",
    "parse_mouth_cues",
    "run_rhubarb",
    "sample_shape",
    "shift_cues",
]
