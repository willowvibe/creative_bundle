"""CLI command group."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import structlog
import typer
from captions.captions import CaptionsSkill
from graphics.graphics import GraphicsSkill
from music.music import MusicSkill
from rough_cut.rough_cut import RoughCutSkill
from second_pass.models import SegmentEdit
from second_pass.second_pass import SecondPassSkill
from verify.models import ContentConstraints, DurationRange, RequirementsSpec
from verify.verify import VerifySkill
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.render_options import RenderOptions

from video_editor_cli import main as cli_main

logger = structlog.get_logger(__name__)


def register_misc(app: typer.Typer) -> None:
    """Register the misc commands."""

    @app.command()
    def export(
        job_id: str = cli_main._JOB_ID_ARG,
        output_name: str = cli_main._OUTPUT_NAME_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        force: bool = cli_main._FORCE_OPT,
        review: bool = cli_main._REVIEW_OPT,
    ) -> None:
        """Export the final MP4, report, and decision log."""
        ctx = cli_main._context(job_id, work_root, output_root)
        skill = cli_main.ExportSkill(ctx)
        final = skill.run(output_name=output_name)
        typer.echo(f"Final export: {final}")

        if review:
            review_skill = cli_main.PostRenderReviewSkill(ctx)
            review_report = review_skill.run(final)
            typer.echo(
                f"Post-render review: {review_report.overall} ({len(review_report.checks)} checks)"
            )
            skill.append_post_render_review()

    @app.command()
    def rough_cut(
        source_path: Path = cli_main._SOURCE_PATH_ARG,
        job_id: str = cli_main._JOB_ID_ARG,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        transcript_path: Path | None = cli_main._TRANSCRIPT_ROUGH_CUT_OPT,
    ) -> None:
        """Run the new rough cut skill (transcription + cache-aware render).

        ``JOB_ID`` is positional so the v2 skill commands share the same shape
        (``ve rough-cut ./raw.mp4 my-job``).
        """
        ctx = cli_main._context(job_id, work_root, output_root)
        skill = RoughCutSkill(ctx)
        locked = skill.run(source_path, transcript_path=transcript_path)
        typer.echo(f"Locked rough cut: {locked}")

    @app.command()
    def graphics(
        job_id: str = cli_main._JOB_ID_ARG,
        preset: str = cli_main._PRESET_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        presets_dir: Path | None = cli_main._PRESETS_DIR_OPT,
        transcript_path: Path | None = cli_main._TRANSCRIPT_GRAPHICS_OPT,
        qa_per_segment: bool = cli_main._QA_PER_SEGMENT_OPT,
        config_path: Path | None = cli_main._CONFIG_OPT,
        skip_hyperframes_lint: bool = cli_main._SKIP_HYPERFRAMES_LINT_OPT,
        transition: str = cli_main._TRANSITION_OPT,
        transition_duration: float = cli_main._TRANSITION_DURATION_OPT,
    ) -> None:
        """Apply graphics preset to the locked rough cut."""
        ctx = cli_main._context(job_id, work_root, output_root)
        effective_presets_dir = cli_main._resolve_presets_dir(presets_dir, config_path)
        render_options = RenderOptions()
        render_options.skip_hyperframes_lint = skip_hyperframes_lint
        render_options.transition = transition
        render_options.transition_duration = transition_duration
        skill = GraphicsSkill(
            ctx,
            presets_dir=effective_presets_dir,
            run_qa_per_segment=qa_per_segment,
            render_options=render_options,
        )
        output = skill.run(preset_name=preset, transcript_path=transcript_path)
        typer.echo(f"Graphics video: {output}")

    @app.command()
    def second_pass(
        job_id: str = cli_main._JOB_ID_ARG,
        segment: str = cli_main._SEGMENT_OPT,
        start: float = cli_main._START_OPT,
        end: float = cli_main._END_OPT,
        label: str = cli_main._LABEL_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        presets_dir: Path | None = cli_main._PRESETS_DIR_OPT,
        config_path: Path | None = cli_main._CONFIG_OPT,
    ) -> None:
        """Refine a single segment and re-render only what changed."""
        ctx = cli_main._context(job_id, work_root, output_root)
        effective_presets_dir = cli_main._resolve_presets_dir(presets_dir, config_path)
        graphics_skill = GraphicsSkill(ctx, presets_dir=effective_presets_dir)
        skill = SecondPassSkill(ctx, graphics_skill=graphics_skill)
        edit = SegmentEdit(
            segment_id=segment, new_source_start=start, new_source_end=end, new_label=label
        )
        output = skill.run(edit)
        typer.echo(f"Second pass output: {output}")

    @app.command()
    def captions(
        job_id: str = cli_main._JOB_ID_ARG,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        presets_dir: Path | None = cli_main._PRESETS_DIR_OPT,
        output_name: str = cli_main._OUTPUT_NAME_CAPTIONS_OPT,
        config_path: Path | None = cli_main._CONFIG_OPT,
    ) -> None:
        """Burn word-level animated captions onto the graphics composition."""
        ctx = cli_main._context(job_id, work_root, output_root)
        effective_presets_dir = cli_main._resolve_presets_dir(presets_dir, config_path)
        graphics_skill = GraphicsSkill(ctx, presets_dir=effective_presets_dir)
        skill = CaptionsSkill(ctx, graphics_skill=graphics_skill)
        output = skill.run(output_name=output_name)
        typer.echo(f"Captions video: {output}")

    @app.command()
    def music(
        job_id: str = cli_main._JOB_ID_ARG,
        music_path: Path | None = cli_main._MUSIC_PATH_OPT,
        output_name: str = cli_main._OUTPUT_NAME_MUSIC_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
    ) -> None:
        """Mix background music with speech ducking onto the latest video."""
        ctx = cli_main._context(job_id, work_root, output_root)
        skill = MusicSkill(ctx, music_path=music_path)
        output = skill.run(output_name=output_name)
        typer.echo(f"Music video: {output}")

    @app.command()
    def requirements(
        job_id: str = cli_main._JOB_ID_ARG,
        min_duration: float | None = cli_main._REQUIRE_MIN_DURATION_OPT,
        max_duration: float | None = cli_main._REQUIRE_MAX_DURATION_OPT,
        require_aspect: str = cli_main._REQUIRE_ASPECT_OPT,
        require_element: list[str] = cli_main._REQUIRE_ELEMENT_OPT,
        forbid_element: list[str] = cli_main._FORBID_ELEMENT_OPT,
        must_mention: list[str] = cli_main._MUST_MENTION_OPT,
        judge_check: list[str] = cli_main._JUDGE_CHECK_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
    ) -> None:
        """Capture requirements for a job so the verification gate can check them."""
        ctx = cli_main._context(job_id, work_root, output_root)
        target_duration = None
        if min_duration is not None or max_duration is not None:
            target_duration = DurationRange(min=min_duration, max=max_duration)
        spec = RequirementsSpec(
            job_id=job_id,
            target_duration_range=target_duration,
            aspect_ratio=require_aspect,
            required_elements=require_element,
            forbidden_elements=forbid_element,
            content_constraints=ContentConstraints(must_mention=must_mention),
            judge_checklist=judge_check,
        )
        skill = VerifySkill(ctx, requirements=spec)
        skill.save_requirements(spec)
        typer.echo(f"Requirements captured for {job_id} -> {skill._requirements_path()}")

    @app.command()
    def verify(
        job_id: str = cli_main._JOB_ID_ARG,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
    ) -> None:
        """Run the requirements verification gate for an already-exported job."""

        ctx = cli_main._context(job_id, work_root, output_root)
        skill = VerifySkill(ctx)
        try:
            report = asyncio.run(skill.run())
            typer.echo(f"Verification {report.overall} ({len(report.checks)} checks)")
        except RuntimeError as exc:
            typer.echo(f"Verification failed: {exc}", err=True)
            raise typer.Exit(1) from exc

    @app.command(name="list-templates")
    def list_templates() -> None:
        """List every supported video template with its required inputs."""
        router = cli_main.TemplateRouter()
        for entry in router.list_templates():
            req = ", ".join(entry["required_inputs"]) or "none"
            opt = ", ".join(entry["optional_inputs"]) or "none"
            typer.echo(
                f"{entry['name']}: {entry['description']}\n"
                f"  pipeline={entry['pipeline']} preset={entry['preset']} "
                f"aspect={entry['aspect_ratio']}\n"
                f"  required: {req}\n"
                f"  optional: {opt}"
            )

    @app.command(name="templates")
    def templates_command() -> None:
        """Alias for ``list-templates``."""
        list_templates()

    @app.command(name="brand")
    def brand_command(
        presets_dir: Path = cli_main._BRAND_PRESETS_DIR_OPT,
        job_id: str | None = cli_main._BRAND_JOB_ID_OPT,
        tenant_id: str | None = cli_main._BRAND_TENANT_ID_OPT,
    ) -> None:
        """Resolve and print the active brand profile for a presets directory."""
        from video_editor_state.brand_profile import (
            BrandResolutionError,
            resolve_brand_profile,
        )

        try:
            profile = resolve_brand_profile(presets_dir, tenant_id=tenant_id, job_id=job_id)
        except BrandResolutionError as exc:
            typer.echo(f"No brand profile found: {exc}", err=True)
            raise typer.Exit(1) from exc

        assert profile is not None  # resolve_brand_profile raises when missing
        typer.echo(f"brand: {profile.brand}")
        if profile.colors:
            typer.echo(f"colors: {profile.colors}")
        if profile.fonts:
            typer.echo(f"fonts: {profile.fonts}")
        if profile.logo:
            typer.echo(f"logo: {profile.logo}")

    @app.command()
    def probe(
        path: Path = cli_main._PATH_ARG,
    ) -> None:
        """Probe a media file with ffprobe."""
        info = probe_media(path)
        duration_str = f"{info.duration:.3f}s" if info.duration is not None else "unknown"
        typer.echo(f"Duration: {duration_str}, {info.width}x{info.height} @ {info.fps:.2f}fps")

    @app.command()
    def commands(
        as_json: bool = cli_main._COMMANDS_JSON_OPT,
    ) -> None:
        """List every registered engine command (id, category, summary).

        The same typed commands are dispatchable from the ``ve control`` JSON
        channel and the MCP ``run_command`` tool.
        """
        from video_editor_engine.commands import default_registry

        registered = default_registry.list_commands()
        if as_json:
            typer.echo(json.dumps([command.to_dict() for command in registered], indent=2))
            return
        for command in registered:
            typer.echo(f"{command.id} [{command.category}] {command.summary}")

    @app.command()
    def control(
        port: int = cli_main._CONTROL_PORT_OPT,
        host: str = cli_main._CONTROL_HOST_OPT,
    ) -> None:
        """Start the JSON-lines control server for the command registry.

        One JSON request per line, one JSON response per line. Methods:
        ``engine.ping``, ``engine.commands`` and
        ``engine.execute`` (``{"command": <id>, "params": {...}}``).
        """

        from video_editor_engine.commands.control import serve_control

        logger.info("control_server_starting", host=host, port=port)
        try:
            asyncio.run(serve_control(host=host, port=port))
        except KeyboardInterrupt:
            typer.echo("Control server stopped.")

    @app.command()
    def brief(
        job_id: str = cli_main._JOB_ID_ARG,
        from_md: Path = cli_main._FROM_MD_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
    ) -> None:
        """Install a ``project.md`` brief into a job's work directory.

        The brief is read, validated, and copied to
        ``<work_root>/<job_id>/project.md`` so subsequent skill invocations
        can pick it up via ``JobContext.load_brief_from_workdir()``. The
        source file is *not* modified — the brief is duplicated into the
        work tree so the worker process can find it without depending on
        the original path.
        """
        if not from_md.exists():
            typer.echo(f"Error: project.md not found: {from_md}", err=True)
            raise typer.Exit(1)
        # The parser is keyed on ``<dir>/project.md``; copy the user's
        # file to a tmp location next to a ``project.md`` shim so the
        # parser sees the canonical filename. This means ``ve brief``
        # accepts any ``*.md`` (not just files literally named
        # ``project.md``) and still benefits from validation.
        import tempfile

        from video_editor_state.project_brief import (
            load_project_brief,
            write_project_brief,
        )

        with tempfile.TemporaryDirectory() as parse_dir:
            parse_root = Path(parse_dir)
            canonical = parse_root / "project.md"
            shutil.copy2(from_md, canonical)
            parsed = load_project_brief(parse_root)
        if parsed is None:
            typer.echo(f"Error: could not parse project.md at {from_md}", err=True)
            raise typer.Exit(1)
        # Stamp the job id so subsequent loads know which job they belong to.
        parsed.job_id = job_id

        ctx = cli_main._context(job_id, work_root, output_root)
        target = write_project_brief(ctx.work_dir, parsed)
        typer.echo(f"Brief written to {target}")
