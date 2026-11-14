"""Cross-segment transition library for FFmpeg-backed video assembly.

Provides a small, deterministic set of transitions between consecutive
segments.  All transitions are implemented via ``filter_complex`` so they
work with segments that may differ in codec or container.

Requires FFmpeg 4.4+ for the ``xfade`` and ``acrossfade`` filters (FFmpeg
6.1 ships with both).  Older versions fall back to a plain concat.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import structlog

from video_editor_ffmpeg.probe import MediaInfoError, probe_media
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.render_options import _add_audio_encode_args as _audio_encode
from video_editor_ffmpeg.render_options import _add_video_encode_args as _video_encode
from video_editor_ffmpeg.run import PROBE_TIMEOUT_SECONDS, FFmpegError, run_ffmpeg

logger = structlog.get_logger(__name__)

TransitionKind = Literal[
    "none",
    "fade",
    "slideleft",
    "slideright",
    "slidedown",
    "slideup",
    "wipeleft",
    "wiperight",
    "radial",
]

# Transitions supported by the ``xfade`` filter.
_XFADE_TRANSITIONS: frozenset[TransitionKind] = frozenset(
    {
        "fade",
        "slideleft",
        "slideright",
        "slidedown",
        "slideup",
        "wipeleft",
        "wiperight",
        "radial",
    }
)

DEFAULT_TRANSITION_DURATION = 0.3


def _xfade_available() -> bool:
    """Return True when ``xfade`` is compiled into FFmpeg."""
    try:
        result = run_ffmpeg(
            ["ffmpeg", "-filters"],
            check=True,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (FFmpegError, FileNotFoundError):
        return False
    return "xfade" in result.stdout


def _audio_presence(segment_paths: list[Path]) -> list[bool]:
    """Return, per segment, whether it carries an audio stream."""
    return [probe_media(path).has_audio for path in segment_paths]


def _durations(segment_paths: list[Path]) -> list[float]:
    """Probe each segment and return its duration in seconds."""
    durations: list[float] = []
    for path in segment_paths:
        info = probe_media(path)
        if info.duration is None:
            raise MediaInfoError(f"duration missing for segment {path}")
        durations.append(info.duration)
    return durations


def _normalize_transitions(
    value: TransitionKind | Sequence[TransitionKind],
    length: int,
    default: TransitionKind,
) -> list[TransitionKind]:
    """Normalize a scalar or sequence of transitions to a list of ``length`` items."""
    if isinstance(value, str):
        return [value] * length
    items = list(value)
    if len(items) != length:
        raise ValueError(f"expected sequence of length {length}, got {len(items)}")
    return items


def _normalize_durations(
    value: float | Sequence[float],
    length: int,
    default: float,
) -> list[float]:
    """Normalize a scalar or sequence of durations to a list of ``length`` items."""
    if isinstance(value, (int, float)):
        return [float(value)] * length
    items = [float(v) for v in value]
    if len(items) != length:
        raise ValueError(f"expected sequence of length {length}, got {len(items)}")
    return items


def _concat_xfade_group(
    paths: list[Path],
    transitions: list[TransitionKind],
    transition_durations: list[float],
    output: Path,
    options: RenderOptions,
) -> Path:
    """Concatenate a group of segments using per-pair xfade transitions."""
    durations = _durations(paths)

    # Clamp each transition duration.
    clamped_durations: list[float] = []
    for idx, td in enumerate(transition_durations):
        pair_durations = [durations[idx], durations[idx + 1]]
        max_allowed = min(pair_durations) / 2.0 if pair_durations else td
        if td > max_allowed:
            logger.warning(
                "transition_duration_clamped",
                pair=idx,
                requested=td,
                max_allowed=max_allowed,
            )
            td = max_allowed
        clamped_durations.append(max(0.0, td))

    # If all transitions became zero duration, plain concat is safer.
    if all(d <= 0.0 for d in clamped_durations):
        from video_editor_ffmpeg.cuts import concat_segments

        return concat_segments(paths, output)

    # Video-only segments (e.g. motion graphics) have no audio stream to
    # crossfade. When no segment has audio, render video only; when some do,
    # stand in a silent track for the ones that don't.
    has_audio = _audio_presence(paths)
    with_audio = any(has_audio)

    video_filters: list[str] = []
    audio_filters: list[str] = []
    audio_inputs: list[str] = []
    for idx, present in enumerate(has_audio):
        if present or not with_audio:
            audio_inputs.append(f"{idx}:a")
        else:
            audio_filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={durations[idx]}[sil{idx}]"
            )
            audio_inputs.append(f"sil{idx}")
    v_label = "0:v"
    a_label = audio_inputs[0]
    out_duration = durations[0]

    for idx in range(1, len(paths)):
        next_out_v = f"vt{idx}"
        next_out_a = f"at{idx}"
        td = clamped_durations[idx - 1]
        offset = out_duration - td
        offset = max(0.0, offset)

        if td > 0.0:
            video_filters.append(
                f"[{v_label}][{idx}:v]xfade=transition={transitions[idx - 1]}:"
                f"duration={td}:offset={offset}[{next_out_v}]"
            )
            audio_filters.append(f"[{a_label}][{audio_inputs[idx]}]acrossfade=d={td}[{next_out_a}]")
        else:
            # Zero-duration boundary: just append the next stream.
            video_filters.append(f"[{v_label}][{idx}:v]concat=n=2:v=1:a=0[{next_out_v}]")
            audio_filters.append(
                f"[{a_label}][{audio_inputs[idx]}]concat=n=2:v=0:a=1[{next_out_a}]"
            )

        v_label = next_out_v
        a_label = next_out_a
        out_duration += durations[idx] - td

    if not with_audio:
        audio_filters = []
    filter_complex = ";".join(video_filters + audio_filters)

    args: list[str] = []
    for path in paths:
        args.extend(["-i", str(path)])
    args.extend(["-filter_complex", filter_complex, "-map", f"[{v_label}]"])
    if with_audio:
        args.extend(["-map", f"[{a_label}]"])
    _video_encode(args, options)
    if with_audio:
        _audio_encode(args, options)
    args.append(str(output))

    logger.info(
        "concat_with_transitions",
        segments=len(paths),
        transitions=transitions,
        transition_durations=clamped_durations,
    )
    run_ffmpeg(["ffmpeg", "-y", *args])
    return output


def concat_with_transitions(
    segment_paths: Sequence[Path | str],
    output_path: Path | str,
    *,
    transition: TransitionKind | Sequence[TransitionKind] = "fade",
    transition_duration: float | Sequence[float] = DEFAULT_TRANSITION_DURATION,
    render_options: RenderOptions | None = None,
) -> Path:
    """Concatenate segments with transitions between each pair.

    ``transition`` may be a single ``TransitionKind`` applied between every
    segment, or a sequence with one entry per adjacent pair (length
    ``len(segment_paths) - 1``).  The same applies to
    ``transition_duration``.

    ``transition_duration`` is the overlap time in seconds.  The output
    duration is ``sum(segment durations) - sum(transition_durations)``.

    Any ``"none"`` transition causes the sequence to be split at that
    boundary: each contiguous group of non-"none" boundaries is rendered
    with xfade, and the groups are concatenated with a plain concat.  If
    ``xfade`` is unavailable, the whole sequence falls back to a plain
    concat.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    paths = [Path(p) for p in segment_paths]
    if len(paths) < 2:
        from video_editor_ffmpeg.cuts import concat_segments

        return concat_segments(paths, output)

    pair_count = len(paths) - 1
    transitions = _normalize_transitions(transition, pair_count, "fade")
    transition_durations = _normalize_durations(
        transition_duration, pair_count, DEFAULT_TRANSITION_DURATION
    )

    if not _xfade_available():
        from video_editor_ffmpeg.cuts import concat_segments

        logger.info(
            "concat_no_transitions",
            segments=len(paths),
            reason="xfade_unavailable",
        )
        return concat_segments(paths, output)

    options = render_options or RenderOptions()

    # Split into groups separated by "none" transitions.
    groups: list[tuple[int, int]] = []
    start = 0
    for idx, trans in enumerate(transitions):
        if trans == "none":
            if start <= idx:
                groups.append((start, idx))
            start = idx + 1
    if start < len(paths):
        groups.append((start, len(paths) - 1))

    if not groups:
        # All boundaries are "none".
        from video_editor_ffmpeg.cuts import concat_segments

        return concat_segments(paths, output)

    if len(groups) == 1 and groups[0] == (0, len(paths) - 1):
        # Fast path: all transitions are non-none.
        return _concat_xfade_group(
            paths,
            transitions,
            transition_durations,
            output,
            options,
        )

    # Render each group separately, then plain-concat the results.
    group_outputs: list[Path] = []
    for start, end in groups:
        if end > start:
            group_paths = paths[start : end + 1]
            group_transitions = transitions[start:end]
            group_durations = transition_durations[start:end]
            group_output = output.parent / f"_transition_group_{start}_{end}.mp4"
            _concat_xfade_group(
                group_paths,
                group_transitions,
                group_durations,
                group_output,
                options,
            )
            group_outputs.append(group_output)
        else:
            group_outputs.append(paths[start])

    from video_editor_ffmpeg.cuts import concat_segments

    return concat_segments(group_outputs, output)
