"""Deterministic planner for the ``musical_video`` template.

No LLM is used. The planner takes a music track, a directory of user media,
and a target segment length, then builds a ``ScenePlan`` where each scene is a
slice of the music paired with one media file (cycled in directory order).
"""

from __future__ import annotations

import math
from pathlib import Path

import structlog
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_ffmpeg.probe import probe_media

logger = structlog.get_logger(__name__)

DEFAULT_SEGMENT_SECONDS: float = 5.0
MAX_SEGMENT_SECONDS: float = 30.0
MIN_SEGMENT_SECONDS: float = 1.0

_IMAGE_EXTS: frozenset[str] = frozenset({"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff"})
_VIDEO_EXTS: frozenset[str] = frozenset({"mp4", "mov", "m4v", "mkv", "webm", "avi"})
_ALLOWED_EXTS = _IMAGE_EXTS | _VIDEO_EXTS


def _discover_media(media_dir: Path) -> list[Path]:
    """Return supported media files directly in ``media_dir`` (non-recursive), sorted.

    Bug #215: the previous docstring said "under ``media_dir``", implying a
    recursive descent, but ``iterdir()`` only ever listed top-level files.
    Nested directories were silently skipped. The wording now matches the
    non-recursive behaviour; ``rglob`` was intentionally not adopted because
    the renderer and docs treat ``media_dir`` as a flat folder of sources.

    Bug #198: ``iterdir()`` + ``child.is_file()`` follow symlinks, and storing
    ``media.resolve()`` resolves them, so a symlink planted in ``media_dir``
    that points at another tenant's ``work_dir`` file (or ``/etc/passwd``)
    would be followed, copied into this tenant's asset cache and the
    composition's assets dir, and read by the tenant. Each candidate is resolved and
    checked to stay within the resolved ``media_dir`` root; anything that
    escapes is skipped with a warning so a malicious link cannot pull an
    out-of-tree file into the build.
    """
    if not media_dir.is_dir():
        raise NotADirectoryError(f"Media directory is not a directory: {media_dir}")
    root = media_dir.resolve()
    files: list[Path] = []
    for child in sorted(media_dir.iterdir()):
        if not child.is_file():
            continue
        if child.suffix.lower().lstrip(".") not in _ALLOWED_EXTS:
            continue
        # Containment: resolve the child (following any symlink) and verify it
        # remains inside the resolved ``media_dir``. A link pointing outside
        # the directory is skipped rather than followed into the build.
        try:
            resolved = child.resolve()
        except OSError:
            logger.warning(
                "media_resolve_failed_skipped",
                media_dir=str(media_dir),
                path=str(child),
            )
            continue
        try:
            resolved.relative_to(root)
        except ValueError:
            logger.warning(
                "media_symlink_escape_skipped",
                media_dir=str(media_dir),
                path=str(child),
                resolved=str(resolved),
            )
            continue
        # Bug #226: a hardlink to an out-of-tree file is not a symlink, so
        # the resolve-then-contain check above (which defeats symlinks by
        # following them to their target) does not catch it — a hardlink
        # resolves to its in-tree path while sharing the original inode.
        # ``shutil.copy2`` would then copy the out-of-tree content into the
        # tenant's asset cache. Reject any discovered file with more than one
        # link; a legitimate single-tenant source has exactly one link, and
        # Linux's ``fs.protected_hardlinks`` already blocks cross-tenant
        # hardlinks at the OS level, so this is defense-in-depth rather than
        # the primary gate.
        try:
            nlink = resolved.stat().st_nlink
        except OSError:
            logger.warning(
                "media_stat_failed_skipped",
                media_dir=str(media_dir),
                path=str(child),
            )
            continue
        if nlink > 1:
            logger.warning(
                "media_hardlink_skipped",
                media_dir=str(media_dir),
                path=str(child),
                nlink=nlink,
            )
            continue
        files.append(child)
    if not files:
        raise ValueError(
            f"No supported media files found in {media_dir}. "
            f"Allowed extensions: {', '.join(sorted(_ALLOWED_EXTS))}"
        )
    return files


def _music_duration_seconds(music_path: Path) -> float:
    """Return the music duration in seconds using ffprobe."""
    if not music_path.is_file():
        raise FileNotFoundError(f"Music file not found: {music_path}")
    info = probe_media(music_path)
    return info.duration or 0.0


def build_music_video_plan(
    job_id: str,
    music_path: Path | str,
    media_dir: Path | str,
    segment_seconds: float = DEFAULT_SEGMENT_SECONDS,
    aspect_ratio: str = "16:9",
) -> ScenePlan:
    """Build a scene plan that cuts user media to a music track.

    Args:
        job_id: Pipeline job id.
        music_path: Path to the music/audio file.
        media_dir: Directory containing user images and short videos.
        segment_seconds: Target duration of each visual segment. The last
            segment is shortened so the total matches the music duration.
        aspect_ratio: Output aspect ratio (used by the renderer).

    Returns:
        A ``ScenePlan`` with one ``music_visual`` scene per segment. Each
        scene's ``asset_query`` holds the resolved filesystem path of the
        media file assigned to that segment.
    """
    music_path = Path(music_path)
    media_dir = Path(media_dir)

    segment_seconds = max(MIN_SEGMENT_SECONDS, min(float(segment_seconds), MAX_SEGMENT_SECONDS))
    music_duration = _music_duration_seconds(music_path)
    if music_duration <= 0:
        raise ValueError(f"Music duration must be > 0, got {music_duration} for {music_path}")

    media_files = _discover_media(media_dir)
    num_segments = max(1, math.ceil(music_duration / segment_seconds))

    scenes: list[Scene] = []
    cursor = 0.0
    for i in range(num_segments):
        media = media_files[i % len(media_files)]
        end = min(cursor + segment_seconds, music_duration)
        # Compare the unrounded remaining duration so floating-point dust
        # on the tail does not get rounded to 0.0 and dropped (#213).
        raw_duration = end - cursor
        if raw_duration <= 0:
            break
        # Round to millisecond precision for storage. The final segment is
        # rounded UP so sub-millisecond dust never shortens the plan total
        # below the music duration (which would clip the audio tail); an
        # interior segment rounds normally so per-frame dust does not
        # accumulate across the timeline.
        is_last = end >= music_duration or i == num_segments - 1
        duration = math.ceil(raw_duration * 1000) / 1000 if is_last else round(raw_duration, 3)
        scenes.append(
            Scene(
                id=f"mv_{i + 1:03d}",
                type="music_visual",
                duration_seconds=duration,
                narration="",
                visual_description="",
                asset_query=str(media.resolve()),
                props={"start_seconds": cursor, "media_file": media.name},
            )
        )
        cursor = end
        if cursor >= music_duration:
            break

    logger.info(
        "music_video_plan_complete",
        job_id=job_id,
        music_duration=music_duration,
        segment_seconds=segment_seconds,
        segments=len(scenes),
        media_files=len(media_files),
    )
    return ScenePlan(
        job_id=job_id,
        script=f"musical_video: {music_path.name}",
        aspect_ratio=aspect_ratio,
        music_mood="",
        music_path=str(music_path.resolve()),
        scenes=scenes,
    )


def write_music_video_plan(plan: ScenePlan, output_path: Path | str) -> Path:
    """Persist a music-video scene plan to disk as JSON."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    logger.info("music_video_plan_written", job_id=plan.job_id, path=str(output_path))
    return output_path
