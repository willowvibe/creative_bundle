"""CLI command group."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal, cast

import structlog
import typer
from chapters.chapters import ChapterSkill
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.scene_assets import (
    attach_results_to_plan,
    requests_from_plan,
)
from video_editor_config import Settings
from video_editor_core.models.scene_plan import ScenePlan
from video_editor_engine.planner.storyteller_planner import (
    parse_storyteller_script,
)
from video_editor_pipelines.scene_planner import build_scene_plan
from video_editor_state.ledger_hooks import record_pipeline_failed

from video_editor_cli import main as cli_main

logger = structlog.get_logger(__name__)


def register_run(app: typer.Typer) -> None:
    """Register the run commands."""

    @app.command()
    def run(
        source: Path = cli_main._RUN_SOURCE_ARG,
        job_id: str = cli_main._JOB_ID_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        force: bool = cli_main._FORCE_RUN_OPT,
        transcript_path: Path | None = cli_main._TRANSCRIPT_RUN_OPT,
        config_path: Path | None = cli_main._CONFIG_RUN_OPT,
        preset: str = cli_main._PRESET_OPT,
        presets_dir: Path | None = cli_main._PRESETS_DIR_OPT,
        music_path: Path | None = cli_main._MUSIC_PATH_OPT,
        output_name: str = cli_main._OUTPUT_NAME_OPT,
        skip_music: bool = cli_main._SKIP_MUSIC_OPT,
        broll_provider: str = cli_main._BROLL_PROVIDER_OPT,
        qa: bool = cli_main._QA_OPT,
        loudness_target_lufs: float | None = cli_main._LOUDNESS_TARGET_OPT,
        expected_aspect_ratio: str | None = cli_main._RUN_EXPECTED_ASPECT_OPT,
        music_prompt: str = cli_main._MUSIC_PROMPT_OPT,
        music_provider: str = cli_main._MUSIC_PROVIDER_OPT,
        music_duration_seconds: int = cli_main._MUSIC_DURATION_OPT,
        sfx_provider: str = cli_main._SFX_PROVIDER_OPT,
        sfx_duration_seconds: float = cli_main._SFX_DURATION_OPT,
        sfx_gain_db: float = cli_main._SFX_GAIN_DB_OPT,
        media_dir: Path | None = cli_main._MEDIA_DIR_OPT,
        segment_seconds: float = cli_main._SEGMENT_SECONDS_OPT,
        lyrics: Path | None = cli_main._LYRICS_OPT,
        language: str = cli_main._LANGUAGE_OPT,
        template: str | None = cli_main._TEMPLATE_OPT,
        image_provider: str = cli_main._RUN_IMAGE_PROVIDER_OPT,
        verify: bool = cli_main._VERIFY_OPT,
        review: bool = cli_main._REVIEW_OPT,
        image_tier: cli_main._StorytellerTier | None = cli_main._IMAGE_TIER_OPT,
        video_tier: cli_main._StorytellerTier | None = cli_main._VIDEO_TIER_OPT,
        skip_hyperframes_lint: bool = cli_main._SKIP_HYPERFRAMES_LINT_OPT,
        transition: str = cli_main._TRANSITION_OPT,
        transition_duration: float = cli_main._TRANSITION_DURATION_OPT,
        reframe: bool = cli_main._REFRAME_OPT,
        reframe_aspect: str = cli_main._REFRAME_ASPECT_OPT,
        broll: bool = cli_main._BROLL_OPT,
        sfx: bool = cli_main._SFX_PLACEMENT_OPT,
        transitions: bool = cli_main._TRANSITIONS_OPT,
        audio_cleanup: bool = cli_main._AUDIO_CLEANUP_OPT,
        render_engine: str = cli_main._RENDER_ENGINE_OPT,
        budget: float | None = cli_main._BUDGET_OPT,
        auto_approve: bool = cli_main._AUTO_APPROVE_OPT,
        skip_preview: bool = cli_main._SKIP_PREVIEW_OPT,
        final: bool = typer.Option(
            False, "--final", help="Use the final image chain (direct image APIs, no agy)"
        ),
    ) -> None:
        """Run the full pipeline."""

        from video_editor_asset_ingestion.budget import BudgetGovernor

        ctx = cli_main._context(job_id, work_root, output_root, config_path=config_path)
        budget_governor = BudgetGovernor(limit=budget) if budget is not None else None

        if not source.exists():
            typer.echo(f"Source path does not exist: {source}", err=True)
            raise typer.Exit(1)

        router = cli_main.TemplateRouter()
        overrides: dict[str, Any] = {}
        if media_dir is not None:
            overrides["media_dir"] = str(media_dir)
        selected = router.select(source=source, template_name=template, overrides=overrides)
        # A raw_to_video / screen_to_video template's own preset wins unless the
        # user overrode --preset; ``short_explainer`` is the CLI default, so it
        # acts as an "unset" sentinel on those paths.
        effective_preset = (
            selected.preset
            if selected.pipeline_kind in ("raw_to_video", "screen_to_video")
            and preset == "short_explainer"
            else preset
        )
        if expected_aspect_ratio is None:
            expected_aspect_ratio = (
                cli_main._preset_aspect_ratio(
                    cli_main._resolve_presets_dir(presets_dir, config_path),
                    effective_preset,
                )
                or selected.aspect_ratio
                or "16:9"
            )
        typer.echo(
            f"Template: {selected.template_name} ({selected.pipeline_kind}, "
            f"preset={effective_preset}, aspect={selected.aspect_ratio})"
        )

        # Only the storyteller planner builds image requests. Accepting the flag
        # on the other pipelines would silently ignore an explicit provider — and
        # a user who pinned one is usually paying for it — so fail instead. The
        # "auto" default is a no-op and stays accepted everywhere. The
        # explainer_stills branch below raises its own (more useful) error.
        if image_provider != "auto" and selected.pipeline_kind not in (
            "storyteller",
            "explainer_stills",
        ):
            raise typer.BadParameter(
                f"--image-provider is only used by storyteller templates; the "
                f"{selected.pipeline_kind} pipeline builds no image assets. Omit "
                f"it (the default is 'auto') or select a storyteller_* template."
            )

        # --- Cost estimation gate ---
        if selected.pipeline_kind in ("script_to_video", "storyteller", "music_video"):
            from video_editor_cli.commands.estimate import estimate_command

            plan_path = ctx.work_dir / "plan" / "scene_plan.json"
            if plan_path.exists() and not ctx.manifest().is_stage_complete("estimate"):
                typer.echo("")
                estimate_command(
                    job_id=job_id,
                    work_root=work_root,
                    output_root=output_root,
                    config_path=config_path,
                    auto_approve=auto_approve,
                    image_provider=image_provider,
                    music_provider=music_provider,
                    final=final,
                )

        if selected.pipeline_kind == "storyteller":
            # Storyteller path: parse the markdown script, build providers, and
            # delegate to ``build_storyteller_scene_plan`` which generates assets
            # and returns a :class:`ScenePlan` written to ``plan/scene_plan.json``.
            # A ``.md`` source is a file and would otherwise wrongly fall into the
            # SkillsPipeline branch below, so handle storyteller first.
            from video_editor_asset_ingestion.providers.audio.freesound_sfx import (
                FreesoundSFXProvider,
            )
            from video_editor_asset_ingestion.providers.audio.poyo_music import (
                PoyoMusicProvider,
            )
            from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

            script = parse_storyteller_script(source)
            config = Settings.load(config_path).config.assets
            asset_manager = AssetIngestionManager(
                ctx, config=config, budget=budget_governor, final=final
            )
            # NOTE: local names ``music_prov``/``sfx_prov``/``tts_prov`` avoid
            # shadowing the ``music_provider``/``sfx_provider`` CLI string params.
            music_prov = PoyoMusicProvider()
            # ElevenLabs is voice-over only; scene SFX come from Freesound.
            sfx_prov = FreesoundSFXProvider(config.providers.get("freesound_sfx"))
            tts_prov = TTSRouter()
            # NOTE: local names ``music_prov``/``sfx_prov``/``tts_prov`` avoid
            # shadowing the ``music_provider``/``sfx_provider`` CLI string params.
            # Storyteller planner declares ``llm_router: ModelRouter`` (non-Optional)
            # and dereferences it on both budget and hd/premium happy paths. When
            # the caller omits ``--config`` we fall back to a CWD ``config.yaml``
            # before constructing the router; if neither is available we exit
            # cleanly with a message instead of letting a ``None`` propagate into
            # the planner and crash on ``await llm_router.generate(...)``.
            effective_config = config_path or Path("config.yaml")
            if not effective_config.exists():
                typer.echo(
                    "--config is required for storyteller templates "
                    f"(no config.yaml found at {effective_config})",
                    err=True,
                )
                raise typer.Exit(1)
            llm_router = cli_main._llm_router(effective_config, ctx)
            assert llm_router is not None  # effective_config.exists() guard above
            # Ripple 2: ``selected.template_name`` is ``str``; the planner requires
            # a ``Literal["storyteller_budget","storyteller_hd","storyteller_premium"]``.
            # Narrow then cast.
            if selected.template_name not in {
                "storyteller_budget",
                "storyteller_hd",
                "storyteller_premium",
            }:
                typer.echo(
                    f"storyteller pipeline requires a storyteller_* template, "
                    f"got: {selected.template_name!r}",
                    err=True,
                )
                raise typer.Exit(1)
            template_name = cast(
                Literal["storyteller_budget", "storyteller_hd", "storyteller_premium"],
                selected.template_name,
            )
            plan = asyncio.run(
                cli_main.build_storyteller_scene_plan(
                    script,
                    llm_router,
                    template_name=template_name,
                    asset_manager=asset_manager,
                    music_provider=music_prov,
                    sfx_provider=sfx_prov,
                    tts_provider=tts_prov,
                    project_dir=ctx.work_dir,
                    image_tier=image_tier.value if image_tier else None,
                    video_tier=video_tier.value if video_tier else None,
                    image_provider=image_provider,
                )
            )
            plan_path = ctx.work_dir / "plan" / "scene_plan.json"
            cli_main.write_scene_plan(plan, plan_path)
            typer.echo(f"Storyteller plan complete. Scene plan: {plan_path}")
            if not skip_preview:
                typer.echo(
                    "\nRun `ve preview` to edit the timeline in FableCut, then `ve finalize` to export."
                )
            return

        if selected.pipeline_kind == "music_video":
            if media_dir is None or not media_dir.is_dir():
                typer.echo(
                    f"musical_video requires an existing --media-dir. Got: {media_dir}",
                    err=True,
                )
                raise typer.Exit(1)
            # Bug #209: ``source`` is the music track. A directory passes the
            # earlier ``source.exists()`` check but is not a playable audio
            # file; reject it here with a clear message instead of letting
            # ``MusicVideoPipeline`` raise a confusing ``FileNotFoundError`` for
            # a path that does exist.
            if not source.is_file():
                typer.echo(
                    f"musical_video requires --source to be a music/audio file, "
                    f"not a directory: {source}",
                    err=True,
                )
                raise typer.Exit(1)
            pipeline = cli_main.MusicVideoPipeline(
                ctx,
                music_path=source,
                media_dir=media_dir,
                segment_seconds=segment_seconds,
                lyrics_path=lyrics,
                language=language,
                preset=preset,
                presets_dir=presets_dir,
                config_path=config_path,
                budget=budget_governor,
                render_engine=render_engine,
            )
            try:
                result = pipeline.run(
                    output_name=output_name,
                    expected_aspect_ratio=expected_aspect_ratio,
                    run_post_render_review=review,
                )
            except Exception as exc:  # noqa: BLE001
                record_pipeline_failed(ctx, repr(exc), status="failed")
                raise
            typer.echo(f"Pipeline complete. Final output: {result['final']}")
            if not skip_preview:
                typer.echo(
                    "\nRun `ve preview` to edit the timeline in FableCut, then `ve finalize` to export."
                )
            return

        if selected.pipeline_kind == "podcast_to_video":
            # podcast_video template: long-form two-host podcast with chapters.
            # Routes through the existing SkillsPipeline + a post-run chapter
            # generation stage (Task 13) that emits chapters.json + chapters.txt.
            if not source.is_file():
                typer.echo(
                    f"podcast_video requires --source to be the host camera/mic track file: {source}",
                    err=True,
                )
                raise typer.Exit(1)
            # podcast_video carries a default preset (podcast_studio) that should
            # win unless the user explicitly overrode --preset. The CLI default
            # is "short_explainer", so treat that sentinel as "not set" for this
            # pipeline kind.
            effective_preset = (
                selected.preset
                if selected.pipeline_kind == "podcast_to_video" and preset == "short_explainer"
                else preset
            )
            effective_music_path = None if skip_music else music_path
            podcast_pipeline = cli_main.SkillsPipeline(
                ctx,
                preset=effective_preset,
                presets_dir=presets_dir,
                music_path=effective_music_path,
                music_prompt=music_prompt,
                music_provider=music_provider,
                music_duration_seconds=music_duration_seconds,
                sfx_provider=sfx_provider,
                sfx_duration_seconds=sfx_duration_seconds,
                sfx_gain_db=sfx_gain_db,
                config_path=config_path,
                skip_hyperframes_lint=skip_hyperframes_lint,
                budget=budget_governor,
                run_audio_cleanup=audio_cleanup,
                run_transitions=False,
                run_reframe=False,
                run_broll=broll,
                run_sfx_placement=sfx,
                reframe_aspect=reframe_aspect,
                render_engine=render_engine,
                final=final,
                skip_music=skip_music,
            )
            try:
                skills_result = podcast_pipeline.run(
                    source,
                    transcript_path=transcript_path,
                    output_name=output_name,
                    run_qa=qa,
                    loudness_target_lufs=loudness_target_lufs,
                    expected_aspect_ratio=expected_aspect_ratio,
                    run_verify=verify,
                    run_post_render_review=review,
                )
            except Exception as exc:  # noqa: BLE001
                record_pipeline_failed(ctx, repr(exc), status="failed")
                raise

            # Optional: generate YouTube chapter markers from the transcript.
            # The chapters skill (Task 13) writes chapters.json + chapters.txt
            # to work_dir/chapters/. A missing transcript is reported but not
            # fatal — many podcast renders still ship without chapters.
            if transcript_path is not None and transcript_path.exists():
                try:
                    import json as _json

                    transcript_payload = _json.loads(transcript_path.read_text(encoding="utf-8"))
                    chapter_skill = ChapterSkill(ctx)
                    chapter_result = chapter_skill.run(transcript_payload)
                    skills_result["chapters"] = {
                        "json": str(chapter_result["chapters_json"]),
                        "txt": str(chapter_result["chapters_txt"]),
                        "count": len(chapter_result["chapters"]),
                    }
                except Exception as exc:  # noqa: BLE001
                    typer.echo(
                        f"Warning: chapter generation failed: {exc!r}",
                        err=True,
                    )
            else:
                typer.echo(
                    "Skipping chapter generation: no --transcript provided. "
                    "Pass --transcript transcript.json to enable YouTube chapters.",
                    err=True,
                )

            typer.echo(f"Pipeline complete. Final output: {skills_result['final']}")
            if not skip_preview:
                typer.echo(
                    "\nRun `ve preview` to edit the timeline in FableCut, then `ve finalize` to export."
                )
            return

        if selected.pipeline_kind == "explainer_stills":
            # ``explainer_stills`` is a registered template (the router selects it
            # and ``ve list-templates`` advertises it), but its entry point is
            # ``ve explain``: it needs a script.md and prices the build with its
            # own gate. ``ve run`` takes a media path, so a .md source would fall
            # through to SkillsPipeline and be treated as footage.
            raise typer.BadParameter(
                "the explainer_stills template is built with `ve explain "
                "<script.md>`, not `ve run`: `ve run` takes a media file or folder, "
                "while explainer_stills needs a script.md (YAML front matter plus "
                "one '## ' scene per beat). Run: "
                f"ve explain {source} --job-id {job_id}"
            )

        if source.is_dir():
            # Directory input (was the v1 raw-footage pipeline): run the skills
            # pipeline on the first supported clip.
            _videos = sorted(
                p
                for ext in (".mp4", ".mov", ".mkv", ".avi", ".webm")
                for p in source.glob(f"*{ext}")
            )
            if not _videos:
                typer.echo(f"No supported media found in {source}", err=True)
                raise typer.Exit(1)
            if len(_videos) > 1:
                typer.echo(
                    f"Directory input: processing the first of {len(_videos)} clips "
                    f"({_videos[0].name})."
                )
            source = _videos[0]

        if source.is_file():
            effective_music_path = None if skip_music else music_path
            # ``effective_preset`` was resolved from the template selection above.
            skills_pipeline = cli_main.SkillsPipeline(
                ctx,
                preset=effective_preset,
                presets_dir=presets_dir,
                music_path=effective_music_path,
                music_prompt=music_prompt,
                music_provider=music_provider,
                music_duration_seconds=music_duration_seconds,
                sfx_provider=sfx_provider,
                sfx_duration_seconds=sfx_duration_seconds,
                sfx_gain_db=sfx_gain_db,
                config_path=config_path,
                skip_hyperframes_lint=skip_hyperframes_lint,
                budget=budget_governor,
                run_audio_cleanup=audio_cleanup,
                run_transitions=transitions,
                run_reframe=reframe,
                run_broll=broll,
                run_sfx_placement=sfx,
                reframe_aspect=reframe_aspect,
                render_engine=render_engine,
                final=final,
                skip_music=skip_music,
            )
            skills_pipeline.render_options.transition = transition
            skills_pipeline.render_options.transition_duration = transition_duration
            # Bug #25: --broll-provider is the v1 raw-footage flag and is not
            # supported on the v2 skills pipeline (single-file path); the v2
            # equivalent is the new --broll flag. Warn only when the user
            # *explicitly* passed --broll-provider (not the default
            # "placeholder" value).
            if broll_provider and broll_provider != "placeholder":
                typer.echo(
                    f"Warning: --broll-provider={broll_provider!r} is not yet "
                    "supported on the v2 skills pipeline (single-file path); "
                    "ignored. Use --broll for transcript-driven B-roll on this "
                    "path, or --broll-provider on a directory source for the "
                    "v1 raw-footage pipeline.",
                    err=True,
                )
            # Bug #115 regression: --force on the v2 skills path is currently a
            # v1 raw-footage flag; warn instead of silently ignoring it.
            if force:
                typer.echo(
                    "Warning: --force is not yet supported on the v2 skills "
                    "pipeline (single-file path); ignored. Use --force on a "
                    "directory source for the v1 raw-footage pipeline, or omit "
                    "the flag.",
                    err=True,
                )
            try:
                skills_result = skills_pipeline.run(
                    source,
                    transcript_path=transcript_path,
                    output_name=output_name,
                    run_qa=qa,
                    loudness_target_lufs=loudness_target_lufs,
                    expected_aspect_ratio=expected_aspect_ratio,
                    run_verify=verify,
                    run_post_render_review=review,
                )
            except Exception as exc:  # noqa: BLE001
                record_pipeline_failed(ctx, repr(exc), status="failed")
                raise
            typer.echo(f"Pipeline complete. Final output: {skills_result['final']}")
            if not skip_preview:
                typer.echo(
                    "\nRun `ve preview` to edit the timeline in FableCut, then `ve finalize` to export."
                )
            return

        typer.echo(f"Source path does not exist: {source}", err=True)
        raise typer.Exit(1)

    @app.command()
    def render(
        job_id: str = cli_main._JOB_ID_ARG,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        plan_out_name: str = cli_main._ASSETS_PLAN_OUT_NAME_OPT,
        output_name: str = cli_main._OUTPUT_NAME_OPT,
    ) -> None:
        """Render a resolved scene plan to an MP4 via HyperFrames."""
        ctx = cli_main._context(job_id, work_root, output_root)
        plan_path = ctx.work_dir / "plan" / plan_out_name
        if not plan_path.exists():
            typer.echo(f"Scene plan not found: {plan_path}", err=True)
            raise typer.Exit(1)

        output_path = ctx.output_dir / output_name
        from video_editor_core.scene_plan_composition import (
            render_scene_plan as render_scene_plan_hf,
        )

        render_scene_plan_hf(plan_path, output_path, ctx.work_dir / "hyperframes")
        typer.echo(f"Rendered {output_path}")

    @app.command()
    def assets(
        job_id: str = cli_main._JOB_ID_ARG,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        plan_name: str = cli_main._ASSETS_PLAN_NAME_OPT,
        plan_out_name: str = cli_main._ASSETS_PLAN_OUT_NAME_OPT,
        tts_provider: str = cli_main._ASSETS_TTS_PROVIDER_OPT,
        image_provider: str = cli_main._ASSETS_IMAGE_PROVIDER_OPT,
        data_viz_provider: str = cli_main._ASSETS_DATA_VIZ_PROVIDER_OPT,
        video_provider: str = cli_main._ASSETS_VIDEO_PROVIDER_OPT,
        config_path: Path | None = cli_main._CONFIG_OPT,
        budget: float | None = cli_main._BUDGET_OPT,
        final: bool = typer.Option(
            False, "--final", help="Use the final image chain (direct image APIs, no agy)"
        ),
    ) -> None:
        """Resolve generative assets for a scene plan and write the enriched plan.

        When ``--video-provider`` is non-empty, scenes with a non-empty
        ``visual_description`` get an extra VIDEO asset request in addition
        to the IMAGE one. The VIDEO is appended before IMAGE so the
        Renderer picks it.
        """

        from video_editor_asset_ingestion.budget import BudgetGovernor

        ctx = cli_main._context(job_id, work_root, output_root, config_path=config_path)
        config = Settings.load(config_path).config.assets
        budget_governor = BudgetGovernor(limit=budget) if budget is not None else None
        manager = AssetIngestionManager(ctx, config=config, budget=budget_governor, final=final)

        # Ensure the project ledger has a row for this job so asset cost lines
        # are grouped under a real project instead of being silently dropped.
        ctx.ledger.upsert_project(job_id, status="planned")

        plan_path = ctx.work_dir / "plan" / plan_name
        plan_out_path = ctx.work_dir / "plan" / plan_out_name
        if not plan_path.exists():
            typer.echo(f"Scene plan not found: {plan_path}", err=True)
            raise typer.Exit(1)

        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        requests = requests_from_plan(
            plan,
            job_id,
            tts_provider=tts_provider,
            image_provider=image_provider,
            data_viz_provider=data_viz_provider,
            video_provider=video_provider,
        )

        results = asyncio.run(manager.resolve(requests))
        attach_results_to_plan(plan, results)

        plan_out_path.parent.mkdir(parents=True, exist_ok=True)
        plan_out_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"Resolved {len(results)} asset(s) -> {plan_out_path}")

    @app.command()
    def plan_script(
        script_path: Path = cli_main._SCRIPT_PATH_ARG,
        job_id: str = cli_main._JOB_ID_ARG,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        aspect_ratio: str = cli_main._ASPECT_RATIO_OPT,
        music_mood: str = cli_main._MUSIC_MOOD_OPT,
        plan_out: str = cli_main._PLAN_OUT_OPT,
        config_path: Path | None = cli_main._CONFIG_OPT,
    ) -> None:
        """Decompose a markdown script into scene_plan.json via LLM."""

        config_path = cli_main._discover_config_path(config_path)
        ctx = cli_main._context(job_id, work_root, output_root, config_path=config_path)
        router = cli_main._llm_router(config_path, ctx)
        if router is None:
            typer.echo(
                "No config.yaml found. Pass --config PATH, set VE_CONFIG, or "
                "create config.yaml in the current directory or the repo root.",
                err=True,
            )
            raise typer.Exit(1)

        plan = asyncio.run(
            build_scene_plan(
                job_id,
                script_path,
                router,
                aspect_ratio=aspect_ratio,
                music_mood=music_mood,
            )
        )
        output_path = ctx.work_dir / "plan" / plan_out
        cli_main.write_scene_plan(plan, output_path)
        typer.echo(f"Scene plan written to {output_path}")
