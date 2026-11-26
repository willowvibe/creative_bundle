"""ffmpeg steps of the chunked render: join chunk videos, mix audio cues, mux.

Audio is never chunked (AAC frames only join cleanly at 1,024-sample
boundaries; see the concept doc), so the composition's cues are mixed once
over the whole timeline and muxed onto the joined video.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from video_editor_ffmpeg.run import CommandError, run_subprocess

from video_editor_core.composition import AudioCue


class ChunkMediaError(RuntimeError):
    pass


def _run(cmd: list[str]) -> None:
    try:
        run_subprocess(cmd, timeout=3600)
    except CommandError as exc:
        raise ChunkMediaError(
            f"{' '.join(cmd[:4])}... failed\nstderr: {(exc.stderr or '')[-2000:]}"
        ) from exc


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


def _stereo_pan(source: str) -> str:
    """Return a pan filter that makes ``source`` stereo without attenuation.

    ffmpeg's resampler upmixes mono with a -3 dB centre level, which makes the
    chunked mix quieter than the single-pass render. Duplicating the mono
    channel (or keeping the first two channels) preserves its loudness.
    """
    try:
        result = run_subprocess(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=channels", "-of", "csv=p=0", source],
            timeout=120,
        )
    except CommandError as exc:
        raise ChunkMediaError(
            f"ffprobe {source} failed\nstderr: {(exc.stderr or '')[-2000:]}"
        ) from exc
    channels = (result.stdout or "").strip().split(",")[0]
    if not channels.isdigit():
        raise ChunkMediaError(f"could not probe channel count for {source}")
    if int(channels) == 1:
        return "pan=stereo|c0=c0|c1=c0"
    return "pan=stereo|c0=c0|c1=c1"


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
            f"[{i}:a]{_stereo_pan(cue.source)},atrim=0:{length:.3f},asetpts=PTS-STARTPTS,"
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
