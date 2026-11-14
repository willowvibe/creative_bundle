"""Measure a reference video's style grammar (P4.4).

The audit's reference workflow extracts a frame every half second and writes a
style guide covering palette, shot lengths and cut rate. The *measured* half of
that guide is deterministic, so it lives here: ffmpeg finds scene cuts and
samples frames, Pillow quantizes the palette, and the caller gets a
``StyleProfile`` plus a markdown guide and a DesignTokens draft.

The prose interpretation (type pairing, camera moves, "grammar not content")
still needs a vision model; the markdown says so instead of inventing values.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from PIL import Image
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg

__all__ = [
    "Shot",
    "StyleProfile",
    "design_draft",
    "detect_cuts",
    "dominant_palette",
    "measure_style",
    "style_markdown",
]


@dataclass(frozen=True)
class Shot:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class StyleProfile:
    source: str
    duration: float
    fps: float
    width: int
    height: int
    shots: tuple[Shot, ...]
    palette: tuple[str, ...]

    @property
    def aspect(self) -> str:
        from math import gcd

        divisor = gcd(self.width, self.height) or 1
        return f"{self.width // divisor}:{self.height // divisor}"

    @property
    def cut_rate_per_minute(self) -> float:
        if self.duration <= 0:
            return 0.0
        return (len(self.shots) - 1) * 60.0 / self.duration

    def as_dict(self) -> dict[str, object]:
        durations = [shot.duration for shot in self.shots]
        ordered = sorted(durations)
        median = ordered[len(ordered) // 2] if ordered else 0.0
        return {
            "source": self.source,
            "duration": round(self.duration, 3),
            "fps": self.fps,
            "aspect": self.aspect,
            "cuts": len(self.shots) - 1,
            "cut_rate_per_minute": round(self.cut_rate_per_minute, 2),
            "shot_seconds": {
                "min": round(min(durations), 3) if durations else 0.0,
                "median": round(median, 3),
                "max": round(max(durations), 3) if durations else 0.0,
            },
            "palette": list(self.palette),
        }


def _require_positive(value: float, label: str) -> None:
    if value <= 0:
        raise ValueError(f"{label} must be positive, got {value}")


def detect_cuts(video: Path | str, *, threshold: float = 0.3) -> tuple[float, ...]:
    """Return scene-cut timestamps using ffmpeg's scene score.

    ``select='gt(scene,threshold)',showinfo`` emits one ``pts_time`` line per
    retained frame on stderr; the first frame is the start, not a cut.
    """
    path = Path(video)
    if not path.is_file():
        raise FileNotFoundError(f"reference video not found: {path}")
    if not 0.0 < threshold < 1.0:
        raise ValueError(f"threshold must be between 0 and 1, got {threshold}")
    completed = run_ffmpeg(
        [
            "ffmpeg",
            "-i",
            str(path),
            "-an",
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-f",
            "null",
            "-",
        ],
        check=False,
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"scene detection failed: {completed.stderr[-400:]}")
    cuts: list[float] = []
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", completed.stderr):
        value = float(match.group(1))
        if value > 0:
            cuts.append(value)
    return tuple(sorted(set(cuts)))


def dominant_palette(
    video: Path | str,
    *,
    frames: int = 24,
    colors: int = 5,
) -> tuple[str, ...]:
    """Sample frames and return the most common colours as ``#rrggbb``."""
    path = Path(video)
    if not path.is_file():
        raise FileNotFoundError(f"reference video not found: {path}")
    if frames < 1 or colors < 1:
        raise ValueError("frames and colors must be positive")
    images: list[Image.Image] = []
    with tempfile.TemporaryDirectory(prefix="style-from-") as tmp:
        out = Path(tmp) / "frame_%03d.png"
        completed = run_ffmpeg(
            [
                "ffmpeg",
                "-i",
                str(path),
                "-vf",
                "fps=2,scale=160:-1",
                "-frames:v",
                str(frames),
                str(out),
            ],
            check=False,
            timeout=RENDER_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"frame sampling failed: {completed.stderr[-400:]}")
        for frame in sorted(Path(tmp).glob("frame_*.png")):
            with Image.open(frame) as image:
                images.append(image.convert("RGB").resize((64, 64)))
    if not images:
        raise RuntimeError("no frames were sampled from the reference video")

    montage = Image.new("RGB", (64 * len(images), 64))
    for index, frame_image in enumerate(images):
        montage.paste(frame_image, (64 * index, 0))
    quantized = montage.quantize(colors=colors)
    palette: list[int] = quantized.getpalette() or []
    counts = sorted(cast(list[tuple[int, int]], quantized.getcolors() or []), reverse=True)
    hexes: list[str] = []
    for _count, palette_index in counts:
        base = int(palette_index) * 3
        red, green, blue = palette[base], palette[base + 1], palette[base + 2]
        hexes.append(f"#{red:02x}{green:02x}{blue:02x}")
    return tuple(hexes[:colors])


