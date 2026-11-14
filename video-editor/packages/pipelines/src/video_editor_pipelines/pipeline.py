"""Skills-first pipeline orchestrator for the new video editor architecture."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import structlog
from captions.captions import CaptionsSkill
from export.export import ExportSkill
from export.models import ExportStyle
from graphics.graphics import GraphicsSkill
from music.music import MusicSkill
from review.review import PostRenderReviewSkill
from rough_cut.rough_cut import RoughCutSkill
from skills.skill_protocol import SkillProtocol
from verify.models import RequirementsSpec
from verify.verify import VerificationError, VerifySkill
from video_editor_asset_ingestion.budget import BudgetGovernor
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.scene_assets import (
    alignment_request,
    attach_results_to_scene,
    forced_alignment_response_to_transcript,
    local_media_requests_for_scene,
    music_request,
    sfx_requests_for_scene,
)
from video_editor_config import Settings
from video_editor_core.constants import REPO_ROOT
from video_editor_core.models.scene_plan import ScenePlan
from video_editor_core.render_graph import AudioTrack
from video_editor_core.render_graph_builder import RenderGraphBuilder
from video_editor_core.renderer import render_graph
from video_editor_core.safe_area import safe_area_violations
from video_editor_core.scene_plan_composition import (
    render_scene_plan as render_scene_plan_hyperframes,
)
from video_editor_core.timeline import EDL, Segment
from video_editor_ffmpeg.broll import mix_sfx_into_video
from video_editor_ffmpeg.captions import (
    drawtext_captions,
    generate_caption_segments,
    remap_transcript_to_cuts,
)
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_state.job import JobContext
from video_editor_state.ledger_hooks import (
    record_pipeline_complete,
    record_pipeline_start,
    record_stage_done,
    record_stage_start,
)

from video_editor_pipelines.music_video_planner import (
    build_music_video_plan,
    write_music_video_plan,
)

logger = structlog.get_logger(__name__)


def render_scene_plan(
    ctx: JobContext,
    plan_path: Path | str,
    output_path: Path | str,
    composition_id: str | None = None,
    force_bundle: bool = False,
) -> Path:
    """Render a resolved scene plan to an MP4 via HyperFrames.

    Kept as a module-level shim with the historical Remotion signature so
    callers/tests keep working; the Remotion bundle backend was retired in
    favour of the HyperFrames HTML composition.
    """
    del composition_id, force_bundle  # Remotion-only knobs, ignored
    return render_scene_plan_hyperframes(plan_path, output_path, ctx.work_dir / "hyperframes")


# Auto-discovered when --music-prompt was used. Matches the path
# written by ``ve music-generate`` and the ``_handle_music_generate``
# worker handler. This path is scoped to the job work directory, not the
# repo root, because generated music is per-job output.
DEFAULT_GENERATED_MUSIC_PATH = Path("music/generated_music.mp3")


class SkillsPipeline:
    """Chain the v2 skills into a single end-to-end run."""

    def __init__(
        self,
        ctx: JobContext,
        preset: str = "short_explainer",
        presets_dir: Path | str | None = None,
        music_path: Path | str | None = None,
        music_prompt: str = "",
        music_provider: str = "poyo_music",
        music_duration_seconds: int = 30,
        sfx_provider: str = "",
        sfx_duration_seconds: float = 5.0,
        sfx_gain_db: float = -6.0,
        config_path: Path | str | None = None,
        skip_hyperframes_lint: bool = False,
        transition: str = "none",
        transition_duration: float = 0.3,
        budget: BudgetGovernor | None = None,
        skills: list[SkillProtocol] | None = None,
        # raw_to_video pipeline options (Task 10). Each enables an optional
        # stage in the vlog/talking-head pipeline branch.
        run_audio_cleanup: bool = False,
        run_transitions: bool = False,
        run_reframe: bool = False,
        run_broll: bool = False,
        run_sfx_placement: bool = False,
        reframe_aspect: str = "9:16",
        broll_providers: list[str] | None = None,
        render_engine: str = "auto",
        final: bool = False,
        skip_music: bool = False,
    ):
        self.ctx = ctx
        self.preset = preset
        self.music_prompt = music_prompt
        self.music_provider = music_provider
        self.music_duration_seconds = music_duration_seconds
        self.sfx_provider = sfx_provider
        self.sfx_duration_seconds = sfx_duration_seconds
        self.sfx_gain_db = sfx_gain_db
        self.skip_hyperframes_lint = skip_hyperframes_lint
        self.transition = transition
        self.transition_duration = transition_duration
        self.budget = budget
        # raw_to_video optional-stage flags (Task 10).
        self.run_audio_cleanup = run_audio_cleanup
        self.run_transitions = run_transitions
        self.run_reframe = run_reframe
        self.run_broll = run_broll
        self.run_sfx_placement = run_sfx_placement
        self.reframe_aspect = reframe_aspect
        self.broll_providers = broll_providers or ["pexels", "pixabay", "coverr", "mixkit"]
        # Canonical render path: flatten graphics + captions into one graph
        # and render once (the legacy per-stage path was retired in A3).
        self.render_engine = render_engine
        self.skip_music = skip_music
        # Final renders use ``assets.image_chain_final`` when configured.
        self.final = final
        # Issue B: ``ve run --config`` was silently dropped on the file-path
        # pipeline. Resolve it once here and inject ``settings.config.assets``
        # into every manager, so music/SFX generation cannot re-read the
        # default config (env var VIDEO_EDITOR_CONFIG still works when this is
        # None — see video_editor_core.config_paths).
        self.config_path = Path(config_path) if config_path else None
        # Load the unified project config once so we can apply config-driven
        # defaults (presets dir, default music path, output format) without
        # re-reading the YAML file at every decision point.
        self.settings = Settings.load(self.config_path)
        self.project_config = self.settings.config
        # Let config drive engine selection unless the caller forced one.
        configured_engine = getattr(self.project_config.render, "engine", "") or "auto"
        if self.render_engine == "auto":
            self.render_engine = configured_engine
        self.presets_dir = self._resolve_presets_dir(presets_dir)
        self.music_path = self._resolve_music_path(music_path)
        self.cache_dir = self._resolve_cache_dir()
        self.render_options = RenderOptions.from_mapping(
            self.project_config.render.model_dump(
                include={
                    "video_codec",
                    "audio_codec",
                    "video_bitrate",
                    "audio_bitrate",
                    "fps",
                    "crf",
                    "width",
                    "height",
                    "preset",
                    "hwaccel",
                }
            )
        )
        self.render_options.skip_hyperframes_lint = self.skip_hyperframes_lint
        self.render_options.transition = self.transition
        self.render_options.transition_duration = self.transition_duration
        self._skills = skills

    @property
    def _repo_root(self) -> Path:
        """Repo root for resolving config-relative paths.

        When a config file is supplied, its parent directory is the logical
        repo root (this matches tests that write ``config.yaml`` into a
        temporary directory). Otherwise fall back to the package's repo root.
        """
        if self.config_path is None:
            return REPO_ROOT
        return self.config_path.resolve().parent

    def _resolve_presets_dir(self, presets_dir: Path | str | None) -> Path | None:
        if presets_dir is not None:
            return Path(presets_dir)
        return self.project_config.presets_dir(repo_root=self._repo_root)

    def _resolve_music_path(self, music_path: Path | str | None) -> Path | None:
        if music_path is not None:
            return Path(music_path)
        cfg_path = self.project_config.default_music_path(repo_root=self._repo_root)
        if cfg_path is not None and cfg_path.exists():
            return cfg_path
        return None

    def _resolve_cache_dir(self) -> Path:
        """Return the asset cache directory, contained inside the job work dir.

        A configured absolute or relative-traversal cache_dir could otherwise
        escape the job directory and read or poison another tenant's cache.
        """
        configured = self.project_config.render.cache_dir
        work_dir = self.ctx.work_dir.resolve()
        if not configured:
            return work_dir / ".cache"
        candidate = Path(configured)
        resolved = (
            candidate.resolve() if candidate.is_absolute() else (work_dir / candidate).resolve()
        )
        if resolved.is_relative_to(work_dir):
            return resolved
        logger.warning(
            "render.cache_dir escapes work_dir; falling back to work_dir/.cache",
            configured=configured,
            work_dir=str(work_dir),
        )
        return work_dir / ".cache"

    @staticmethod
    def _safe_output_name(name: str) -> str:
        """Reject path traversal in a rendered output filename."""
        if not name or "/" in name or "\\" in name or ".." in name or name in (".", ".."):
            raise ValueError(
                f"render.output_name_template must expand to a plain filename; got {name!r}"
            )
        return name

    def _effective_music_path(self) -> Path | None:
        """Resolve the music file to fold into the render graph, if any."""
        if self.music_path is not None:
            return self.music_path
        cfg_path = self.project_config.default_music_path(repo_root=self._repo_root)
        if cfg_path is not None and cfg_path.exists():
            return cfg_path
        workdir_default = self.ctx.work_dir / DEFAULT_GENERATED_MUSIC_PATH
        if workdir_default.exists():
            return workdir_default
        return None

    def _should_run_music(self) -> bool:
        """Return True when music mixing should be attempted."""
        if self.skip_music:
            return False
        if self.music_path is not None:
            return True
        if self.music_prompt:
            # We will generate a track below; treat the run as music-enabled.
            return True
        if (self.ctx.work_dir / DEFAULT_GENERATED_MUSIC_PATH).exists():
            # Auto-discover a track left behind by ``ve music-generate``.
            return True
        # Check the config-driven default music path.
        cfg_path = self.project_config.default_music_path(repo_root=self._repo_root)
        return cfg_path is not None and cfg_path.exists()

    async def _generate_music(self) -> Path | None:
        """Generate one AI music track and save it for MusicSkill to pick up.

        Returns the path of the generated track, or None if generation
        was skipped.
        """
        if not self.music_prompt:
            return None
        if self.music_provider == "elevenlabs_music":
            # Bug #229: ElevenLabs Music is ~6x the cost of the PoYo path and is
            # never a sensible default. Warn loudly; callers should use
            # poyo_music (~$0.10/track) or ace_step (free/local).
            logger.warning(
                "elevenlabs_music_costly",
                provider=self.music_provider,
                hint="ElevenLabs Music ~$0.15/min; prefer poyo_music or ace_step.",
            )
        target = self.ctx.work_dir / DEFAULT_GENERATED_MUSIC_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        config = self.settings.config.assets
        manager = AssetIngestionManager(
            self.ctx,
            config=config,
            cache_dir=self.cache_dir,
            budget=self.budget,
            final=self.final,
        )
        request = music_request(
            job_id=self.ctx.job_id,
            prompt=self.music_prompt,
            duration_seconds=self.music_duration_seconds,
            provider=self.music_provider,
        )
        results = await manager.resolve([request])
        shutil.copyfile(Path(results[0].local_path), target)
        logger.info(
            "pipeline_music_generated",
            path=str(target),
            provider=self.music_provider,
            duration_ms=results[0].duration_ms,
        )
        return target

    async def _generate_and_mix_sfx(self, base_video: Path) -> Path:
        """Generate per-scene SFX and mix them into ``base_video``.

        When ``self.sfx_provider`` is empty, returns ``base_video``
        unchanged so the caller can pass the result straight to
        MusicSkill without branching.
        """
        if not self.sfx_provider:
            return base_video
        # Read the scene plan the v2 pipeline produced.
        plan_path = self.ctx.work_dir / "plan" / "scene_plan.json"
        if not plan_path.exists():
            logger.warning(
                "pipeline_sfx_skipped",
                reason="missing_scene_plan",
                plan=str(plan_path),
            )
            return base_video
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        config = self.settings.config.assets
        manager = AssetIngestionManager(
            self.ctx,
            config=config,
            cache_dir=self.cache_dir,
            budget=self.budget,
            final=self.final,
        )
        requests = [
            req
            for scene in plan.scenes
            for req in sfx_requests_for_scene(
                scene,
                self.ctx.job_id,
                sfx_provider=self.sfx_provider,
                duration_seconds=self.sfx_duration_seconds,
            )
        ]
        if not requests:
            return base_video
        # Bug #83: tolerate partial SFX batch failures instead of failing
        # the whole job when a single scene's sound effect cannot be generated.
        batch = await manager.resolve_partial(requests)
        if batch.failures:
            logger.warning(
                "pipeline_sfx_partial_failures",
                failed=batch.failed_asset_ids,
                success=batch.success_asset_ids,
            )
        sfx_dir = self.ctx.work_dir / "sfx"
        sfx_dir.mkdir(parents=True, exist_ok=True)
        sfx_map: dict[str, Path] = {}
        for result in batch.results:
            out_path = sfx_dir / f"{result.scene_id}.mp3"
            shutil.copyfile(Path(result.local_path), out_path)
            sfx_map[result.scene_id] = out_path

        # Build the (path, start_time) timeline. Start times are the
        # cumulative scene durations (matches the ``ve sfx-mix``
        # ordering).
        timeline: list[tuple[Path, float]] = []
        cursor = 0.0
        for scene in plan.scenes:
            sfx_path = sfx_map.get(scene.id)
            if sfx_path is None:
                cursor += scene.duration_seconds
                continue
            timeline.append((sfx_path, cursor))
            cursor += scene.duration_seconds

        output_path = self.ctx.work_dir / "broll" / "with_broll_sfx.mp4"
        if not timeline:
            return base_video
        mix_sfx_into_video(
            base_video=base_video,
            sfx_timeline=timeline,
            output_path=output_path,
            sfx_gain_db=self.sfx_gain_db,
            render_options=self.render_options,
        )
        logger.info(
            "pipeline_sfx_mixed",
            count=len(timeline),
            gain_db=self.sfx_gain_db,
            output=str(output_path),
        )
        return output_path

    # ------------------------------------------------------------------
    # raw_to_video optional stages (Task 10). Each method is a no-op-free
    # helper that produces the next base video in the pipeline chain. The
    # run_async caller owns resume/ledger bookkeeping.
    # ------------------------------------------------------------------

    def _load_transcript(self) -> dict[str, Any]:
        """Read the transcript JSON written by the rough cut stage."""
        transcript_path = self.ctx.artifact("transcript", "transcript.json")
        if not transcript_path.exists():
            return {}
        with open(transcript_path, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
            return data

    def _rewrite_edl_single_segment(self, base: Path) -> EDL:
        """Replace the EDL with a single full-duration segment over ``base``.

        Transitions and reframe both change the base video timeline (transitions
        overlap adjacent segments; reframe re-encodes the source), so the
        original source-relative EDL no longer matches the new base. Rewriting
        to one continuous segment keeps graphics/captions consistent with the
        composed video.
        """
        info = probe_media(base)
        duration = info.duration or 0.0
        edl = EDL(
            job_id=self.ctx.job_id,
            source_path=str(base),
            segments=[
                Segment(
                    id="seg_0000",
                    source_start=0.0,
                    source_end=duration,
                    timeline_start=0.0,
                    timeline_end=duration,
                    label="composed",
                )
            ],
        )
        edl_path = self.ctx.artifact("plan", "edl.json")
        edl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(edl_path, "w", encoding="utf-8") as f:
            json.dump(edl.model_dump(), f, indent=2)
        return edl

    def _run_audio_cleanup(self, source: Path) -> Path:
        """Run the audio cleanup pre-stage on ``source`` and return the cleaned video."""
        from audio_cleanup.audio_cleanup import AudioCleanupSkill

        skill = AudioCleanupSkill(self.ctx, render_options=self.render_options)
        return skill.run(source, output_name="cleaned.mp4", return_video=True)

    def _run_transitions(self, edl: EDL) -> Path:
        """Cut EDL segments from the source and render a transitioned video.

        Returns the path to ``transitions/with_transitions.mp4``.
        """
        from transitions.transitions import TransitionSkill
        from video_editor_ffmpeg.cuts import apply_cuts

        seg_dir = self.ctx.work_dir / "transitions" / "segments"
        seg_dir.mkdir(parents=True, exist_ok=True)
        source = Path(edl.source_path)
        paths: list[Path] = []
        for seg in edl.segments:
            out = seg_dir / f"{seg.id}.mp4"
            if not out.exists():
                apply_cuts(
                    source,
                    [(seg.source_start, seg.source_end)],
                    out,
                    render_options=self.render_options,
                )
            paths.append(out)
        skill = TransitionSkill(self.ctx, render_options=self.render_options)
        result = skill.run(paths, edl=edl, output_name="with_transitions.mp4")
        return Path(result["output"])

    def _run_reframe(self, base: Path) -> Path:
        """Reframe ``base`` to the target vertical aspect ratio."""
        from reframe.reframe import ReframeSkill

        skill = ReframeSkill(
            self.ctx,
            target_aspect=self.reframe_aspect,
            render_options=self.render_options,
        )
        return skill.run(base, output_name="reframed.mp4")

    async def _run_broll(self, base: Path) -> Path:
        """Overlay B-roll clips on ``base`` driven by the rough-cut transcript."""
        from broll.broll import BrollSkill

        transcript = self._load_transcript()
        config = self.settings.config.assets
        skill = BrollSkill(
            self.ctx,
            providers=self.broll_providers,
            render_options=self.render_options,
            config=config,
        )
        result = await skill.run(base, transcript, output_name="with_broll.mp4")
        return Path(result["output"])

    async def _run_sfx_placement(self, base: Path) -> Path:
        """Place context-aware SFX on ``base`` driven by the rough-cut transcript."""
        from sfx_placement.sfx_placement import SfxPlacementSkill

        transcript = self._load_transcript()
        manager = AssetIngestionManager(
            self.ctx,
            config=self.settings.config.assets,
            cache_dir=self.cache_dir,
            budget=self.budget,
            final=self.final,
        )
        skill = SfxPlacementSkill(
            self.ctx,
            sfx_duration_seconds=self.sfx_duration_seconds,
            gain_db=self.sfx_gain_db,
            render_options=self.render_options,
            manager=manager,
        )
        result = await skill.run(base, transcript, output_name="with_sfx.mp4")
        return Path(result["output"])

    def _graphics_inputs(self, base_video: Path) -> tuple[EDL, Path]:
        """Map the source-relative EDL/transcript onto the cut ``base_video``.

        ``rough_cut/locked.mp4`` is a concatenation of the kept source
        segments, but the saved EDL still uses source timestamps. The graphics
        planner renders against ``base_video``, so it must receive segment
        times relative to that cut, and word timings shifted to the same
        timeline. Without this, a segment that starts after the last source
        cut is clamped to an empty/truncated tail.
        """
        edl_path = self.ctx.artifact("plan", "edl.json")
        with open(edl_path, encoding="utf-8") as f:
            edl = EDL(**json.load(f))
        duration = probe_media(base_video).duration or max(
            (seg.timeline_end for seg in edl.segments), default=0.0
        )
        segments = []
        for seg in edl.segments:
            start = min(seg.timeline_start, duration)
            end = min(seg.timeline_end, duration)
            if end - start <= 0.001:
                continue
            segments.append(
                seg.model_copy(
                    update={
                        "source_start": start,
                        "source_end": end,
                        "timeline_start": start,
                        "timeline_end": end,
                    }
                )
            )
        cut_edl = edl.model_copy(update={"source_path": str(base_video), "segments": segments})
        transcript_path = self.ctx.artifact("transcript", "transcript.json")
        if transcript_path.exists():
            with open(transcript_path, encoding="utf-8") as f:
                transcript = json.load(f)
            remapped = remap_transcript_to_cuts(
                transcript, [(seg.source_start, seg.source_end) for seg in edl.segments]
            )
        else:
            remapped = {"words": [], "segments": []}
        cut_transcript = self.ctx.artifact("graphics", "cut_transcript.json")
        cut_transcript.parent.mkdir(parents=True, exist_ok=True)
        cut_transcript.write_text(json.dumps(remapped, indent=2), encoding="utf-8")
        return cut_edl, cut_transcript

    def _render_composition_graph(
        self,
        graphics: GraphicsSkill,
        captions: CaptionsSkill,
        base_video: Path,
        output: Path,
        music_path: Path | None = None,
        *,
        edl: EDL | None = None,
        transcript_path: Path | None = None,
    ) -> Path:
        """Flatten graphics + captions (+ music) into one render graph and render once.

        Replaces the graphics (per-segment) + captions (per-segment) renders
        and the separate music mix with a single whole-timeline composition.
        """
        plan = graphics.plan(
            edl=edl,
            preset_name=self.preset,
            source_video=base_video,
            transcript_path=transcript_path,
        )
        caption_layers = captions.plan_graph_layers(plan)
        builder = (
            RenderGraphBuilder.from_source(
                self.ctx.job_id,
                str(base_video),
                width=plan.width,
                height=plan.height,
                fps=plan.fps,
                options=self.render_options,
            )
            .extend_graphics_plan(plan)
            .add_overlays(caption_layers)
        )
        if music_path is not None:
            builder.add_audio(
                AudioTrack(kind="music", source=str(music_path), duck=True, loop=True)
            )
        graph = builder.build()
        unsafe = safe_area_violations(graph.overlays, graph.width, graph.height)
        if unsafe:
            logger.warning("text_outside_safe_area", count=len(unsafe), layers=unsafe[:5])
        graph_path = self.ctx.artifact("plan", "render_graph.json")
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        graph_path.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        render_graph(graph, output, engine=self.render_engine)
        logger.info(
            "pipeline_render_graph_complete",
            video=str(output),
            overlays=len(graph.overlays),
            cuts=len(graph.cuts),
            music=bool(music_path),
        )
        return output

    async def run_async(
        self,
        source_path: Path | str,
        transcript_path: Path | str | None = None,
        output_name: str | None = None,
        run_qa: bool = False,
        loudness_target_lufs: float | None = None,
        expected_aspect_ratio: str = "16:9",
        run_verify: bool = False,
        requirements: RequirementsSpec | None = None,
        run_post_render_review: bool = False,
    ) -> dict[str, Any]:
        """Async implementation of the full skills pipeline.

        Run rough-cut → graphics → captions → optional SFX → music → export.
        When ``run_qa=True``, the pipeline runs ``QASkill.run`` on the
        captioned output between the music and export stages. If the QA
        report has any failing check, the build raises and the export
        stage is skipped. ``loudness_target_lufs`` is forwarded to the
        QA skill so the caller can override the default -16 LUFS target
        (e.g. to match a synthetic test signal). ``expected_aspect_ratio``
        is forwarded to the QA skill so a 9:16 short-form render is
        validated against the right aspect label.

        When ``run_post_render_review=True`` the exported final video is
        run through a deterministic post-render review gate. Critical
        failures raise; quality issues are reported but do not block the
        build.
        """
        self.ctx.ensure_dirs()
        manifest = self.ctx.manifest()
        record_pipeline_start(
            self.ctx,
            "skills",
            template=self.preset,
            budget_limit=self.budget.limit if self.budget else None,
        )

        def _mark_stage(stage: str, outputs: dict[str, Any]) -> None:
            record_stage_start(self.ctx, stage)
            manifest.stage_done(stage, outputs)
            self.ctx.store.save()
            record_stage_done(self.ctx, stage, outputs)

        # 0. Optional audio cleanup pre-stage (raw_to_video). Runs on the
        # source before the rough cut so silence detection and transcription
        # see cleaner audio. The cleaned video becomes the rough-cut source.
        pipeline_source: Path | str = source_path
        if self.run_audio_cleanup:
            cleaned_path = self.ctx.work_dir / "audio_cleanup" / "cleaned.mp4"
            if manifest.is_stage_complete("audio_cleanup") and cleaned_path.exists():
                pipeline_source = cleaned_path
                logger.info(
                    "pipeline_stage_resumed",
                    stage="audio_cleanup",
                    video=str(pipeline_source),
                )
            else:
                pipeline_source = self._run_audio_cleanup(Path(source_path))
                logger.info("pipeline_audio_cleanup_complete", video=str(pipeline_source))
                _mark_stage("audio_cleanup", {"video": str(pipeline_source)})

        # 1. Rough cut
        locked_path = self.ctx.work_dir / "rough_cut" / "locked.mp4"
        if manifest.is_stage_complete("rough_cut") and locked_path.exists():
            locked = locked_path
            logger.info("pipeline_stage_resumed", stage="rough_cut", locked=str(locked))
        else:
            rough_cut = RoughCutSkill(self.ctx, render_options=self.render_options)
            locked = rough_cut.run(pipeline_source, transcript_path=transcript_path)
            logger.info("pipeline_rough_cut_complete", locked=str(locked))
            _mark_stage("rough_cut", {"locked": str(locked)})

        # 1b. raw_to_video base-video chain: optional transitions → reframe →
        # broll. Each optional stage transforms the current base video; the
        # graphics stage then cuts from the final base instead of the bare
        # rough cut. Transitions and reframe rewrite the EDL to a single
        # segment over the composed video (their timelines differ from the
        # original source); broll overlays in place and preserves the timeline.
        base_video: Path = locked
        if self.run_transitions:
            transitions_path = self.ctx.work_dir / "transitions" / "with_transitions.mp4"
            if manifest.is_stage_complete("transitions") and transitions_path.exists():
                base_video = transitions_path
                logger.info(
                    "pipeline_stage_resumed",
                    stage="transitions",
                    video=str(base_video),
                )
            else:
                edl_path = self.ctx.artifact("plan", "edl.json")
                with open(edl_path, encoding="utf-8") as f:
                    current_edl = EDL(**json.load(f))
                base_video = self._run_transitions(current_edl)
                self._rewrite_edl_single_segment(base_video)
                logger.info("pipeline_transitions_complete", video=str(base_video))
                _mark_stage("transitions", {"video": str(base_video)})

        if self.run_reframe:
            reframed_path = self.ctx.work_dir / "reframe" / "reframed.mp4"
            if manifest.is_stage_complete("reframe") and reframed_path.exists():
                base_video = reframed_path
                logger.info(
                    "pipeline_stage_resumed",
                    stage="reframe",
                    video=str(base_video),
                )
            else:
                base_video = self._run_reframe(base_video)
                self._rewrite_edl_single_segment(base_video)
                logger.info("pipeline_reframe_complete", video=str(base_video))
                _mark_stage("reframe", {"video": str(base_video)})

        if self.run_broll:
            broll_path = self.ctx.work_dir / "broll" / "with_broll.mp4"
            if manifest.is_stage_complete("broll") and broll_path.exists():
                base_video = broll_path
                logger.info(
                    "pipeline_stage_resumed",
                    stage="broll",
                    video=str(base_video),
                )
            else:
                base_video = await self._run_broll(base_video)
                logger.info("pipeline_broll_complete", video=str(base_video))
                _mark_stage("broll", {"video": str(base_video)})

        # 2. Graphics + 3. Captions: flatten the composition plan and caption
        # layers into one graph and render once. The legacy per-stage renderer
        # was retired in A3 after HyperFrames dropped JSON-plan rendering.
        graphics_edl, graphics_transcript = self._graphics_inputs(base_video)
        graphics = GraphicsSkill(
            self.ctx,
            presets_dir=self.presets_dir,
            render_options=self.render_options,
        )
        captions = CaptionsSkill(self.ctx, graphics_skill=graphics)
        graph_composed = self.ctx.work_dir / "graph" / "composed.mp4"
        graph_resumes = (
            manifest.is_stage_complete("graphics")
            and manifest.is_stage_complete("captions")
            and graph_composed.exists()
        )

        # Resolve music before the render so it folds into the single composition
        # (avoids a separate music re-encode). Only when not resuming, so a
        # generated track is never re-billed on a resume.
        folded_music: Path | None = None
        if not graph_resumes and self._should_run_music():
            if self.music_prompt:
                generated = await self._generate_music()
                if generated is not None:
                    self.music_path = generated
            candidate = self._effective_music_path()
            if candidate is not None and candidate.exists():
                folded_music = candidate

        music_folded = False
        if graph_resumes:
            graphics_video = graph_composed
            captions_video = graph_composed
            music_folded = bool(manifest.stage_outputs("captions").get("music_folded"))
            logger.info("pipeline_stage_resumed", stage="render_graph", video=str(graph_composed))
        else:
            graphics_video = self._render_composition_graph(
                graphics,
                captions,
                base_video,
                graph_composed,
                music_path=folded_music,
                edl=graphics_edl,
                transcript_path=graphics_transcript,
            )
            captions_video = graphics_video
            music_folded = folded_music is not None
            _mark_stage("graphics", {"video": str(graphics_video)})
            _mark_stage(
                "captions",
                {
                    "video": str(captions_video),
                    "graph": str(graph_composed),
                    "music_folded": music_folded,
                },
            )

        # 3a. Optional SFX placement (raw_to_video). Runs on the captioned
        # video driven by the rough-cut transcript; its output becomes the
        # music mix input so the SFX survives the music stage.
        sfx_input: Path = captions_video
        if self.run_sfx_placement:
            sfx_placement_path = self.ctx.work_dir / "sfx_placement" / "with_sfx.mp4"
            if manifest.is_stage_complete("sfx_placement") and sfx_placement_path.exists():
                sfx_input = sfx_placement_path
                logger.info(
                    "pipeline_stage_resumed",
                    stage="sfx_placement",
                    input=str(sfx_input),
                )
            else:
                sfx_input = await self._run_sfx_placement(captions_video)
                logger.info("pipeline_sfx_placement_complete", input=str(sfx_input))
                _mark_stage("sfx_placement", {"input": str(sfx_input)})

        # 3b. Optional SFX — generate per-scene clips and mix them into
        # the captioned video. When the provider is empty, this is a
        # no-op (returns ``captions_video`` unchanged). The mixed
        # output becomes the music mix's input so the SFX survives
        # the music stage instead of being overwritten.
        if self.sfx_provider:
            sfx_path = self.ctx.work_dir / "broll" / "with_broll_sfx.mp4"
            if manifest.is_stage_complete("sfx") and sfx_path.exists():
                sfx_input = sfx_path
                logger.info("pipeline_stage_resumed", stage="sfx", input=str(sfx_input))
            else:
                sfx_input = await self._generate_and_mix_sfx(sfx_input)
                logger.info("pipeline_sfx_complete", input=str(sfx_input))
                _mark_stage("sfx", {"input": str(sfx_input)})

        # 4. Music (optional). If the caller supplied --music-prompt,
        # generate a track first and let MusicSkill pick it up via
        # its own auto-discovery (or via the explicit path).
        music_video: Path | None = None
        if self._should_run_music():
            with_music_path = self.ctx.work_dir / "music" / "with_music.mp4"
            if music_folded:
                # The music bed was folded into the composition render above.
                music_video = sfx_input
                _mark_stage("music", {"video": str(music_video), "folded": True})
                logger.info("pipeline_music_folded", video=str(music_video))
            elif manifest.is_stage_complete("music") and with_music_path.exists():
                music_video = with_music_path
                logger.info("pipeline_stage_resumed", stage="music", video=str(music_video))
            else:
                if self.music_prompt:
                    generated = await self._generate_music()
                    if generated is not None:
                        self.music_path = generated
                music = MusicSkill(
                    self.ctx,
                    music_path=self.music_path,
                    render_options=self.render_options,
                )
                music_video = music.run(input_video=sfx_input)
                logger.info("pipeline_music_complete", video=str(music_video))
                _mark_stage("music", {"video": str(music_video)})
        else:
            logger.info("pipeline_music_skipped", reason="no_music_file")

        # 5. Optional QA gate — runs between music and export so a
        # known-bad render never reaches the deliverable.
        if run_qa:
            # Local import keeps the qa skill out of the import graph
            # for callers that don't enable the gate.
            from qa.qa import QASkill

            # Bug #40: when music ran, the gate should target the
            # music-mixed video (the closest thing to the deliverable),
            # not the captions video. Falling back to the captions
            # video would miss loudness drift or black leaders that
            # the music skill's mix introduced.
            qa_target: Path | None = music_video if music_video is not None else captions_video
            if manifest.is_stage_complete("qa"):
                logger.info("pipeline_stage_resumed", stage="qa")
            else:
                qa_skill = QASkill(
                    self.ctx,
                    expected_aspect_ratio=expected_aspect_ratio,
                    loudness_target_lufs=loudness_target_lufs,
                )
                qa_report = qa_skill.run(qa_target)
                if not qa_report.passed:
                    failed = [c.name for c in qa_report.checks if not c.passed]
                    logger.error(
                        "pipeline_qa_gate_failed",
                        failed_checks=failed,
                        report_path=str(self.ctx.artifact("qa", "qa_report.json")),
                    )
                    raise RuntimeError(
                        f"QA gate failed; failing checks: {failed}. "
                        f"See report at: {self.ctx.artifact('qa', 'qa_report.json')}"
                    )
                logger.info(
                    "pipeline_qa_gate_passed",
                    checks=len(qa_report.checks),
                )
                _mark_stage("qa", {"passed": True})

        # 6. Export
        rendered_output_name = self.project_config.render.output_name_template.replace(
            "{stage}", "final"
        )
        export_style = ExportStyle(
            output_name=self._safe_output_name(rendered_output_name),
        )
        export = ExportSkill(self.ctx, style=export_style)
        final_name = output_name or export_style.output_name
        final_path = self.ctx.output_dir / final_name
        if manifest.is_stage_complete("export") and final_path.exists():
            final = final_path
            logger.info("pipeline_stage_resumed", stage="export", final=str(final))
        else:
            # Export the video this pipeline actually produced. Without an
            # explicit input, ExportSkill's fallback search finds the
            # pre-graphics rough cut and ships it as the deliverable.
            deliverable = music_video or captions_video
            final = export.run(
                output_name=output_name or final_name,
                input_video=deliverable,
            )
            logger.info("pipeline_export_complete", final=str(final))

        # 7. Requirements verification gate. Run when explicitly requested or
        # when requirements have already been captured for this job. A FAIL
        # verdict raises VerificationError so the job does not report ready.
        verification_dict: dict[str, Any] | None = None
        verify_skill = VerifySkill(self.ctx)
        if run_verify or verify_skill.load_requirements() is not None:
            # If the caller supplied requirements inline, prefer them over
            # the persisted spec.
            if requirements is not None:
                verify_skill.requirements = requirements
            try:
                report = await verify_skill.run()
                logger.info(
                    "pipeline_verify_complete",
                    overall=report.overall,
                    checks=len(report.checks),
                )
                verification_dict = report.model_dump(mode="json")
            except VerificationError:
                logger.error("pipeline_verify_failed", job_id=self.ctx.job_id)
                raise

        # 8. Post-render review gate (optional). Runs on the final export
        # and catches critical deliverability issues like missing audio,
        # silent output, or unexpected black frames. Quality issues are
        # recorded without blocking the build.
        review_dict: dict[str, Any] | None = None
        if run_post_render_review and not manifest.is_stage_complete("review"):
            review_skill = PostRenderReviewSkill(
                self.ctx,
                expected_aspect_ratio=expected_aspect_ratio,
                loudness_target_lufs=loudness_target_lufs,
            )
            try:
                review_report = review_skill.run(final)
                logger.info(
                    "pipeline_post_render_review_complete",
                    overall=review_report.overall,
                    checks=len(review_report.checks),
                )
                review_dict = review_report.model_dump(mode="json")
                export.append_post_render_review()
            except RuntimeError:
                logger.error("pipeline_post_render_review_failed", job_id=self.ctx.job_id)
                raise

        result: dict[str, Any] = {
            "locked": str(locked),
            "graphics": str(graphics_video),
            "captions": str(captions_video),
            "music": str(music_video) if music_video else None,
            "sfx_input": str(sfx_input),
            "final": str(final),
        }
        if self.run_audio_cleanup:
            result["audio_cleanup"] = str(self.ctx.work_dir / "audio_cleanup" / "cleaned.mp4")
        if self.run_transitions:
            result["transitions"] = str(self.ctx.work_dir / "transitions" / "with_transitions.mp4")
        if self.run_reframe:
            result["reframe"] = str(self.ctx.work_dir / "reframe" / "reframed.mp4")
        if self.run_broll:
            result["broll"] = str(self.ctx.work_dir / "broll" / "with_broll.mp4")
        if self.run_sfx_placement:
            result["sfx_placement"] = str(self.ctx.work_dir / "sfx_placement" / "with_sfx.mp4")
        if verification_dict is not None:
            result["verification"] = verification_dict
        if review_dict is not None:
            result["review"] = review_dict

        final_status = "complete"
        notes: str | None = None
        if review_dict is not None and review_dict.get("overall") != "PASS":
            final_status = "partial"
            notes = f"Post-render review: {review_dict.get('overall')}"
        if verification_dict is not None and verification_dict.get("overall") != "PASS":
            final_status = "partial"
            notes = notes or f"Verification: {verification_dict.get('overall')}"

        record_pipeline_complete(
            self.ctx,
            final_video_path=str(final),
            status=final_status,
            notes=notes,
        )
        return result

    def run(
        self,
        source_path: Path | str,
        transcript_path: Path | str | None = None,
        output_name: str | None = None,
        run_qa: bool = False,
        loudness_target_lufs: float | None = None,
        expected_aspect_ratio: str = "16:9",
        run_verify: bool = False,
        requirements: RequirementsSpec | None = None,
        run_post_render_review: bool = False,
    ) -> dict[str, Any]:
        """Synchronous entry point for the skills pipeline.

        Calls the async :meth:`run_async` implementation with
        ``asyncio.run``. Use :meth:`run_async` directly when calling from
        an async context (e.g. the ``ve worker`` dispatcher) to avoid
        nested event loops.
        """
        return asyncio.run(
            self.run_async(
                source_path,
                transcript_path=transcript_path,
                output_name=output_name,
                run_qa=run_qa,
                loudness_target_lufs=loudness_target_lufs,
                expected_aspect_ratio=expected_aspect_ratio,
                run_verify=run_verify,
                requirements=requirements,
                run_post_render_review=run_post_render_review,
            )
        )


class MusicVideoPipeline:
    """Orchestrator for the ``musical_video`` template.

    The music track is the spine. User media is cycled in directory order,
    one segment per ``segment_seconds``. The plan is rendered through the
    HyperFrames composition (which adds a global music audio track),
    then optional lyric captions are burned with FFmpeg drawtext.
    """

    def __init__(
        self,
        ctx: JobContext,
        music_path: Path | str,
        media_dir: Path | str,
        segment_seconds: float = 5.0,
        lyrics_path: Path | str | None = None,
        language: str = "en",
        preset: str = "long_form",
        presets_dir: Path | str | None = None,
        config_path: Path | str | None = None,
        local_media_provider: str = "local_media",
        alignment_provider: str = "",
        budget: BudgetGovernor | None = None,
        render_engine: str = "auto",
    ):
        self.ctx = ctx
        self.music_path = Path(music_path)
        self.media_dir = Path(media_dir)
        self.segment_seconds = segment_seconds
        self.lyrics_path = Path(lyrics_path) if lyrics_path else None
        # Bug #192: sanitize ``language`` before storing it; it is later
        # interpolated into ``transcript_{language}.json`` inside ``work_dir``.
        self.language = self._safe_language(language)
        self.preset = preset
        self.config_path = Path(config_path) if config_path else None
        self.local_media_provider = local_media_provider
        self.alignment_provider = alignment_provider
        self.budget = budget
        self.settings = Settings.load(self.config_path)
        self.project_config = self.settings.config
        self.render_engine = (
            render_engine
            if render_engine != "auto"
            else (getattr(self.project_config.render, "engine", "") or "auto")
        )
        self.presets_dir = self._resolve_presets_dir(presets_dir)
        self.cache_dir = self._resolve_cache_dir()
        self.render_options = RenderOptions.from_mapping(
            self.project_config.render.model_dump(
                include={
                    "video_codec",
                    "audio_codec",
                    "video_bitrate",
                    "audio_bitrate",
                    "fps",
                    "crf",
                    "width",
                    "height",
                    "preset",
                    "hwaccel",
                }
            )
        )

    @property
    def _repo_root(self) -> Path:
        if self.config_path is None:
            return REPO_ROOT
        return self.config_path.resolve().parent

    @staticmethod
    def _safe_output_name(name: str) -> str:
        """Reject path traversal in the rendered/final output filename.

        Bug #191: ``run_async`` joins ``output_name`` with ``ctx.output_dir``
        to build both the rendered and final paths. A value like
        ``../../evil.mp4`` would write outside ``output_dir``. Accept only a
        plain basename with no separators or parent-directory sequences,
        mirroring ``SkillsPipeline._safe_output_name`` (#167).
        """
        if not name or "/" in name or "\\" in name or ".." in name or name in (".", ".."):
            raise ValueError(f"output_name must be a plain filename; got {name!r}")
        return name

    @staticmethod
    def _safe_language(language: str) -> str:
        """Reject path traversal in the lyrics ``language`` code.

        Bug #192: ``__init__`` stores ``language`` and ``run_async`` later
        interpolates it into ``transcript_{language}.json`` inside
        ``ctx.work_dir / "transcript"``. A value like ``../../tmp/evil``
        resolves the transcript path outside ``work_dir``. Accept only a
        simple language code with no path separators or parent-directory
        sequences (so ``en``, ``es``, ``en-US`` still pass), mirroring the
        closed #170's class of fix on a different field.
        """
        if (
            not language
            or "/" in language
            or "\\" in language
            or ".." in language
            or language in (".", "..")
        ):
            raise ValueError(
                f"language must be a simple language code without path "
                f"separators or parent references; got {language!r}"
            )
        return language

    def _resolve_presets_dir(self, presets_dir: Path | str | None) -> Path | None:
        if presets_dir is not None:
            return Path(presets_dir)
        return self.project_config.presets_dir(repo_root=self._repo_root)

    def _resolve_cache_dir(self) -> Path:
        """Keep the asset cache inside the job work dir."""
        configured = self.project_config.render.cache_dir
        work_dir = self.ctx.work_dir.resolve()
        if not configured:
            return work_dir / ".cache"
        candidate = Path(configured)
        resolved = (
            candidate.resolve() if candidate.is_absolute() else (work_dir / candidate).resolve()
        )
        if resolved.is_relative_to(work_dir):
            return resolved
        logger.warning(
            "render.cache_dir escapes work_dir; falling back to work_dir/.cache",
            configured=configured,
            work_dir=str(work_dir),
        )
        return work_dir / ".cache"

    async def _resolve_local_media(self, plan: ScenePlan) -> ScenePlan:
        """Ingest every scene's local media file into the asset cache.

        Bug #197: a single corrupt media file used to fail the entire batch
        because ``manager.resolve`` is all-or-nothing. Use ``resolve_partial``
        and attach only the successes; if every request fails (or none were
        generated) raise, but a single broken file no longer kills the build.
        """
        config = self.settings.config.assets
        manager = AssetIngestionManager(
            self.ctx,
            config=config,
            cache_dir=self.cache_dir,
            budget=self.budget,
        )
        requests = [
            req
            for scene in plan.scenes
            for req in local_media_requests_for_scene(
                scene,
                self.ctx.job_id,
                provider=self.local_media_provider,
            )
        ]
        if not requests:
            raise RuntimeError("No local media requests generated from the scene plan")

        batch = await manager.resolve_partial(requests)
        if batch.failures:
            for asset_id, err in batch.failures:
                logger.warning(
                    "music_video_media_skipped",
                    job_id=self.ctx.job_id,
                    asset_id=asset_id,
                    error=err,
                )
        if not batch.results:
            raise RuntimeError(
                f"All {len(requests)} local media request(s) failed: "
                f"{batch.failures[0][1] if batch.failures else 'unknown error'}"
            )
        for scene in plan.scenes:
            attach_results_to_scene(scene, batch.results)
        return plan

    async def _align_lyrics(self) -> dict[str, Any] | None:
        """Run forced alignment on the lyrics and return a WhisperX-shaped transcript.

        Bug #196: forced alignment is best-effort. The class docstring says
        captions are optional, so a failure (missing key, 401, 400, network
        timeout) logs a warning and returns ``None`` instead of crashing the
        whole build after the plan, ingest, and render already succeeded.
        """
        if self.lyrics_path is None:
            return None
        if not self.alignment_provider:
            # ElevenLabs forced alignment is off (voice-over only) and no other aligner is wired.
            logger.info(
                "lyrics_alignment_skipped", job_id=self.ctx.job_id, reason="no alignment provider"
            )
            return None
        # Bug #217: a directory passes ``.exists()``; require a regular file
        # so the next ``read_text`` does not raise ``IsADirectoryError``.
        if not self.lyrics_path.is_file():
            # Bug #202: a user-supplied but missing/invalid lyrics path used
            # to be silently skipped with no captions and no explanation.
            logger.warning(
                "lyrics_path_not_found",
                job_id=self.ctx.job_id,
                lyrics_path=str(self.lyrics_path),
            )
            return None
        lyrics_text = self.lyrics_path.read_text(encoding="utf-8").strip()
        if not lyrics_text:
            logger.info(
                "lyrics_empty",
                job_id=self.ctx.job_id,
                lyrics_path=str(self.lyrics_path),
            )
            return None

        config = self.settings.config.assets
        manager = AssetIngestionManager(
            self.ctx,
            config=config,
            cache_dir=self.cache_dir,
            budget=self.budget,
        )
        request = alignment_request(
            job_id=self.ctx.job_id,
            audio_path=str(self.music_path),
            text=lyrics_text,
            provider=self.alignment_provider,
        )
        try:
            batch = await manager.resolve_partial([request])
        except Exception as exc:  # noqa: BLE001 — degrade gracefully on any failure
            logger.warning(
                "lyrics_alignment_failed",
                job_id=self.ctx.job_id,
                error=str(exc),
            )
            return None
        if batch.failures or not batch.results:
            logger.warning(
                "lyrics_alignment_failed",
                job_id=self.ctx.job_id,
                error=batch.failures[0][1] if batch.failures else "no result",
            )
            return None
        alignment_result = batch.results[0]
        alignment_data = json.loads(Path(alignment_result.local_path).read_text(encoding="utf-8"))
        transcript = forced_alignment_response_to_transcript(
            alignment_data,
            source_audio=str(self.music_path),
            language=self.language,
        )

        transcript_dir = self.ctx.work_dir / "transcript"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcript_dir / f"transcript_{self.language}.json"
        transcript_path.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
        # Bug #216: writing the canonical ``transcript.json`` from every run
        # silently swapped the language a downstream consumer saw when the
        # same job was re-run with a different language. First-write-wins:
        # only seed the canonical name if no transcript is there yet, so the
        # first language's canonical is stable across re-runs.
        canonical_path = transcript_dir / "transcript.json"
        if not canonical_path.exists():
            canonical_path.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
        else:
            logger.info(
                "transcript_canonical_present",
                job_id=self.ctx.job_id,
                canonical=str(canonical_path),
                language=self.language,
            )
        logger.info(
            "lyrics_alignment_complete",
            job_id=self.ctx.job_id,
            language=self.language,
            words=len(transcript.get("words", [])),
            path=str(transcript_path),
        )
        return transcript

    def _burn_captions(
        self,
        video_path: Path,
        transcript: dict[str, Any] | None,
        output_path: Path,
    ) -> Path:
        """Burn lyric captions over the rendered video."""
        if not transcript:
            logger.info("no_lyrics_to_burn", output=str(output_path))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(video_path, output_path)
            return output_path

        segments = generate_caption_segments(transcript)
        if not segments:
            logger.warning("empty_caption_segments", output=str(output_path))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(video_path, output_path)
            return output_path

        return drawtext_captions(
            video_path=video_path,
            output_path=output_path,
            text_segments=segments,
            style={
                "fontfile": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "fontsize": 54,
                "fontcolor": "white",
                "borderw": 5,
                "bordercolor": "black",
                "x": "(w-text_w)/2",
                "y": "h*0.85",
            },
        )

    async def run_async(
        self,
        output_name: str | None = None,
        expected_aspect_ratio: str = "16:9",
        run_post_render_review: bool = False,
    ) -> dict[str, Any]:
        """Async implementation of the music-video pipeline."""
        # Bug #191: reject a traversal ``output_name`` before any work so
        # the CLI path (and any direct caller) cannot write the rendered or
        # final video outside ``ctx.output_dir``.
        if output_name is not None:
            output_name = self._safe_output_name(output_name)

        self.ctx.ensure_dirs()
        record_pipeline_start(
            self.ctx,
            "music_video",
            template="musical_video",
            source_path=str(self.music_path),
            budget_limit=self.budget.limit if self.budget else None,
        )

        manifest = self.ctx.manifest()

        def _maybe_review(final: Path) -> dict[str, Any] | None:
            if not run_post_render_review or manifest.is_stage_complete("review"):
                return None
            music_info = probe_media(self.music_path)
            review_skill = PostRenderReviewSkill(
                self.ctx,
                expected_aspect_ratio=expected_aspect_ratio,
                expected_duration_seconds=music_info.duration,
            )
            report = review_skill.run(final)
            logger.info(
                "pipeline_post_render_review_complete",
                overall=report.overall,
                checks=len(report.checks),
            )
            return report.model_dump(mode="json")

        # Bug #195: render to a distinct intermediate filename so burning
        # captions never overwrites the uncaptioned render. The final
        # deliverable is ``output_name`` (or ``final.mp4``); the render is
        # always a separate ``rendered.mp4`` (or ``rendered_uncaptioned.mp4``
        # if the user asked for ``rendered.mp4`` as the final name).
        final_name = output_name or "final.mp4"
        final_path = self.ctx.output_dir / final_name
        rendered_name = (
            "rendered.mp4" if final_name != "rendered.mp4" else "rendered_uncaptioned.mp4"
        )
        rendered_output = self.ctx.output_dir / rendered_name

        # Bug #211: resume path. If the export already completed and the final
        # deliverable exists, return it instead of re-rendering. A crash after
        # render used to waste the re-render on retry.
        if manifest.is_stage_complete("export") and final_path.exists():
            logger.info(
                "pipeline_stage_resumed",
                stage="export",
                final=str(final_path),
                job_id=self.ctx.job_id,
            )
            review_dict = _maybe_review(final_path)
            result: dict[str, Any] = {
                "plan": str(self.ctx.work_dir / "plan" / "scene_plan.json"),
                "rendered": str(rendered_output),
                "captions": str(self.ctx.work_dir / "captions" / "with_captions.mp4"),
                "final": str(final_path),
                "transcript": str(
                    self.ctx.work_dir / "transcript" / f"transcript_{self.language}.json"
                )
                if (self.ctx.work_dir / "transcript" / f"transcript_{self.language}.json").exists()
                else None,
            }
            if review_dict is not None:
                result["review"] = review_dict
            return result

        # 1. Plan
        plan_path = self.ctx.work_dir / "plan" / "scene_plan.json"
        if manifest.is_stage_complete("music_video_plan") and plan_path.exists():
            logger.info(
                "pipeline_stage_resumed",
                stage="music_video_plan",
                plan=str(plan_path),
                job_id=self.ctx.job_id,
            )
            plan = build_music_video_plan(
                job_id=self.ctx.job_id,
                music_path=self.music_path,
                media_dir=self.media_dir,
                segment_seconds=self.segment_seconds,
                aspect_ratio=expected_aspect_ratio,
            )
        else:
            plan = build_music_video_plan(
                job_id=self.ctx.job_id,
                music_path=self.music_path,
                media_dir=self.media_dir,
                segment_seconds=self.segment_seconds,
                aspect_ratio=expected_aspect_ratio,
            )
            write_music_video_plan(plan, plan_path)
            manifest.stage_done("music_video_plan", {"plan": str(plan_path)})
            self.ctx.store.save()

        # 2. Ingest local media
        enriched_plan = await self._resolve_local_media(plan)
        enriched_plan_path = self.ctx.work_dir / "plan" / "scene_plan_with_assets.json"
        enriched_plan_path.write_text(enriched_plan.model_dump_json(indent=2), encoding="utf-8")

        # 3. Stage every visual asset into the asset dir so
        # ``staticFile(basename(local_path))`` resolves during render.
        public_dir = self.ctx.work_dir / ".cache" / "assets"
        public_dir.mkdir(parents=True, exist_ok=True)
        visual_types = {"image", "video", "data_viz"}
        for scene in enriched_plan.scenes:
            for asset in scene.assets or []:
                if asset.asset_type.value not in visual_types:
                    continue
                src = asset.resolved_path()
                dst = public_dir / src.name
                if src.resolve() != dst.resolve():
                    shutil.copy2(src, dst)
                    logger.info(
                        "visual_copied_to_public_dir",
                        job_id=self.ctx.job_id,
                        scene_id=scene.id,
                        asset_id=asset.asset_id,
                        source=str(src),
                        dest=str(dst),
                    )

        # 4. Optional lyric alignment
        transcript = await self._align_lyrics()

        # 5. Copy the music bed alongside the visuals.
        music_public = public_dir / self.music_path.name
        if music_public.resolve() != self.music_path.resolve():
            shutil.copy2(self.music_path, music_public)
            logger.info(
                "music_copied_to_public_dir",
                job_id=self.ctx.job_id,
                source=str(self.music_path),
                dest=str(music_public),
            )

        # 6. Render via HyperFrames HTML composition (Remotion retired).
        if self.render_engine not in ("auto", "hyperframes"):
            logger.warning(
                "render_engine_ignored",
                requested=self.render_engine,
                using="hyperframes",
            )
        render_scene_plan(self.ctx, enriched_plan_path, rendered_output)

        # 7. Burn captions onto a separate file, then place the final
        # deliverable at ``final_path``. Because ``rendered_output`` != ``final_path``
        # (#195), the uncaptioned render is preserved at ``rendered_output``.
        captioned_output = self.ctx.work_dir / "captions" / "with_captions.mp4"
        final_video = self._burn_captions(rendered_output, transcript, captioned_output)
        if final_video != final_path:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final_video, final_path)

        manifest.stage_done("export", {"final": str(final_path)})
        self.ctx.store.save()
        logger.info("music_video_complete", job_id=self.ctx.job_id, final=str(final_path))

        review_dict = _maybe_review(final_path)
        result = {
            "plan": str(plan_path),
            "rendered": str(rendered_output),
            "captions": str(captioned_output),
            "final": str(final_path),
            "transcript": str(self.ctx.work_dir / "transcript" / f"transcript_{self.language}.json")
            if transcript
            else None,
        }
        if review_dict is not None:
            result["review"] = review_dict

        final_status = "complete"
        notes: str | None = None
        if review_dict is not None and review_dict.get("overall") != "PASS":
            final_status = "partial"
            notes = f"Post-render review: {review_dict.get('overall')}"

        record_pipeline_complete(
            self.ctx,
            final_video_path=str(final_path),
            status=final_status,
            notes=notes,
        )
        return result

    def run(
        self,
        output_name: str | None = None,
        expected_aspect_ratio: str = "16:9",
        run_post_render_review: bool = False,
    ) -> dict[str, Any]:
        """Synchronous entry point for the music-video pipeline."""
        return asyncio.run(
            self.run_async(
                output_name=output_name,
                expected_aspect_ratio=expected_aspect_ratio,
                run_post_render_review=run_post_render_review,
            )
        )
