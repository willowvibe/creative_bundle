"""SFX placement skill: detect moments and mix context-aware sound effects.

The skill consumes a transcript (word-level or segment-level) and a base
video, finds spoken keywords that map to sound effects, generates/downloads
those SFX clips, and mixes them into the video's audio track.  The result is
a single output video plus a JSON plan describing every placed SFX.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import structlog
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.audio.elevenlabs_sfx import (
    ElevenLabsSFXProvider,
)
from video_editor_asset_ingestion.providers.audio.freesound_sfx import FreesoundSFXProvider
from video_editor_ffmpeg.broll import mix_sfx_into_video
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_llm import ClassifyClient, question_noul
from video_editor_state.job import JobContext

from sfx_placement.mapper import (
    SfxMoment,
    extract_moments_from_segments,
    extract_moments_from_words,
)

logger = structlog.get_logger(__name__)

DEFAULT_SFX_DURATION_SECONDS = 3.0
DEFAULT_SFX_GAIN_DB = -8.0


class SfxPlacementSkill:
    """Place context-aware sound effects into a video."""

    @property
    def name(self) -> str:
        return "sfx_placement"

    def __init__(
        self,
        ctx: JobContext,
        *,
        provider: str = "freesound_sfx",
        sfx_duration_seconds: float = DEFAULT_SFX_DURATION_SECONDS,
        gain_db: float = DEFAULT_SFX_GAIN_DB,
        render_options: RenderOptions | None = None,
        classify_client: ClassifyClient | None = None,
        jev_max_placements: int = 6,
        jev_min_score: float = 0.5,
        manager: AssetIngestionManager | None = None,
    ):
        self.ctx = ctx
        self.provider = provider
        self.sfx_duration_seconds = sfx_duration_seconds
        self.gain_db = gain_db
        self.render_options = render_options or RenderOptions()
        self.classify_client = classify_client
        self.jev_max_placements = jev_max_placements
        self.jev_min_score = jev_min_score
        #: When supplied, generation goes through the manager so the job's
        #: budget and the provider chain apply. Without it (standalone callers
        #: and the offline tests) the skill talks to ElevenLabs directly.
        self.manager = manager
        self.output_dir = ctx.work_dir / "sfx_placement"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def _filter_moments_with_jev(
        self,
        moments: list[SfxMoment],
    ) -> list[SfxMoment]:
        """Keep only the moments a Jev classifier judges SFX-worthy.

        The keyword mapper is deliberately high-recall (every spoken keyword
        match), so it over-triggers. Jev suppresses the gratuitous placements
        in a single batched call, capped at ``jev_max_placements`` by
        confidence. Falls back to the unfiltered list when no classifier is
        available or the call fails.
        """
        if not moments:
            return moments
        client = self.classify_client
        owns_client = False
        if client is None:
            try:
                client = ClassifyClient.from_config()
            except Exception:  # noqa: BLE001 - config probing must not raise
                client = ClassifyClient()
            owns_client = True
        try:
            if not client.available():
                return moments
            questions: dict[str, Any] = {
                f"m{i}": question_noul(
                    "Would a short sound effect at `context` genuinely enhance this "
                    "video moment, or would it be gratuitous?",
                    data={"context": f"[{m.keyword}] {m.text}"},
                    true_label="Enhance",
                    false_label="Gratuitous",
                )
                for i, m in enumerate(moments)
            }
            body = await client.classify({"state": "SFX placement review."}, questions)
            answers = body.get("answers") or {}
            scored: list[tuple[float, int, SfxMoment]] = []
            for i, moment in enumerate(moments):
                prob = float((answers.get(f"m{i}") or {}).get("noul", 1.0) or 0.0)
                if prob >= self.jev_min_score:
                    scored.append((prob, i, moment))
            if not scored:
                return []
            scored.sort(key=lambda item: (-item[0], item[1]))
            kept = [moment for _, _, moment in scored[: self.jev_max_placements]]
            kept.sort(key=lambda m: m.start)
            logger.info("sfx_jev_filter", considered=len(moments), kept=len(kept))
            return kept
        except Exception as exc:  # noqa: BLE001 - fall back to unfiltered
            logger.warning("sfx_jev_filter_failed", error=str(exc))
            return moments
        finally:
            if owns_client:
                await client.close()

    def _extract_moments(
        self,
        transcript: dict[str, Any],
    ) -> list[SfxMoment]:
        """Extract SFX moments from word-level or segment-level transcript."""
        words = transcript.get("words") or transcript.get("word_segments") or []
        if words and "word" in words[0]:
            return extract_moments_from_words(words)

        segments = transcript.get("segments") or []
        if segments and "text" in segments[0]:
            return extract_moments_from_segments(segments)

        return []

    async def _generate_sfx_clip(
        self,
        moment: SfxMoment,
        output_path: Path,
    ) -> Path:
        """Generate or fetch a single SFX clip for a moment.

        With a manager, the request goes through the job's provider chain and
        budget (the paid path MCP and the CLI use). The direct provider call is
        kept for standalone callers and the offline tests.
        """
        if self.manager is not None:
            request = AssetRequest(
                job_id=self.ctx.job_id,
                asset_id=f"sfx_{moment.keyword}_{int(moment.start * 1000)}",
                scene_id="",
                asset_type=AssetType.SFX,
                prompt=moment.prompt,
                provider=self.provider,
                params={"duration_seconds": self.sfx_duration_seconds},
                output_ext="mp3",
            )
            batch = await self.manager.resolve_partial([request])
            if not batch.results:
                reason = batch.failures[0][1] if batch.failures else "no result"
                raise RuntimeError(f"SFX generation failed for {moment.keyword!r}: {reason}")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(batch.results[0].local_path, output_path)
            return output_path

        if self.provider in ("elevenlabs_sfx", "freesound_sfx"):
            # elevenlabs_sfx is kept so an explicit request fails with the voice-over-only error.
            if self.provider == "freesound_sfx":
                provider: FreesoundSFXProvider | ElevenLabsSFXProvider = FreesoundSFXProvider(
                    ProviderConfig(api_key_env="FREESOUND_API_KEY")
                )
            else:
                provider = ElevenLabsSFXProvider(ProviderConfig())
            payload = {
                "job_id": self.ctx.job_id,
                "asset_id": f"sfx_{moment.keyword}_{int(moment.start * 1000)}",
                "scene_id": "",
                "asset_type": "sfx",
                "prompt": moment.prompt,
                "params": {"duration_seconds": self.sfx_duration_seconds},
            }
            # Minimal AssetRequest-compatible dict for the provider.
            asset_request = AssetRequest.model_validate(payload)
            result = await provider.generate(asset_request, output_path)
            await (
                provider.aclose()
                if isinstance(provider, FreesoundSFXProvider)
                else provider.close()
            )
            return Path(result.local_path or output_path)

        raise NotImplementedError(f"SFX provider {self.provider!r} is not supported")

    def _build_plan(
        self,
        moments: list[SfxMoment],
        sfx_paths: list[Path],
        output_path: Path,
    ) -> Path:
        """Write a JSON plan describing the placed SFX."""
        plan_path = self.output_dir / "plan.json"
        plan = {
            "job_id": self.ctx.job_id,
            "output": str(output_path),
            "provider": self.provider,
            "gain_db": self.gain_db,
            "sfx": [
                {
                    **moment.to_dict(),
                    "clip": str(sfx_path),
                }
                for moment, sfx_path in zip(moments, sfx_paths, strict=True)
            ],
        }
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return plan_path

    async def run(
        self,
        video_path: Path | str,
        transcript: dict[str, Any],
        *,
        output_name: str = "with_sfx.mp4",
    ) -> dict[str, Any]:
        """Place SFX into ``video_path`` based on the transcript.

        Args:
            video_path: Base video file.
            transcript: Dict with either ``words``/``word_segments`` (word-level)
                or ``segments`` (segment-level) transcript data.
            output_name: Output filename under ``sfx_placement/``.

        Returns:
            Skill output dict with ``output`` and ``plan`` paths plus the
            list of placed SFX moments.
        """
        self.ctx.ensure_dirs()
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"SFX input not found: {video_path}")

        info = probe_media(video_path)
        if info.duration is None:
            raise RuntimeError(f"Could not probe video: {video_path}")

        moments = await self._filter_moments_with_jev(self._extract_moments(transcript))
        if not moments:
            logger.info("no_sfx_moments_detected", video=str(video_path))
            # Fall through to mix with empty timeline so output still exists.

        # Generate SFX clips.
        sfx_paths: list[Path] = []
        timeline: list[tuple[Path, float]] = []
        for moment in moments:
            clip_path = self.output_dir / f"{moment.keyword}_{int(moment.start * 1000)}.mp3"
            await self._generate_sfx_clip(moment, clip_path)
            sfx_paths.append(clip_path)
            timeline.append((clip_path, moment.start))

        output_path = self.output_dir / output_name
        mix_sfx_into_video(
            video_path,
            timeline,
            output_path,
            sfx_gain_db=self.gain_db,
            render_options=self.render_options,
        )
        plan_path = self._build_plan(moments, sfx_paths, output_path)

        return {
            "output": str(output_path),
            "plan": str(plan_path),
            "moments": [m.to_dict() for m in moments],
            "count": len(moments),
        }
