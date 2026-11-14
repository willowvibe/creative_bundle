"""ve preview: export to FableCut, open browser, interactive edit loop."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import webbrowser
from pathlib import Path

import httpx
import structlog
import typer
from video_editor_core.models.scene_plan import ScenePlan
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_state.job import JobContext

from video_editor_cli.bridge_loader import FABLECUT, bridge
from video_editor_cli.paths import default_output_root, default_work_root

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[5]
FABLECUT_URL = "http://localhost:7777"
FABLECUT_DEFAULT_DIR = REPO_ROOT / "tools" / "FableCut"


def _find_fablecut_dir() -> Path:
    """Resolve the FableCut installation directory."""
    env_path = os.getenv("FABLECUT_PATH")
    if env_path:
        candidate = Path(env_path)
        if candidate.is_dir():
            return candidate

    if FABLECUT_DEFAULT_DIR.is_dir():
        return FABLECUT_DEFAULT_DIR

    typer.echo(
        "FableCut not found. Clone it to tools/FableCut/ or set FABLECUT_PATH.",
        err=True,
    )
    raise typer.Exit(1)


def _is_fablecut_running() -> bool:
    """Check if FableCut server is already running on localhost:7777."""
    try:
        response = httpx.get(FABLECUT_URL, timeout=2.0)
        return response.status_code == 200
    except Exception:  # noqa: BLE001 - a failed request must not kill the preview server
        return False


def _start_fablecut_server(fablecut_dir: Path) -> subprocess.Popen[bytes] | None:
    """Start the FableCut Node.js server as a background process."""
    if _is_fablecut_running():
        logger.info("fablecut_already_running")
        return None

    server_js = fablecut_dir / "server.js"
    if not server_js.exists():
        typer.echo(f"server.js not found in {fablecut_dir}", err=True)
        raise typer.Exit(1)

    logger.info("fablecut_starting", dir=str(fablecut_dir))
    proc = subprocess.Popen(
        ["node", "server.js"],
        cwd=str(fablecut_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    pid_file = fablecut_dir / ".fablecut_server.pid"
    pid_file.write_text(str(proc.pid))

    for _ in range(30):
        time.sleep(0.5)
        if _is_fablecut_running():
            logger.info("fablecut_ready")
            return proc

    typer.echo("FableCut server did not start within 15 seconds.", err=True)
    raise typer.Exit(1)


def _stop_fablecut_server(proc: subprocess.Popen[bytes] | None, fablecut_dir: Path) -> None:
    """Stop the FableCut server and clean up the PID file."""
    if proc is not None:
        pid_file = fablecut_dir / ".fablecut_server.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
            except (ValueError, ProcessLookupError, OSError):
                pass
            pid_file.unlink(missing_ok=True)

        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()


def _export_project(
    plan: ScenePlan, project_dir: Path, fps: int = 30, width: int = 1920, height: int = 1080
) -> Path:
    """Export a ScenePlan to a FableCut project directory."""
    exported = bridge(f"{FABLECUT}.exporter", "export_to_fablecut")(
        scene_plan=plan,
        project_root=project_dir,
        fps=fps,
        width=width,
        height=height,
    )
    return Path(exported)


def _sync_to_editor(source_dir: Path, fablecut_dir: Path) -> None:
    """Copy the exported project into the FableCut editor directory."""
    import shutil

    src_json = source_dir / "project.json"
    src_media = source_dir / "media"

    dst_json = fablecut_dir / "project.json"
    dst_media = fablecut_dir / "media"

    backup_json = fablecut_dir / "project.json.ve_backup"
    backup_media = fablecut_dir / "media.ve_backup"
    if dst_json.exists() and not backup_json.exists():
        shutil.copy2(dst_json, backup_json)
    if dst_media.exists() and not backup_media.exists():
        shutil.copytree(dst_media, backup_media)

    shutil.copy2(src_json, dst_json)
    if dst_media.exists():
        shutil.rmtree(dst_media)
    if src_media.exists():
        shutil.copytree(src_media, dst_media)

    state = {"source_project_dir": str(source_dir)}
    (fablecut_dir / ".fablecut_state.json").write_text(json.dumps(state))


def _run_edit_loop(ctx: JobContext, project_dir: Path, fablecut_dir: Path) -> int:
    """Run the interactive edit loop. Returns the number of edit iterations."""
    edit_count = 0

    typer.echo("\n" + "=" * 60)
    typer.echo("FableCut editor is open at http://localhost:7777")
    typer.echo("Edit the timeline, then type a command.")
    typer.echo("=" * 60)
    typer.echo("\nCommands:")
    typer.echo("  done, finalize  — finish editing and proceed to final export")
    typer.echo("  status          — show current state")
    typer.echo("  help            — show this help")
    typer.echo()

    while True:
        edit_count += 1
        try:
            cmd = typer.prompt("fablecut", default="", show_default=False).strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo("\nExiting preview loop.")
            break

        if not cmd:
            continue

        if cmd in ("done", "finalize"):
            typer.echo("Importing edited timeline and rendering final video...")

            edited_json = fablecut_dir / "project.json"
            if edited_json.exists():
                plan_path = ctx.work_dir / "plan" / "scene_plan_with_assets.json"
                if plan_path.exists():
                    original_plan = ScenePlan.model_validate_json(
                        plan_path.read_text(encoding="utf-8")
                    )
                    try:
                        updated_plan = bridge(f"{FABLECUT}.importer", "import_from_fablecut")(
                            edited_json, original_plan
                        )
                        edited_plan_path = ctx.work_dir / "plan" / "scene_plan_edited.json"
                        edited_plan_path.write_text(
                            updated_plan.model_dump_json(indent=2), encoding="utf-8"
                        )
                        typer.echo(f"Edited plan saved to {edited_plan_path}")
                    except Exception as exc:  # noqa: BLE001 - a failed request must not kill the preview server
                        logger.warning("fablecut_import_failed", error=str(exc))
                        typer.echo(f"Warning: could not import edits: {exc}")

            output_path = ctx.output_dir / "final.mp4"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                bridge(f"{FABLECUT}.renderer", "render_fablecut_project")(
                    project_path=edited_json,
                    output_path=output_path,
                    media_dir=fablecut_dir / "media",
                    render_options=RenderOptions(),
                )
                typer.echo(f"Final render: {output_path}")
            except Exception as exc:  # noqa: BLE001 - a failed request must not kill the preview server
                logger.warning("fablecut_render_failed", error=str(exc))
                typer.echo(f"Warning: FableCut render failed: {exc}")
                typer.echo("Falling back to HyperFrames render of the edited plan.")
                from video_editor_core.scene_plan_composition import (
                    render_scene_plan as render_scene_plan_hf,
                )

                edited_plan_path = ctx.work_dir / "plan" / "scene_plan_edited.json"
                if edited_plan_path.exists():
                    render_scene_plan_hf(
                        edited_plan_path, output_path, ctx.work_dir / "hyperframes"
                    )

            manifest = ctx.manifest()
            manifest.stage_done(
                "preview",
                {
                    "fablecut_project": str(project_dir),
                    "edited_plan": str(ctx.work_dir / "plan" / "scene_plan_edited.json"),
                    "final_video": str(output_path),
                    "edit_count": edit_count,
                },
            )
            ctx.store.save()

            typer.echo("Preview complete. Run `ve finalize` to export the final deliverable.")
            break

        elif cmd == "status":
            typer.echo(f"Job: {ctx.job_id}")
            typer.echo(f"Work dir: {ctx.work_dir}")
            typer.echo(f"FableCut: {FABLECUT_URL}")
            typer.echo(f"Edit iterations: {edit_count}")

        elif cmd == "help":
            typer.echo("Commands: done, finalize, status, help")

        else:
            typer.echo(f"Unknown command: {cmd!r}. Type 'help' for available commands.")

    return edit_count


def preview_command(
    job_id: str,
    work_root: str | None = None,
    output_root: str | None = None,
    config_path: Path | None = None,  # reserved for future use (provider config overrides)
    project_dir: Path | None = None,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
) -> None:
    """Export to FableCut, open browser, and run the interactive edit loop."""
    ctx = JobContext(
        job_id=job_id,
        work_root=work_root or default_work_root(),
        output_root=output_root or default_output_root(),
    )
    ctx.ensure_dirs()

    plan_path = ctx.work_dir / "plan" / "scene_plan_with_assets.json"
    if not plan_path.exists():
        typer.echo(f"Scene plan not found: {plan_path}", err=True)
        typer.echo("Run ve assets first.", err=True)
        raise typer.Exit(1)

    plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))

    if project_dir is None:
        project_dir = ctx.work_dir / "fablecut"

    fablecut_dir = _find_fablecut_dir()

    typer.echo(f"Exporting to FableCut: {project_dir}")
    _export_project(plan, project_dir, fps=fps, width=width, height=height)

    _sync_to_editor(project_dir, fablecut_dir)

    proc = _start_fablecut_server(fablecut_dir)

    try:
        webbrowser.open(FABLECUT_URL)
    except Exception:  # noqa: BLE001 - a failed request must not kill the preview server
        typer.echo(f"Could not open browser. Navigate to {FABLECUT_URL}")

    try:
        _run_edit_loop(ctx, project_dir, fablecut_dir)
    finally:
        _stop_fablecut_server(proc, fablecut_dir)


def register_preview(app: typer.Typer) -> None:
    """Register the preview command on the Typer app."""

    @app.command(name="preview")
    def preview(
        job_id: str = typer.Argument(..., help="Job identifier"),
        work_root: str | None = typer.Option(
            None,
            "--work-root",
            help="Work directory root (default: VE_WORK_ROOT, else /work or ./projects)",
        ),
        output_root: str | None = typer.Option(
            None,
            "--output-root",
            help="Output directory root (default: VE_OUTPUT_ROOT, else /output or ./outputs)",
        ),
        config_path: Path | None = typer.Option(
            None, "--config", "-c", help="Path to project config.yaml"
        ),
        project_dir: Path | None = typer.Option(
            None, "--project-dir", "-d", help="FableCut project directory"
        ),
        fps: int = typer.Option(30, "--fps", help="Timeline FPS"),
        width: int = typer.Option(1920, "--width", help="Timeline width"),
        height: int = typer.Option(1080, "--height", help="Timeline height"),
    ) -> None:
        """Export to FableCut web editor for manual timeline editing."""
        preview_command(
            job_id=job_id,
            work_root=work_root,
            output_root=output_root,
            config_path=config_path,
            project_dir=project_dir,
            fps=fps,
            width=width,
            height=height,
        )
