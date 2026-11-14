"""Real pipeline commands registered on the typed command registry.

Where :mod:`video_editor_engine.commands.builtins` exposes cheap introspection
helpers, this module exposes the actual stage commands an agent needs to drive
the engine end-to-end through ``ve commands`` / ``ve control`` / MCP
``run_command``:

* ``project.status`` — read a project's ledger row (jobs, costs, issues, artifacts),
* ``qa.run``         — run the QA skill against a rendered video,
* ``assets.resolve`` — resolve a scene plan's generative assets (TTS / image / ...),
* ``render.run``     — render a resolved scene plan to an MP4.

The handlers mirror the equivalent CLI commands. Every heavy dependency is
imported lazily *inside* the handler so that importing
:mod:`video_editor_engine.commands` (and listing the command catalog) stays
cheap and free of optional-dependency surprises.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from video_editor_config import Settings

from video_editor_engine.commands.registry import register_command

if TYPE_CHECKING:
    from video_editor_state.job import JobContext

__all__ = [
    "AssetsResolveParams",
    "ProjectStatusParams",
    "QaRunParams",
    "RenderRunParams",
]

_DEFAULT_WORK_ROOT = "projects"
_DEFAULT_OUTPUT_ROOT = "outputs"


def _build_context(
    job_id: str,
    work_root: str,
    output_root: str,
    config_path: str | None,
) -> JobContext:
    """Build a :class:`JobContext` the same way the CLI ``_context`` helper does.

    Resolves the ledger from ``config_path`` (falling back to a no-op ledger)
    and ensures the work/output directories exist.
    """
    from video_editor_state.job import JobContext

    ledger = JobContext._resolve_ledger(config_path)
    ctx = JobContext(
        job_id=job_id,
        work_root=work_root,
        output_root=output_root,
        ledger=ledger,
    )
    ctx.ensure_dirs()
    return ctx


# ---------------------------------------------------------------------------
# project.status
# ---------------------------------------------------------------------------


class ProjectStatusParams(BaseModel):
    """Inputs for ``project.status``."""

    job_id: str = Field(min_length=1, description="Project id (usually the job_id).")
    work_root: str = Field(
        default=_DEFAULT_WORK_ROOT,
        description="Root directory that holds per-job work directories.",
    )
    output_root: str = Field(
        default=_DEFAULT_OUTPUT_ROOT,
        description="Root directory that holds per-job output directories.",
    )
    config_path: str | None = Field(
        default=None,
        description=(
            "Path to config.yaml. Controls the project ledger and provider "
            "selection. When omitted the default config resolution applies."
        ),
    )


@register_command(
    id="project.status",
    summary="Read a project's ledger status: jobs, costs, issues and artifacts.",
    category="project",
    params_model=ProjectStatusParams,
)
def _project_status(params: ProjectStatusParams) -> dict[str, Any]:
    from video_editor_state.job import JobContext

    # ``_resolve_ledger`` returns a NullLedger when the ledger is disabled;
    # ``get_project`` then returns None rather than raising.
    ledger = JobContext._resolve_ledger(params.config_path)
    project = ledger.get_project(params.job_id)
    return {
        "found": project is not None,
        "project_id": params.job_id,
        "work_root": params.work_root,
        "output_root": params.output_root,
        "project": project,
    }


# ---------------------------------------------------------------------------
# qa.run
# ---------------------------------------------------------------------------


class QaRunParams(BaseModel):
    """Inputs for ``qa.run``."""

    job_id: str = Field(min_length=1, description="Job id whose artifacts QA should read.")
    work_root: str = Field(
        default=_DEFAULT_WORK_ROOT,
        description="Root directory that holds per-job work directories.",
    )
    output_root: str = Field(
        default=_DEFAULT_OUTPUT_ROOT,
        description="Root directory that holds per-job output directories.",
    )
    expected_aspect_ratio: str = Field(
        default="16:9",
        description="Aspect ratio the render is expected to have, e.g. '16:9' or '9:16'.",
    )
    video_path: str | None = Field(
        default=None,
        description=(
            "Explicit video to check. When omitted QA falls back to the job's "
            "b-roll / captioned artifact."
        ),
    )


@register_command(
    id="qa.run",
    summary="Run the automated QA gate on a rendered video and return the report.",
    category="qa",
    params_model=QaRunParams,
    mutates=True,
)
def _qa_run(params: QaRunParams) -> dict[str, Any]:
    from qa.qa import QASkill

    ctx = _build_context(params.job_id, params.work_root, params.output_root, config_path=None)
    skill = QASkill(ctx, expected_aspect_ratio=params.expected_aspect_ratio)
    report = skill.run(params.video_path)
    return report.model_dump(mode="json")


# ---------------------------------------------------------------------------
# assets.resolve
# ---------------------------------------------------------------------------


class AssetsResolveParams(BaseModel):
    """Inputs for ``assets.resolve``."""

    job_id: str = Field(min_length=1, description="Job id the assets belong to.")
    tts_provider: str = Field(
        default="stub_tts", description="Provider used for narration TTS assets."
    )
    image_provider: str = Field(
        default="auto",
        description=(
            "Provider used for per-scene image assets. 'auto' walks the "
            "configured assets.image_chain; a named provider bypasses it."
        ),
    )
    video_provider: str = Field(
        default="",
        description=(
            "When non-empty, scenes that require motion also get a VIDEO request. "
            "Empty disables video generation."
        ),
    )
    data_viz_provider: str = Field(
        default="matplotlib_data_viz",
        description="Provider used for ``data:`` chart assets.",
    )
    plan_name: str = Field(
        default="scene_plan.json",
        description="Scene-plan filename under ``<work_dir>/plan/`` to read.",
    )
    plan_out_name: str = Field(
        default="scene_plan_with_assets.json",
        description="Scene-plan filename under ``<work_dir>/plan/`` to write.",
    )
    work_root: str = Field(
        default=_DEFAULT_WORK_ROOT,
        description="Root directory that holds per-job work directories.",
    )
    output_root: str = Field(
        default=_DEFAULT_OUTPUT_ROOT,
        description="Root directory that holds per-job output directories.",
    )
    config_path: str | None = Field(
        default=None,
        description=(
            "Path to config.yaml. Controls the project ledger and provider "
            "selection. When omitted the default config resolution applies."
        ),
    )


@register_command(
    id="assets.resolve",
    summary="Resolve a scene plan's generative assets and write the enriched plan.",
    category="assets",
    params_model=AssetsResolveParams,
    mutates=True,
    spends=True,
)
async def _assets_resolve(params: AssetsResolveParams) -> dict[str, Any]:
    from video_editor_asset_ingestion.manager import AssetIngestionManager
    from video_editor_asset_ingestion.scene_assets import (
        attach_results_to_plan,
        requests_from_plan,
    )
    from video_editor_core.models.scene_plan import ScenePlan

    ctx = _build_context(params.job_id, params.work_root, params.output_root, params.config_path)
    config = Settings.load(params.config_path).config.assets
    manager = AssetIngestionManager(ctx, config=config)
    # Ensure the project ledger has a row so asset cost lines are grouped
    # under a real project (mirrors the CLI ``ve assets`` handler).
    ctx.ledger.upsert_project(params.job_id, status="planned")

    plan_path = ctx.work_dir / "plan" / params.plan_name
    if not plan_path.exists():
        raise FileNotFoundError(f"Scene plan not found: {plan_path}")

    plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    requests = requests_from_plan(
        plan,
        params.job_id,
        tts_provider=params.tts_provider,
        image_provider=params.image_provider,
        data_viz_provider=params.data_viz_provider,
        video_provider=params.video_provider,
    )

    # ``resolve_partial`` (not ``resolve``) so a single failing asset does not
    # discard the successfully generated ones — the caller gets both lists.
    batch = await manager.resolve_partial(requests)
    attach_results_to_plan(plan, batch.results)

    plan_out_path = ctx.work_dir / "plan" / params.plan_out_name
    plan_out_path.parent.mkdir(parents=True, exist_ok=True)
    plan_out_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    return {
        "resolved": len(batch.results),
        "failed": [asset_id for asset_id, _ in batch.failures],
        "plan_out": str(plan_out_path),
    }


# ---------------------------------------------------------------------------
# render.run
# ---------------------------------------------------------------------------


class RenderRunParams(BaseModel):
    """Inputs for ``render.run``."""

    job_id: str = Field(min_length=1, description="Job id whose plan/render dirs to use.")
    plan_name: str = Field(
        default="scene_plan_with_assets.json",
        description="Resolved scene-plan filename under ``<work_dir>/plan/`` to render.",
    )
    output_name: str = Field(
        default="final.mp4", description="Output filename under ``<output_dir>/``."
    )
    render_engine: str = Field(
        default="auto",
        description=(
            "Requested render engine. Scene plans render through HyperFrames, so "
            "only 'auto' and 'hyperframes' are honoured; other values warn and "
            "fall back to HyperFrames."
        ),
    )
    work_root: str = Field(
        default=_DEFAULT_WORK_ROOT,
        description="Root directory that holds per-job work directories.",
    )
    output_root: str = Field(
        default=_DEFAULT_OUTPUT_ROOT,
        description="Root directory that holds per-job output directories.",
    )
    config_path: str | None = Field(
        default=None,
        description=(
            "Path to config.yaml. Controls the project ledger and provider "
            "selection. When omitted the default config resolution applies."
        ),
    )


def _probe_duration(output_path: Path) -> tuple[float | None, str | None]:
    """Return the measured duration and an error, never the plan's guess (#310).

    A render that cannot be probed is reported as ``duration_seconds: null``
    with a ``duration_error``. Substituting the plan's scene total made a
    broken render look plausible.
    """
    try:
        from video_editor_ffmpeg.probe import probe_media

        info = probe_media(str(output_path))
    except Exception as exc:  # noqa: BLE001 - reported as a typed field
        return None, f"probe failed: {exc}"
    if info.duration is None:
        return None, "probe returned no duration"
    return float(info.duration), None


@register_command(
    id="render.run",
    summary="Render a resolved scene plan to an MP4 via HyperFrames.",
    category="render",
    params_model=RenderRunParams,
    mutates=True,
)
def _render_run(params: RenderRunParams) -> dict[str, Any]:
    from video_editor_core.scene_plan_composition import render_scene_plan

    ctx = _build_context(params.job_id, params.work_root, params.output_root, params.config_path)
    plan_path = ctx.work_dir / "plan" / params.plan_name
    if not plan_path.exists():
        raise FileNotFoundError(f"Scene plan not found: {plan_path}")

    if params.render_engine not in ("auto", "hyperframes"):
        import structlog

        structlog.get_logger(__name__).warning(
            "render_engine_ignored",
            requested=params.render_engine,
            using="hyperframes",
        )

    output_path = ctx.output_dir / params.output_name
    render_scene_plan(plan_path, output_path, ctx.work_dir / "hyperframes")

    duration, duration_error = _probe_duration(output_path)
    result: dict[str, Any] = {
        "output": str(output_path),
        "duration_seconds": duration,
        "render_engine": "hyperframes",
    }
    if duration_error is not None:
        result["duration_error"] = duration_error
    return result
