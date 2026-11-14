"""B-roll skill: insert stock clips over a talking-head video.

The skill consumes a transcript (word-level or segment-level) plus a base
video. It plans B-roll moments, resolves stock clips through the asset
ingestion manager using a ranked provider fallback chain, normalizes clip
durations, and overlays the clips on top of the base video.  A JSON plan
documents every insert.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_ffmpeg.broll import insert_broll
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg
from video_editor_llm.router import ModelRouter
from video_editor_state.job import JobContext

from broll.planner import BrollMoment, extract_moments_async
from broll.ranker import rank_candidates

logger = structlog.get_logger(__name__)

DEFAULT_PROVIDERS = ["pexels", "pixabay", "coverr", "mixkit"]
DEFAULT_FALLBACK_PROVIDER = "coverr"


class BrollSkill:
    """Insert B-roll coverage over a base video based on its transcript."""

    @property
    def name(self) -> str:
        return "broll"

    def __init__(
        self,
        ctx: JobContext,
        *,
        providers: list[str] | None = None,
        fallback_provider: str = DEFAULT_FALLBACK_PROVIDER,
        min_moment_duration: float = 2.5,
        max_moment_duration: float = 6.0,
        coverage_fraction: float = 0.4,
        render_options: RenderOptions | None = None,
        router: ModelRouter | None = None,
        config: AssetProviderConfig | None = None,
        manager: AssetIngestionManager | None = None,
    ):
        self.ctx = ctx
        self.providers = providers or DEFAULT_PROVIDERS
        self.fallback_provider = fallback_provider
        self.min_moment_duration = min_moment_duration
        self.max_moment_duration = max_moment_duration
        self.coverage_fraction = coverage_fraction
        self.render_options = render_options or RenderOptions()
        self.router = router
        self._manager = manager
        self._config = config
        self.output_dir = ctx.work_dir / "broll"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _manager_for(self) -> AssetIngestionManager:
        """Return a manager, creating one if not injected."""
        if self._manager is None:
            self._manager = AssetIngestionManager(self.ctx, config=self._config)
        return self._manager

    def _normalize_clip_duration(self, source: Path, target_duration: float, output: Path) -> Path:
        """Trim or loop a clip so it matches ``target_duration`` exactly."""
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
            self.render_options.video_codec,
            "-preset",
            self.render_options.preset,
            "-crf",
            str(self.render_options.crf or 23),
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output),
        ]
        logger.debug(
            "normalizing_broll_clip",
            source=str(source),
            target=target_duration,
            output=str(output),
        )
        run_ffmpeg(cmd, check=True, timeout=RENDER_TIMEOUT_SECONDS)
        return output

    async def _resolve_clip(self, moment: BrollMoment) -> Path:
        """Resolve one clip for ``moment``, trying each provider in order.

        Raises ``RuntimeError`` if every provider fails and no fallback works.
        """
        manager = self._manager_for()
        duration = moment.end - moment.start

        providers = [p for p in self.providers if p] or DEFAULT_PROVIDERS
        if self.fallback_provider and self.fallback_provider not in providers:
            providers.append(self.fallback_provider)

        for provider in providers:
            try:
                candidates = await self._fetch_clips(manager, moment, provider, duration)
                if not candidates:
                    logger.info(
                        "broll_no_candidates",
                        moment=str(moment.start),
                        provider=provider,
                    )
                    continue
                best = rank_candidates(candidates, duration)
                if best is None:
                    continue
                normalized = self.output_dir / f"normalized_{int(moment.start * 1000)}.mp4"
                return self._normalize_clip_duration(best.path, duration, normalized)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "broll_provider_failed",
                    moment=str(moment.start),
                    provider=provider,
                    error=str(exc),
                )

        raise RuntimeError(f"All B-roll providers failed for moment at {moment.start:.2f}s")

    async def _fetch_clips(
        self,
        manager: AssetIngestionManager,
        moment: BrollMoment,
        provider: str,
        duration: float,
    ) -> list[Path]:
        """Ask the manager to resolve a video clip for ``moment`` via ``provider``.

        Returns a list of downloaded clip paths (usually one).  A per-moment
        cache directory keeps distinct providers from clobbering each other.
        """
        query = moment.query or moment.description or "b-roll video"
        asset_id = f"broll_{int(moment.start * 1000)}_{int(moment.end * 1000)}_{provider}"
        request = AssetRequest(
            asset_id=asset_id,
            job_id=self.ctx.job_id,
            scene_id=f"broll_{int(moment.start * 1000)}",
            asset_type=AssetType.VIDEO,
            prompt=query,
            provider=provider,
            params={"duration_seconds": duration, "per_page": 3},
            output_ext="mp4",
        )
        results = await manager.resolve([request])
        if not results:
            return []
        result = results[0]
        if result.resolved_path().exists():
            return [result.resolved_path()]
        return []

    async def _resolve_clips(
        self, moments: list[BrollMoment]
    ) -> tuple[list[Path], list[BrollMoment]]:
        """Resolve clips for all moments, dropping any that fail."""
        resolved: list[Path] = []
        kept: list[BrollMoment] = []
        for moment in moments:
            try:
                clip = await self._resolve_clip(moment)
                resolved.append(clip)
                kept.append(moment)
            except RuntimeError as exc:
                logger.warning("broll_moment_skipped", moment=str(moment.start), error=str(exc))
        return resolved, kept

    def _build_plan(
        self,
        moments: list[BrollMoment],
        clips: list[Path],
        output_path: Path,
    ) -> Path:
        """Write a JSON plan describing every inserted B-roll clip."""
        plan_path = self.output_dir / "plan.json"
        plan = {
            "job_id": self.ctx.job_id,
            "output": str(output_path),
            "providers": self.providers,
            "inserts": [
                {
                    **moment.to_dict(),
                    "clip": str(clip),
                }
                for moment, clip in zip(moments, clips, strict=True)
            ],
        }
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return plan_path

    async def run(
        self,
        video_path: Path | str,
        transcript: dict[str, Any],
        *,
        output_name: str = "with_broll.mp4",
    ) -> dict[str, Any]:
        """Insert B-roll clips into ``video_path`` based on the transcript.

        Args:
            video_path: Base video file.
            transcript: Dict with ``segments`` or ``words`` transcript data.
            output_name: Output filename under ``broll/``.

        Returns:
            Skill output dict with ``output`` and ``plan`` paths plus the
            list of inserted moments.
        """
        self.ctx.ensure_dirs()
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"B-roll input not found: {video_path}")

        info = probe_media(video_path)
        if info.duration is None:
            raise RuntimeError(f"Could not probe video: {video_path}")

        moments = await extract_moments_async(
            transcript,
            router=self.router,
            min_duration=self.min_moment_duration,
            max_duration=self.max_moment_duration,
            coverage_fraction=self.coverage_fraction,
        )
        # Keep moments inside the video duration.
        moments = [m for m in moments if m.start < info.duration]
        logger.info(
            "broll_planning",
            job_id=self.ctx.job_id,
            moments=len(moments),
            video=str(video_path),
        )

        if not moments:
            logger.info("no_broll_moments_detected", video=str(video_path))
            # Still produce an output (plain copy) so the pipeline can resume.
            output_path = self.output_dir / output_name
            insert_broll(video_path, [], output_path, render_options=self.render_options)
            plan_path = self._build_plan([], [], output_path)
            return {
                "output": str(output_path),
                "plan": str(plan_path),
                "moments": [],
                "count": 0,
            }

        clips, kept = await self._resolve_clips(moments)
        timeline = [
            (clip, kept[i].start, kept[i].end - kept[i].start) for i, clip in enumerate(clips)
        ]

        output_path = self.output_dir / output_name
        insert_broll(video_path, timeline, output_path, render_options=self.render_options)
        plan_path = self._build_plan(kept, clips, output_path)

        return {
            "output": str(output_path),
            "plan": str(plan_path),
            "moments": [m.to_dict() for m in kept],
            "count": len(kept),
        }
