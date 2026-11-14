"""Generate real b-roll clips for the raw-footage pipeline.

This module bridges the edit plan's b-roll inserts with the asset ingestion
system.  Each insert can be satisfied by a configured generative video provider
(currently FAL.AI), a stock-footage stub, or a local placeholder.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_core.models.edit_plan import BRollInsert
from video_editor_ffmpeg.broll import generate_placeholder
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg
from video_editor_state.job import JobContext

logger = structlog.get_logger(__name__)

DEFAULT_BROLL_PROVIDER = "placeholder"


def _format_duration(duration: float) -> str:
    """Return a short human-readable duration label."""
    if duration < 1:
        return f"{int(duration * 1000)}ms"
    return f"{duration:.1f}s"


def _normalize_clip_duration(source: Path, target_duration: float, output: Path) -> Path:
    """Trim or loop a clip so it matches ``target_duration`` exactly.

    ``-stream_loop -1`` loops the input indefinitely and ``-t`` stops at the
    requested duration.  We re-encode to avoid timestamp/GOP issues when looping.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(source),
        "-t",
        str(target_duration),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(output),
    ]
    logger.debug(
        "normalizing_broll_clip", source=str(source), target=target_duration, output=str(output)
    )
    run_ffmpeg(cmd, check=True, timeout=RENDER_TIMEOUT_SECONDS)
    return output


def _resolve_provider(insert: BRollInsert) -> str:
    """Pick the provider for an insert.

    B-roll defaults to ``placeholder`` to avoid accidental cloud spend.  A real
    provider must be explicitly set on the insert (or supplied by the caller via
    ``model_copy``) before we will call a generative video API.
    """
    provider = insert.provider.strip().lower() if insert.provider else ""
    if provider and provider != DEFAULT_BROLL_PROVIDER:
        return provider
    return DEFAULT_BROLL_PROVIDER


async def _generate_with_asset_manager(
    ctx: JobContext,
    insert: BRollInsert,
    output_path: Path,
    config: AssetProviderConfig | None,
) -> Path:
    """Resolve a real video clip through the asset ingestion manager."""
    manager = AssetIngestionManager(ctx, config=config)
    duration = insert.end - insert.start
    asset_id = f"broll_{insert.start:.6f}_{insert.end:.6f}"

    request = AssetRequest(
        asset_id=asset_id,
        job_id=ctx.job_id,
        scene_id="broll",
        asset_type=AssetType.VIDEO,
        prompt=insert.description or insert.asset_query or "b-roll video",
        provider=insert.provider,
        params={"duration_seconds": duration, **insert.params},
        output_ext=insert.output_ext or "mp4",
    )

    results: list[AssetResult] = await manager.resolve([request])
    generated_path = results[0].resolved_path()

    # The generative model may return a clip shorter or longer than the insert.
    # Normalize so overlay timing is exact.
    return _normalize_clip_duration(generated_path, duration, output_path)


async def generate_broll_clip(
    ctx: JobContext,
    insert: BRollInsert,
    output_path: Path,
    *,
    config: AssetProviderConfig | None = None,
    fallback_label: str = "B-roll",
) -> Path:
    """Resolve a single b-roll insert to a local video clip.

    Falls back to a colored placeholder if the requested provider is
    ``placeholder`` or if generation fails for any reason.  This keeps the
    downstream compositor from breaking when API keys are missing or the
    network is unavailable.
    """
    output_path = Path(output_path)
    provider = _resolve_provider(insert)

    if provider == DEFAULT_BROLL_PROVIDER:
        return generate_placeholder(
            output_path,
            insert.end - insert.start,
            label=fallback_label,
        )

    logger.info(
        "generating_broll_clip",
        job_id=ctx.job_id,
        provider=provider,
        duration=_format_duration(insert.end - insert.start),
        prompt=insert.description or insert.asset_query,
    )
    try:
        return await _generate_with_asset_manager(ctx, insert, output_path, config)
    except Exception as exc:  # noqa: BLE001 - one unavailable asset must not fail the plan
        logger.warning(
            "broll_generation_failed",
            job_id=ctx.job_id,
            provider=provider,
            error=str(exc),
            fallback=DEFAULT_BROLL_PROVIDER,
        )
        return generate_placeholder(
            output_path,
            insert.end - insert.start,
            label=fallback_label,
        )
