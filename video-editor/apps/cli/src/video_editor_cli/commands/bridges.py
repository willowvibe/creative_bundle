"""CLI command group."""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
import typer
from video_editor_config import ProjectConfig
from video_editor_core.models.scene_plan import ScenePlan
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_pipelines.handlers import _safe_filename

from video_editor_cli import main as cli_main
from video_editor_cli.bridge_loader import COMFYSTUDIO, FABLECUT, bridge

logger = structlog.get_logger(__name__)


def register_bridges(app: typer.Typer) -> None:
    """Register the bridges commands."""

    @app.command(name="export-comfystudio")
    def export_comfystudio(
        job_id: str = cli_main._JOB_ID_ARG,
        project_dir: Path = cli_main._COMFYSTUDIO_PROJECT_DIR_OPT,
        plan_name: str = cli_main._ASSETS_PLAN_NAME_OPT,
        fps: int = cli_main._COMFYSTUDIO_FPS_OPT,
        width: int = cli_main._COMFYSTUDIO_WIDTH_OPT,
        height: int = cli_main._COMFYSTUDIO_HEIGHT_OPT,
        music_path: Path | None = cli_main._MUSIC_PATH_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
    ) -> None:
        """Export a resolved scene plan to a ComfyStudio project folder.

        Writes ``project.comfystudio`` plus copied assets into ``--project-dir``
        so a user can open the timeline in ComfyStudio, drag clips, adjust
        timing, and re-import the edited plan with ``ve import-comfystudio``.
        """
        ctx = cli_main._context(job_id, work_root, output_root)
        plan_path = ctx.work_dir / "plan" / plan_name
        if not plan_path.exists():
            typer.echo(f"Scene plan not found: {plan_path}", err=True)
            raise typer.Exit(1)
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        bridge(f"{COMFYSTUDIO}.exporter", "export_to_comfystudio")(
            scene_plan=plan,
            project_root=project_dir.expanduser(),
            fps=fps,
            width=width,
            height=height,
            music_path=music_path.expanduser() if music_path else None,
        )
        typer.echo(f"Exported ComfyStudio project -> {project_dir}")

    @app.command(name="import-comfystudio")
    def import_comfystudio(
        job_id: str = cli_main._JOB_ID_ARG,
        project_dir: Path = cli_main._COMFYSTUDIO_PROJECT_DIR_OPT,
        plan_name: str = cli_main._ASSETS_PLAN_NAME_OPT,
        plan_out_name: str = cli_main._ASSETS_PLAN_OUT_NAME_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
    ) -> None:
        """Import an edited ComfyStudio project back into a scene plan.

        Reads ``<project-dir>/project.comfystudio`` and writes an updated
        ``ScenePlan`` to ``<work>/plan/<plan-out-name>`` so the engine can
        re-render with the user's timeline changes.
        """
        ctx = cli_main._context(job_id, work_root, output_root)
        plan_path = ctx.work_dir / "plan" / plan_name
        if not plan_path.exists():
            typer.echo(f"Original scene plan not found: {plan_path}", err=True)
            raise typer.Exit(1)
        original_plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        project_file = project_dir.expanduser() / "project.comfystudio"
        updated_plan = bridge(f"{COMFYSTUDIO}.importer", "import_from_comfystudio")(
            project_file, original_plan
        )
        plan_out_path = ctx.work_dir / "plan" / plan_out_name
        plan_out_path.parent.mkdir(parents=True, exist_ok=True)
        plan_out_path.write_text(updated_plan.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"Imported edited scene plan -> {plan_out_path}")

    @app.command(name="export-fablecut")
    def export_fablecut(
        job_id: str = cli_main._JOB_ID_ARG,
        project_dir: Path = cli_main._FABLECUT_PROJECT_DIR_OPT,
        plan_name: str = cli_main._ASSETS_PLAN_NAME_OPT,
        fps: int = cli_main._FABLECUT_FPS_OPT,
        width: int = cli_main._FABLECUT_WIDTH_OPT,
        height: int = cli_main._FABLECUT_HEIGHT_OPT,
        music_path: Path | None = cli_main._MUSIC_PATH_OPT,
        lyrics: Path | None = cli_main._LYRICS_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
    ) -> None:
        """Export a resolved scene plan to a FableCut project folder.

        Writes ``project.json`` plus copied assets into ``--project-dir``
        so a user can open the timeline in FableCut, drag clips, adjust
        timing, and re-import the edited plan with ``ve import-fablecut``.

        Optional ``--lyrics-path`` creates bottom pop-up caption clips on
        track V2, with one lyric line mapped to each scene.
        """
        ctx = cli_main._context(job_id, work_root, output_root)
        plan_path = ctx.work_dir / "plan" / plan_name
        if not plan_path.exists():
            typer.echo(f"Scene plan not found: {plan_path}", err=True)
            raise typer.Exit(1)
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        bridge(f"{FABLECUT}.exporter", "export_to_fablecut")(
            scene_plan=plan,
            project_root=project_dir.expanduser(),
            fps=fps,
            width=width,
            height=height,
            music_path=music_path.expanduser() if music_path else None,
            lyrics_path=lyrics.expanduser() if lyrics else None,
        )
        typer.echo(f"Exported FableCut project -> {project_dir}")

    @app.command(name="import-fablecut")
    def import_fablecut(
        job_id: str = cli_main._JOB_ID_ARG,
        project_dir: Path = cli_main._FABLECUT_PROJECT_DIR_OPT,
        plan_name: str = cli_main._ASSETS_PLAN_NAME_OPT,
        plan_out_name: str = cli_main._ASSETS_PLAN_OUT_NAME_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
    ) -> None:
        """Import an edited FableCut project back into a scene plan.

        Reads ``<project-dir>/project.json`` and writes an updated
        ``ScenePlan`` to ``<work>/plan/<plan-out-name>`` so the engine can
        re-render with the user's timeline changes.
        """
        ctx = cli_main._context(job_id, work_root, output_root)
        plan_path = ctx.work_dir / "plan" / plan_name
        if not plan_path.exists():
            typer.echo(f"Original scene plan not found: {plan_path}", err=True)
            raise typer.Exit(1)
        original_plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        project_file = project_dir.expanduser() / "project.json"
        updated_plan = bridge(f"{FABLECUT}.importer", "import_from_fablecut")(
            project_file, original_plan
        )
        plan_out_path = ctx.work_dir / "plan" / plan_out_name
        plan_out_path.parent.mkdir(parents=True, exist_ok=True)
        plan_out_path.write_text(updated_plan.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"Imported edited scene plan -> {plan_out_path}")

    @app.command(name="export-otio")
    def export_otio_cmd(
        job_id: str = cli_main._JOB_ID_ARG,
        project_dir: Path = cli_main._FABLECUT_PROJECT_DIR_OPT,
        otio_path: Path | None = cli_main._OTIO_PATH_OPT,
        plan_name: str = cli_main._ASSETS_PLAN_NAME_OPT,
        fps: int = cli_main._FABLECUT_FPS_OPT,
        width: int = cli_main._FABLECUT_WIDTH_OPT,
        height: int = cli_main._FABLECUT_HEIGHT_OPT,
        music_path: Path | None = cli_main._MUSIC_PATH_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
    ) -> None:
        """Export a job's timeline as OpenTimelineIO (.otio).

        Builds the FableCut project (copying media into ``--project-dir``) and writes
        an OTIO timeline with **absolute** media references, so it opens in any
        OTIO-capable editor — FilmCraft, DaVinci Resolve, or Premiere via an adapter.
        Pass ``--otio-path`` to also write a copy to a chosen location.
        """
        ctx = cli_main._context(job_id, work_root, output_root)
        plan_path = ctx.work_dir / "plan" / plan_name
        if not plan_path.exists():
            typer.echo(f"Scene plan not found: {plan_path}", err=True)
            raise typer.Exit(1)
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        project_root = project_dir.expanduser()
        project = bridge(f"{FABLECUT}.exporter", "build_fablecut_project")(
            scene_plan=plan,
            project_root=project_root,
            fps=fps,
            width=width,
            height=height,
            music_path=music_path.expanduser() if music_path else None,
        )
        primary = bridge(f"{FABLECUT}.otio", "export_otio")(
            project, project_root / "project.otio", media_root=project_root
        )
        typer.echo(f"Exported OTIO timeline -> {primary}")
        if otio_path is not None:
            extra = bridge(f"{FABLECUT}.otio", "export_otio")(
                project, otio_path.expanduser(), media_root=project_root
            )
            typer.echo(f"Also wrote -> {extra}")

    @app.command(name="import-otio")
    def import_otio_cmd(
        job_id: str = cli_main._JOB_ID_ARG,
        otio_path: Path = typer.Option(
            ..., "--otio-path", help="OpenTimelineIO (.otio) file edited in another editor."
        ),
        plan_name: str = cli_main._ASSETS_PLAN_NAME_OPT,
        plan_out_name: str = cli_main._ASSETS_PLAN_OUT_NAME_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
    ) -> None:
        """Import an edited ``.otio`` timeline back into the job's scene plan.

        Reads the OTIO (e.g. exported from FilmCraft/Resolve), reconciles clip
        durations onto the original scene order, and writes an updated ``ScenePlan``
        to ``<work>/plan/<plan-out-name>`` for re-rendering.
        """
        ctx = cli_main._context(job_id, work_root, output_root)
        plan_path = ctx.work_dir / "plan" / plan_name
        if not plan_path.exists():
            typer.echo(f"Original scene plan not found: {plan_path}", err=True)
            raise typer.Exit(1)
        original_plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        updated_plan = bridge(f"{FABLECUT}.importer", "import_otio_plan")(
            otio_path.expanduser(), original_plan
        )
        out_path = ctx.work_dir / "plan" / plan_out_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(updated_plan.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"Imported edited scene plan -> {out_path}")

    @app.command(name="render-fablecut")
    def render_fablecut(
        job_id: str = cli_main._JOB_ID_ARG,
        project_dir: Path = cli_main._RENDER_FABLECUT_PROJECT_DIR_OPT,
        output_name: str = cli_main._RENDER_FABLECUT_OUTPUT_NAME_OPT,
        font: str = cli_main._RENDER_FABLECUT_FONT_OPT,
        fps: int | None = cli_main._RENDER_FABLECUT_FPS_OPT,
        width: int | None = cli_main._RENDER_FABLECUT_WIDTH_OPT,
        height: int | None = cli_main._RENDER_FABLECUT_HEIGHT_OPT,
        config_path: Path | None = cli_main._CONFIG_OPT,
        review: bool = cli_main._REVIEW_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
    ) -> None:
        """Render the final MP4 directly from an edited FableCut project.

        Treats ``<project-dir>/project.json`` as the single source of truth, so
        every browser edit (caption timing/position, image transforms/keyframes,
        clip speed, audio volume) survives into the deliverable. Writes the final
        video to ``<output>/output/<output-name>``.
        """

        ctx = cli_main._context(job_id, work_root, output_root)
        project_config = ProjectConfig.load(config_path)
        render_options = RenderOptions.from_mapping(
            project_config.render.model_dump(
                include={
                    "video_codec",
                    "audio_codec",
                    "video_bitrate",
                    "audio_bitrate",
                    "fps",
                    "crf",
                    "width",
                    "height",
                }
            )
        )
        if width is not None:
            render_options.width = width
        if height is not None:
            render_options.height = height
        if fps is not None:
            render_options.fps = fps

        output_path = ctx.output_dir / _safe_filename(output_name, "output_name")
        bridge(f"{FABLECUT}.renderer", "render_fablecut_project")(
            project_path=project_dir.expanduser() / "project.json",
            output_path=output_path,
            media_dir=project_dir.expanduser() / "media",
            render_options=render_options,
            font_path=font,
        )
        typer.echo(f"Rendered FableCut project -> {output_path}")

        if review:
            review_skill = cli_main.PostRenderReviewSkill(ctx)
            review_report = review_skill.run(output_path)
            typer.echo(
                f"Post-render review: {review_report.overall} ({len(review_report.checks)} checks)"
            )

    @app.command(name="storyteller-export")
    def storyteller_export(
        job_id: str = cli_main._JOB_ID_ARG,
        project_dir: Path | None = cli_main._STORYTELLER_PROJECT_DIR_OPT,
    ) -> None:
        """Re-export a job's scene_plan.json to a FableCut project for browser preview."""

        target = project_dir if project_dir is not None else Path(f"projects/{job_id}/fablecut")
        plan_path = target.parent / "scene_plan.json"
        if not plan_path.exists():
            plan_path = Path(f"projects/{job_id}/scene_plan.json")
        if not plan_path.exists():
            typer.echo(f"scene_plan.json not found for job {job_id!r}", err=True)
            raise typer.Exit(1)
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        asyncio.run(
            cli_main.storyteller_to_fablecut(plan, target, aspect_ratio=plan.aspect_ratio or "16:9")
        )
        typer.echo(f"FableCut project written to {target}")