def measure_style(
    video: Path | str,
    *,
    threshold: float = 0.3,
    frames: int = 24,
    colors: int = 5,
) -> StyleProfile:
    """Probe the file, detect cuts and quantize the palette."""
    path = Path(video)
    info = probe_media(str(path))
    duration = float(info.duration or 0.0)
    _require_positive(duration, "video duration")
    cut_times = detect_cuts(path, threshold=threshold)
    edges = [0.0, *cut_times, duration]
    shots = tuple(
        Shot(start=start, end=end)
        for start, end in zip(edges, edges[1:], strict=False)
        if end > start
    )
    return StyleProfile(
        source=str(path),
        duration=duration,
        fps=float(info.fps or 0.0),
        width=int(info.width or 0),
        height=int(info.height or 0),
        shots=shots,
        palette=dominant_palette(path, frames=frames, colors=colors),
    )


def style_markdown(profile: StyleProfile) -> str:
    """The measured style guide; prose interpretation is left to a vision pass."""
    data = profile.as_dict()
    shot_seconds = data["shot_seconds"]
    assert isinstance(shot_seconds, dict)
    lines = [
        "# Style guide (measured)",
        "",
        f"- Source: `{profile.source}`",
        f"- Duration: {profile.duration:.2f}s at {profile.fps:.2f} fps",
        f"- Aspect: {profile.aspect} ({profile.width}×{profile.height})",
        f"- Cuts: {data['cuts']} ({data['cut_rate_per_minute']}/min)",
        f"- Shot length: min {float(shot_seconds['min']):.3f}s, median "
        f"{float(shot_seconds['median']):.3f}s, max {float(shot_seconds['max']):.3f}s",
        "",
        "## Palette",
        "",
        *[f"- `{color}`" for color in profile.palette],
        "",
        "## Shots",
        "",
        "| # | Start | End | Seconds |",
        "| --- | --- | --- | --- |",
        *[
            f"| {index} | {shot.start:.3f} | {shot.end:.3f} | {shot.duration:.3f} |"
            for index, shot in enumerate(profile.shots, start=1)
        ],
        "",
        "> The measured grammar is above. Type pairing, camera moves and the "
        "transition mix need a vision pass; take the grammar, never the content.",
        "",
    ]
    return "\n".join(lines)


def design_draft(profile: StyleProfile, *, name: str = "reference_style") -> dict[str, object]:
    """A minimal DesignTokens draft the operator can review and edit."""
    colors = list(profile.palette) or ["#000000"]
    return {
        "name": name,
        "palette": {
            "bg": colors[0],
            "ink": colors[1] if len(colors) > 1 else "#ffffff",
            "accent": colors[2] if len(colors) > 2 else colors[0],
            "muted": colors[3] if len(colors) > 3 else colors[-1],
        },
        "measured": profile.as_dict(),
    }


def write_style(
    profile: StyleProfile,
    *,
    style_path: Path | str,
    design_path: Path | str | None = None,
) -> tuple[Path, Path | None]:
    """Write the markdown guide and, when asked, the JSON design draft."""
    style = Path(style_path)
    style.parent.mkdir(parents=True, exist_ok=True)
    style.write_text(style_markdown(profile), encoding="utf-8")
    design: Path | None = None
    if design_path is not None:
        design = Path(design_path)
        design.parent.mkdir(parents=True, exist_ok=True)
        design.write_text(
            json.dumps(design_draft(profile), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return style, design


def sample_frames(
    video: Path | str,
    out_dir: Path | str,
    *,
    frames: int = 12,
    width: int = 160,
) -> list[Path]:
    """Extract ``frames`` evenly spaced PNGs (2 fps sampling) for measurement."""
    path = Path(video)
    if not path.is_file():
        raise FileNotFoundError(f"reference video not found: {path}")
    if frames < 1 or width < 1:
        raise ValueError("frames and width must be positive")
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    pattern = target_dir / "frame_%03d.png"
    completed = run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-vf",
            f"fps=2,scale={width}:-1",
            "-frames:v",
            str(frames),
            str(pattern),
        ],
        check=False,
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"frame sampling failed: {completed.stderr[-400:]}")
    return sorted(target_dir.glob("frame_*.png"))
